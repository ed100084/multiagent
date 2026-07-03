#!/usr/bin/env bash
# Adapter contract: $1=role_prompt $2=task_file $3=workdir $4=mode(ro|rw)
# stdout = result, exit code = success/failure
set -euo pipefail
PROMPT="$(cat "$1"; printf '\n--- TASK ---\n'; cat "$2")"
PERM="plan"                       # ro: plan mode = no edits
EXTRA=()
if [ "$4" = "rw" ]; then
    PERM="acceptEdits"
    # acceptEdits only auto-approves file edits; headless runs cannot answer
    # Bash permission prompts, so allowlist the project's test/lint commands
    # (dispatcher exports them from pipeline.yaml)
    ALLOW=()
    [ -n "${PIPELINE_TEST_CMD:-}" ] && ALLOW+=("Bash(${PIPELINE_TEST_CMD})" "Bash(${PIPELINE_TEST_CMD}:*)")
    [ -n "${PIPELINE_LINT_CMD:-}" ] && ALLOW+=("Bash(${PIPELINE_LINT_CMD})" "Bash(${PIPELINE_LINT_CMD}:*)")
    [ "${#ALLOW[@]}" -gt 0 ] && EXTRA=(--allowedTools "${ALLOW[@]}")
fi
cd "$3"
exec claude -p "$PROMPT" --permission-mode "$PERM" --output-format text "${EXTRA[@]}"
