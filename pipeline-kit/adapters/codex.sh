#!/usr/bin/env bash
# Adapter contract: $1=role_prompt $2=task_file $3=workdir $4=mode(ro|rw)
set -euo pipefail
PROMPT="$(cat "$1"; printf '\n--- TASK ---\n'; cat "$2")"
SANDBOX="read-only"
[ "$4" = "rw" ] && SANDBOX="workspace-write"
cd "$3"
exec codex exec --sandbox "$SANDBOX" --skip-git-repo-check "$PROMPT"
