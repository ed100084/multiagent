#!/usr/bin/env python3
"""Council mode: dispatch one question to a heterogeneous engine panel,
let them debate for N rounds, then a judge synthesizes a verified verdict.

Usage: council.py <question_file> [--workdir DIR] [--config pipeline.yaml]
                  [--rounds N] [--id NAME]

Flow: panel answers independently -> N revision rounds (each panelist sees
peers' anonymized answers) -> moderator judges claim-by-claim.
Panel engines are deduped by model family (heterogeneity is the point).
Exit: 0 = consensus, 3 = split (escalate to human), 1 = failure, 2 = config.
"""
import argparse
import concurrent.futures
import datetime
import json
import pathlib
import os
import string
import subprocess
import sys

import yaml

import dispatcher as dp

COUNCIL_DIR = "pipeline/council"


def call_adapter(adapter: str, role_prompt: pathlib.Path, task: pathlib.Path,
                 workdir: str, timeout: int, log: pathlib.Path, env: dict):
    """One adapter invocation, always ro. Returns stdout or None."""
    try:
        r = subprocess.run(
            [adapter, str(role_prompt), str(task), workdir, "ro"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        log.write_text("TIMEOUT\n")
        return None
    log.write_text(
        f"exit={r.returncode}\n--- STDOUT ---\n{r.stdout}\n--- STDERR ---\n{r.stderr}\n"
    )
    return r.stdout if r.returncode == 0 else None


def run_stage(kit_root, proj_root, engines, eng, role, task, workdir,
              retry, timeout, base_env, ts, stage):
    ecfg = engines[eng]
    adapter = str((kit_root / ecfg["adapter"]).resolve())
    env = {**base_env, **{k: str(v) for k, v in (ecfg.get("env") or {}).items()}}
    role_prompt = kit_root / "roles" / f"{role}.md"
    for attempt in range(1, retry + 1):
        log = proj_root / dp.LOG_DIR / f"{ts}-council-{stage}-{eng}-{attempt}.log"
        print(f"[council] stage={stage} engine={eng} attempt={attempt}/{retry}",
              file=sys.stderr)
        out = call_adapter(adapter, role_prompt, task, workdir, timeout, log, env)
        if out is not None:
            return out
    print(f"[council] engine {eng} failed stage {stage} after {retry} attempts",
          file=sys.stderr)
    return None


def build_task(path: pathlib.Path, question: str, answers: dict = None,
               heading: str = "PEER ANSWERS") -> pathlib.Path:
    parts = [f"# QUESTION\n\n{question}\n"]
    if answers:
        parts.append(f"\n# {heading}\n")
        for letter, text in sorted(answers.items()):
            parts.append(f"\n## Panelist {letter}\n\n{text}\n")
    path.write_text("".join(parts))
    return path


def parse_verdict(out: str):
    for line in out.splitlines():
        if line.strip() in ("verdict: consensus", "verdict: split"):
            return line.strip().split(": ", 1)[1]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question_file")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--config", default="pipeline.yaml")
    ap.add_argument("--rounds", type=int, default=None,
                    help="debate rounds after initial answers (default: config)")
    ap.add_argument("--id", default=None, help="council run id (default: timestamp)")
    args = ap.parse_args()

    kit_root = pathlib.Path(__file__).resolve().parent
    proj_root = dp.find_config(pathlib.Path.cwd(), args.config).parent
    cfg = yaml.safe_load((proj_root / args.config).read_text())

    engines = cfg.get("engines") or dp.die("no 'engines' in config")
    council = cfg.get("council") or dp.die("no 'council' in config")
    policy = cfg.get("policy", {})
    retry = int(policy.get("retry", 1))
    timeout = int(policy.get("timeout_secs", 1800))
    rounds = args.rounds if args.rounds is not None else int(council.get("rounds", 1))
    min_panel = int(council.get("min_panel", 2))

    question_file = pathlib.Path(args.question_file)
    question_file.is_file() or dp.die(f"question file not found: {question_file}")
    question = question_file.read_text()

    # panel: skip unhealthy engines, dedupe by family (heterogeneity is the point)
    panel, seen_fam = [], {}
    for eng in council.get("panel") or dp.die("council.panel is empty"):
        ecfg = engines.get(eng) or dp.die(f"engine '{eng}' not defined")
        fam = dp.engine_family(engines, eng)
        if fam in seen_fam:
            print(f"[council] skip {eng}: family '{fam}' already seated "
                  f"({seen_fam[fam]})", file=sys.stderr)
            continue
        if not dp.health_ok(ecfg):
            print(f"[council] skip {eng}: health check failed", file=sys.stderr)
            continue
        panel.append(eng)
        seen_fam[fam] = eng
    if len(panel) < min_panel:
        dp.die(f"only {len(panel)} healthy heterogeneous panelists "
               f"(min_panel={min_panel})", code=1)
    letters = dict(zip(panel, string.ascii_uppercase))  # engine -> anon letter

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = args.id or ts
    outdir = proj_root / COUNCIL_DIR / run_id
    (outdir / "tasks").mkdir(parents=True, exist_ok=True)
    (proj_root / dp.LOG_DIR).mkdir(parents=True, exist_ok=True)
    (outdir / "00-question.md").write_text(question)

    base_env = os.environ.copy()

    def fan_out(stage: str, tasks: dict) -> dict:
        """Run panel engines concurrently. tasks: engine -> task path."""
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futs = {ex.submit(run_stage, kit_root, proj_root, engines, eng,
                              "panelist", tasks[eng], args.workdir, retry,
                              timeout, base_env, ts, stage): eng
                    for eng in tasks}
            for fut in concurrent.futures.as_completed(futs):
                eng = futs[fut]
                out = fut.result()
                if out is not None:
                    results[eng] = out
                    (outdir / f"{stage}-{eng}.md").write_text(out)
        return results

    # round 1: independent answers
    r1_task = build_task(outdir / "tasks" / "r1-task.md", question)
    answers = fan_out("r1", {eng: r1_task for eng in panel})
    if len(answers) < min_panel:
        dp.die(f"only {len(answers)} panelists answered (min_panel={min_panel})",
               code=1)

    # debate rounds: each panelist revises after seeing peers' anonymized answers.
    # An engine that fails a revision round keeps its previous answer (carry forward).
    for rnd in range(2, rounds + 2):
        stage = f"r{rnd}"
        tasks = {}
        for eng in answers:
            peers = {letters[e]: a for e, a in answers.items() if e != eng}
            tasks[eng] = build_task(outdir / "tasks" / f"{stage}-{eng}-task.md",
                                    question, peers)
        revised = fan_out(stage, tasks)
        for eng, text in revised.items():
            answers[eng] = text
        carried = set(answers) - set(revised)
        if carried:
            print(f"[council] carry forward (failed {stage}): {sorted(carried)}",
                  file=sys.stderr)

    # judge: moderator synthesizes a verdict; non-compliant output -> next judge
    judge_chain = [council.get("judge") or dp.die("council.judge not set"),
                   *council.get("judge_fallback", [])]
    final = {letters[e]: a for e, a in answers.items()}
    judge_task = build_task(outdir / "tasks" / "judge-task.md", question,
                            final, heading="FINAL ANSWERS")
    verdict, verdict_out, judge_used = None, None, None
    for eng in judge_chain:
        ecfg = engines.get(eng) or dp.die(f"engine '{eng}' not defined")
        if not dp.health_ok(ecfg):
            print(f"[council] skip judge {eng}: health check failed",
                  file=sys.stderr)
            continue
        out = run_stage(kit_root, proj_root, engines, eng, "moderator",
                        judge_task, args.workdir, retry, timeout, base_env,
                        ts, "judge")
        if out is None:
            continue
        v = parse_verdict(out)
        if v is None:
            print(f"[council] judge {eng} output has no valid verdict line, "
                  f"trying next judge", file=sys.stderr)
            continue
        verdict, verdict_out, judge_used = v, out, eng
        break
    if verdict is None:
        dp.die(f"no judge produced a valid verdict (chain={judge_chain})", code=1)

    (outdir / "90-verdict.md").write_text(verdict_out)
    (outdir / "meta.json").write_text(json.dumps({
        "question_file": str(question_file),
        "panel": {eng: {"letter": letters[eng],
                        "family": dp.engine_family(engines, eng)}
                  for eng in panel},
        "answered": sorted(answers),
        "rounds": rounds,
        "judge": judge_used,
        "verdict": verdict,
    }, indent=2, ensure_ascii=False))

    print(verdict_out)
    print(f"[council] verdict={verdict} judge={judge_used} "
          f"artifacts={outdir}", file=sys.stderr)
    sys.exit(0 if verdict == "consensus" else 3)


if __name__ == "__main__":
    main()
