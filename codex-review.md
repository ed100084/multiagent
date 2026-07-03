# Codex Review: pipeline-kit

Review date: 2026-07-03

## Summary

`pipeline-kit` is a PoC multi-agent runner with a clear separation between
role prompts, adapters, and engine dispatch. The main flow is understandable,
and the README / requirement documents are broadly aligned with the
implementation.

The highest-priority gap remains machine-enforced gating: role prompts require
structured verdicts, but the dispatcher mostly trusts adapter exit codes and
does not enforce reviewer `verdict: fail`. A second class of risks comes from
automation hardening: trusted shell health checks, council artifact path
handling, and large prompts passed as CLI arguments.

## Findings

### 1. Reviewer `verdict: fail` is not enforced by dispatcher

- File: `pipeline-kit/dispatcher.py:82`
- Severity: high

`run_engine()` treats any zero adapter exit code as success and prints stdout.
It does not parse role-specific output. For the `reviewer` role,
`roles/reviewer.md` requires an exact `verdict: pass` or `verdict: fail`, but
`dispatcher.py` will record the reviewer engine as successful even when the
model returns `verdict: fail`.

This is not just a theoretical mismatch. A local mock check returned `True`
from `run_engine()` for stdout containing:

```text
# Review Report
verdict: fail
## Findings
- high issue
```

The README currently shows an external `grep` gate, which works for manual use,
but this is easy to forget and weak for CI. If reviewer output is meant to be a
gate, `dispatcher.py` should enforce `verdict: pass` for reviewer runs, or a
dedicated gate script should be part of the kit and documented as mandatory.

This also conflicts with `requirement.md:127`, which states that structured
fields such as `verdict: pass|fail` are parsed by an engine-agnostic
dispatcher.

### 2. `health` commands execute trusted shell from project config

- File: `pipeline-kit/dispatcher.py:56`
- Severity: medium

`health_ok()` runs `engine_cfg["health"]` with `shell=True`. This is acceptable
if `pipeline.yaml` is treated as trusted local code, but it is unsafe if a PR
can modify `pipeline.yaml` and a gate runner executes health checks before
human review.

For CI or shared repositories, prefer one of these:

- Treat `pipeline.yaml` as trusted and protect changes to it.
- Replace arbitrary shell health checks with structured adapter health commands.
- Run health checks only from a locked config outside the PR worktree.

### 3. Local LLM health timeout setting is ineffective

- Files: `pipeline-kit/dispatcher.py:61`, `pipeline-kit/pipeline.yaml.example:86`
- Severity: medium

`pipeline.yaml.example` documents the local LLM health check as having an
extended timeout for model loading and uses `curl -m 120`. In practice,
`dispatcher.py` wraps every health command with `subprocess.run(...,
timeout=60)`, so the dispatcher kills that health check after 60 seconds before
curl's 120-second timeout can matter.

For local engines that may cold-load a model, either make the dispatcher health
timeout configurable, derive it from the engine config, or lower the documented
curl timeout and remove the misleading comment.

### 4. Council run id can write outside the intended artifact directory and overwrite prior runs

- File: `pipeline-kit/council.py:129`
- Severity: medium

`--id` is used directly as a path segment:

```python
run_id = args.id or ts
outdir = proj_root / COUNCIL_DIR / run_id
```

An id containing `../` can escape `pipeline/council/`, and an existing id can
overwrite prior artifacts because the code creates directories with
`exist_ok=True` and then writes fixed filenames inside them. If
`run-council.sh` will ever be called from automation, sanitize the id to a
conservative pattern such as `[A-Za-z0-9._-]+`, reject path separators, and
fail if the output directory already exists.

### 5. Claude and Codex adapters pass the full prompt as a CLI argument

- Files: `pipeline-kit/adapters/claude.sh:5`, `pipeline-kit/adapters/codex.sh:4`
- Severity: medium

Both agentic adapters concatenate the role prompt and task into a shell
variable, then pass the full content as a single CLI argument:

```bash
PROMPT="$(cat "$1"; printf '\n--- TASK ---\n'; cat "$2")"
```

For small tasks this is fine, but review tasks can include large diffs. A large
prompt can exceed the OS command-line argument limit and fail before the engine
starts, typically as `Argument list too long`. This is likely to surface
exactly on larger reviews, where the gate matters most.

Prefer stdin or a temporary prompt file if the CLIs support it. If not, enforce
a documented max task size before invoking the adapter and fail loud with a
clear message.

### 6. Council validates moderator verdict but not the rest of the required schema

- File: `pipeline-kit/council.py:76`
- Severity: low

`parse_verdict()` validates `verdict: consensus|split` for the moderator.
It does not validate the moderator's required `confidence: high|medium|low`
line from `roles/moderator.md`, and panelist output is not validated for the
required `confidence: high|medium|low` line or basic section structure from
`roles/panelist.md`.

This is probably fine for early PoC usage, but if council output becomes a
decision gate, add lightweight validation before feeding panel answers into
later rounds and before accepting the judge output.

### 7. README has small runnable-documentation issues

- Files: `pipeline-kit/README.md:37`, `pipeline-kit/README.md:133`
- Severity: low

The quickstart writes to `pipeline/F-001/00-spec.md` without first creating the
directory, so a copy-paste run fails unless the user already made that path.
Add `mkdir -p pipeline/F-001` before the first `echo`.

The README also says:

```text
套用到 StudyPlan（~/projects/studyplan）→ zeroshot/tutti 對照評估（requirement.md §6）
```

but `requirement.md` currently has no section 6. Either restore the missing
section or update the reference.

### 8. Workspace is not currently a git repository

- Observation: `git status --short` returned `fatal: not a git repository`.
- Severity: low

The workspace contains a `.git` directory entry, but Git does not recognize the
current directory as a repository. The kit writes audit state and artifacts
under `pipeline/`, and the docs mention git-tracked state. If this directory is
intended to be the canonical project workspace, initialize or move it into a
valid git repository before relying on auditability.

## Verification

Local checks run:

```bash
python3 -m py_compile pipeline-kit/dispatcher.py pipeline-kit/council.py
python3 pipeline-kit/dispatcher.py --help
python3 pipeline-kit/council.py --help
bash -n pipeline-kit/run-agent.sh
bash -n pipeline-kit/run-council.sh
bash -n pipeline-kit/adapters/claude.sh
bash -n pipeline-kit/adapters/codex.sh
bash -n pipeline-kit/adapters/proxyapi.sh
python3 - <<'PY'
import yaml
with open('pipeline-kit/pipeline.yaml.example') as f:
    data = yaml.safe_load(f)
print(sorted(data.keys()))
print('engines', len(data['engines']))
print('roles', sorted(data['roles']))
print('council', sorted(data['council']))
PY
```

All local checks passed. I also ran a local mock of `dispatcher.run_engine()`
showing that reviewer stdout with `verdict: fail` is currently accepted as a
successful engine run.

`shellcheck` is not installed in this environment, so shell static analysis was
not run. I did not run live Claude / Codex / proxy engine tests because this
workspace root does not currently contain a `pipeline.yaml`, and those checks
would invoke external model services.
