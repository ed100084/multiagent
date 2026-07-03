#!/usr/bin/env python3
"""Engine-agnostic agent dispatcher.

Usage: dispatcher.py <role> <task_file> [--workdir DIR] [--config pipeline.yaml]

Resolves role -> engine chain from pipeline.yaml, runs the adapter,
fails over on error, records which engine ran (audit + cross-check).
Gate roles (see GATE_VERDICTS) must emit a machine-parseable verdict line;
output without one is treated as engine failure and fails over.
Exit code: 0 = success, 1 = all engines failed, 2 = config error,
3 = gate role returned "verdict: fail" (engine ran fine; no failover).
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

import yaml

STATE_FILE = "pipeline/state/engines.json"
LOG_DIR = "pipeline/logs"

# gate roles: stdout must contain exactly one of these verdict lines
# (keep in sync with the 輸出格式 section of roles/<role>.md)
GATE_VERDICTS = {"reviewer": ("pass", "fail")}


def die(msg: str, code: int = 2):
    print(f"[dispatcher] FATAL: {msg}", file=sys.stderr)
    sys.exit(code)


def find_config(start: pathlib.Path, name: str) -> pathlib.Path:
    for d in [start, *start.parents]:
        p = d / name
        if p.is_file():
            return p
    die(f"{name} not found from {start} upward")


def load_state(root: pathlib.Path) -> dict:
    p = root / STATE_FILE
    if p.is_file():
        return json.loads(p.read_text())
    return {}


def save_state(root: pathlib.Path, state: dict):
    p = root / STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def engine_family(engines: dict, name: str) -> str:
    """Engines backed by the same model (e.g. codex CLI vs codex-via-proxy)
    share a 'family'; cross-check bans by family, not by engine name."""
    return (engines.get(name) or {}).get("family", name)


def engine_supports_mode(engine_cfg: dict, mode: str) -> bool:
    """Return whether an engine can run the requested access mode.

    Existing configs did not declare capabilities, so keep a conservative
    built-in rule for the bundled text-only proxy adapter and otherwise allow
    custom adapters unless they opt into an explicit 'modes' list.
    """
    if "modes" in engine_cfg:
        return mode in set(engine_cfg.get("modes") or [])
    adapter = str(engine_cfg.get("adapter", ""))
    if adapter.endswith("proxyapi.sh") and mode == "rw":
        return False
    return True


def health_ok(engine_cfg: dict) -> bool:
    cmd = engine_cfg.get("health")
    if not cmd:
        return True  # no health cmd defined -> assume alive
    # engines with slow cold starts (e.g. GB10 model load) override the default
    timeout = int(engine_cfg.get("health_timeout", 60))
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def parse_verdict(out: str, allowed: tuple):
    """Exact-line match like council.parse_verdict; None if the verdict line
    is missing or conflicting (e.g. template copied verbatim)."""
    found = {v for line in out.splitlines() for v in allowed
             if line.strip() == f"verdict: {v}"}
    return found.pop() if len(found) == 1 else None


def run_engine(adapter: str, role_prompt: pathlib.Path, task: pathlib.Path,
               workdir: str, mode: str, timeout: int, log: pathlib.Path,
               env: dict):
    """Returns stdout on success (exit 0), None on engine failure."""
    try:
        r = subprocess.run(
            [adapter, str(role_prompt), str(task), workdir, mode],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        log.write_text("TIMEOUT\n")
        print(f"[dispatcher] engine timed out after {timeout}s", file=sys.stderr)
        return None
    log.write_text(
        f"exit={r.returncode}\n--- STDOUT ---\n{r.stdout}\n--- STDERR ---\n{r.stderr}\n"
    )
    if r.returncode != 0:
        print(f"[dispatcher] engine failed (exit {r.returncode}), see {log}",
              file=sys.stderr)
        return None
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("role")
    ap.add_argument("task_file")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--config", default="pipeline.yaml")
    args = ap.parse_args()

    kit_root = pathlib.Path(__file__).resolve().parent
    proj_root = find_config(pathlib.Path.cwd(), args.config).parent
    cfg = yaml.safe_load((proj_root / args.config).read_text())

    engines = cfg.get("engines") or die("no 'engines' in config")
    roles = cfg.get("roles") or die("no 'roles' in config")
    policy = cfg.get("policy", {})
    role_cfg = roles.get(args.role) or die(f"role '{args.role}' not defined")

    role_prompt = kit_root / "roles" / f"{args.role}.md"
    role_prompt.is_file() or die(f"missing role prompt {role_prompt}")
    task = pathlib.Path(args.task_file)
    task.is_file() or die(f"task file not found: {task}")
    task = task.resolve()

    chain = [role_cfg["engine"], *role_cfg.get("fallback", [])]
    mode = role_cfg.get("mode", "ro")
    state_key = role_cfg.get("state_as", args.role)
    retry = int(policy.get("retry", 1))
    timeout = int(policy.get("timeout_secs", 1800))

    # cross-check: this role's engine must differ (by model family) from
    # the engine last used by the role named in 'differ_from'
    state = load_state(proj_root)
    banned = None
    if policy.get("cross_check") and role_cfg.get("differ_from"):
        last = state.get(role_cfg["differ_from"])
        if last:
            banned = engine_family(engines, last)

    # pass project commands to adapters so rw engines can allowlist them
    # (headless runs cannot answer permission prompts)
    env = os.environ.copy()
    for key in ("test_cmd", "lint_cmd"):
        if cfg.get(key):
            env[f"PIPELINE_{key.upper()}"] = cfg[key]

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (proj_root / LOG_DIR).mkdir(parents=True, exist_ok=True)

    for eng in chain:
        if banned and engine_family(engines, eng) == banned:
            print(f"[dispatcher] skip {eng}: cross_check vs "
                  f"{role_cfg['differ_from']} (family={banned})",
                  file=sys.stderr)
            continue
        ecfg = engines.get(eng) or die(f"engine '{eng}' not defined")
        if not engine_supports_mode(ecfg, mode):
            print(f"[dispatcher] skip {eng}: mode '{mode}' not supported "
                  f"by adapter {ecfg.get('adapter')}", file=sys.stderr)
            continue
        if not health_ok(ecfg):
            print(f"[dispatcher] skip {eng}: health check failed",
                  file=sys.stderr)
            continue
        adapter = str((kit_root / ecfg["adapter"]).resolve())
        eng_env = {**env, **{k: str(v) for k, v in
                             (ecfg.get("env") or {}).items()}}
        for attempt in range(1, retry + 1):
            log = proj_root / LOG_DIR / f"{ts}-{args.role}-{eng}-{attempt}.log"
            print(f"[dispatcher] role={args.role} engine={eng} "
                  f"attempt={attempt}/{retry} mode={mode}", file=sys.stderr)
            out = run_engine(adapter, role_prompt, task, args.workdir,
                             mode, timeout, log, eng_env)
            if out is None:
                continue
            verdict = None
            if args.role in GATE_VERDICTS:
                verdict = parse_verdict(out, GATE_VERDICTS[args.role])
                if verdict is None:
                    # non-compliant output = engine failure -> fail over;
                    # a compliant "fail" must NOT fail over (no verdict shopping)
                    print(f"[dispatcher] engine {eng} output has no valid "
                          f"verdict line (gate role '{args.role}'), see {log}",
                          file=sys.stderr)
                    continue
            sys.stdout.write(out)
            if not out.endswith("\n"):
                sys.stdout.write("\n")
            state[state_key] = eng
            save_state(proj_root, state)
            if verdict == "fail":
                print(f"[dispatcher] GATE FAIL role={args.role} engine={eng} "
                      f"verdict=fail log={log}", file=sys.stderr)
                sys.exit(3)
            print(f"[dispatcher] OK role={args.role} engine={eng} "
                  f"state={state_key} log={log}", file=sys.stderr)
            return
    die(f"all engines failed for role '{args.role}' "
        f"(chain={chain}, banned={banned})", code=1)


if __name__ == "__main__":
    main()
