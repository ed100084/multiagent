# Codex Review: pipeline-kit

Review date: 2026-07-03
Status: refreshed after the dispatcher/council hardening pass.

## Summary

`pipeline-kit` is a small engine-agnostic multi-agent runner. The core split is
still sound: role prompts define behavior and output contracts, adapters isolate
engine-specific invocation, and `dispatcher.py` / `council.py` handle routing,
failover, audit logs, and machine-readable gates.

The previous highest-priority artifact integrity issues have been fixed. The
remaining concerns are mostly deployment-policy questions: trusted shell health
checks, prompt size limits through CLI arguments, and how strict council schema
validation should become before it is used as a hard decision gate.

## Resolved Since Previous Review

### Reviewer gate is enforced by dispatcher

- File: `pipeline-kit/dispatcher.py`
- Status: fixed

Reviewer output is parsed with an exact `verdict: pass|fail` line. Missing or
conflicting verdicts are treated as engine failures and can fail over. A valid
`verdict: fail` exits 3 and does not fail over, avoiding verdict shopping.

### Failed gate attempts no longer pollute stdout artifacts

- File: `pipeline-kit/dispatcher.py`
- Status: fixed

`run_engine()` now returns stdout without printing it. The dispatcher prints
stdout only after gate validation has accepted the output, so a non-compliant
first reviewer cannot leak into redirected artifacts such as `30-review.md`.

### Task paths are stable across `--workdir`

- File: `pipeline-kit/dispatcher.py`
- Status: fixed

The dispatcher resolves `task_file` to an absolute path before invoking adapters.
Adapters can now change directory to `--workdir` without losing access to the
task file.

### Council run ids cannot escape or overwrite artifact directories

- File: `pipeline-kit/council.py`
- Status: fixed

Council `--id` values are restricted to `[A-Za-z0-9._-]+`, with `.` and `..`
rejected. Existing output directories now fail loud instead of being reused.

### Local LLM health timeout is configurable

- File: `pipeline-kit/dispatcher.py`
- Status: already fixed in current code

`health_ok()` honors per-engine `health_timeout`, so the local LLM timeout in
`pipeline.yaml.example` is no longer misleading.

## Remaining Findings

### 1. Health checks execute trusted shell from config

- File: `pipeline-kit/dispatcher.py`
- Severity: medium

`health_ok()` still runs `engine_cfg["health"]` with `shell=True`. This is
acceptable for local trusted use, but it is unsafe if an automated gate runs a
PR-modified `pipeline.yaml` before human review. For CI, protect
`pipeline.yaml`, run from a locked config, or replace health checks with
structured adapter health commands.

### 2. Agentic adapters pass full prompts as CLI arguments

- Files: `pipeline-kit/adapters/claude.sh`, `pipeline-kit/adapters/codex.sh`
- Severity: medium

Claude and Codex adapters still concatenate role prompt plus task into a single
CLI argument. Large review tasks can hit OS argument-length limits. The current
docs warn not to paste large diffs into task files; a stronger future fix would
use stdin or prompt files if the CLIs support them, or fail early with a clear
size limit.

### 3. Council validates verdict but not the full schema

- File: `pipeline-kit/council.py`
- Severity: low

The moderator verdict line is validated, but `confidence:` and panelist output
schema are not. This is fine for the current PoC; if council becomes a hard gate,
add lightweight validation for required confidence lines and section presence.

## Verification

Local checks run after the fixes:

```bash
python3 -m unittest discover -s pipeline-kit/tests -v
python3 -m py_compile pipeline-kit/dispatcher.py pipeline-kit/council.py pipeline-kit/tests/test_regressions.py
bash -n pipeline-kit/run-agent.sh pipeline-kit/run-council.sh pipeline-kit/init-project.sh pipeline-kit/adapters/claude.sh pipeline-kit/adapters/codex.sh pipeline-kit/adapters/proxyapi.sh
python3 pipeline-kit/dispatcher.py --help
python3 pipeline-kit/council.py --help
python3 -c "import yaml; data=yaml.safe_load(open('pipeline-kit/pipeline.yaml.example')); print(sorted(data)); print(len(data['engines']), sorted(data['roles']))"
```

All listed checks passed. `shellcheck` is not installed in this environment, so
shell static analysis was not run.
