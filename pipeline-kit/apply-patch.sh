#!/usr/bin/env bash
# Deterministic local patch applier:
# apply-patch.sh <patch_file> [--workdir DIR] [--config pipeline.yaml]
set -euo pipefail

usage() {
  echo "usage: apply-patch.sh <patch_file> [--workdir DIR] [--config pipeline.yaml]" >&2
}

find_config() {
  local dir="$1"
  local name="$2"
  while true; do
    if [ -f "$dir/$name" ]; then
      printf '%s\n' "$dir/$name"
      return 0
    fi
    [ "$dir" = "/" ] && return 1
    dir="$(dirname "$dir")"
  done
}

run_project_command() {
  local label="$1"
  local cmd="$2"
  local out="$TMP_DIR/$label.out"

  printf '### %s\n\n' "$label"
  printf '```text\n$ %s\n' "$cmd"
  set +e
  bash -lc "$cmd" >"$out" 2>&1
  local code=$?
  set -e
  if [ -s "$out" ]; then
    cat "$out"
  else
    printf '(no output)\n'
  fi
  printf '```\nexit=%s\n\n' "$code"
  return "$code"
}

PATCH_FILE=""
WORKDIR="."
CONFIG_NAME="pipeline.yaml"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workdir)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      WORKDIR="$2"
      shift 2
      ;;
    --config)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      CONFIG_NAME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      [ -z "$PATCH_FILE" ] || { usage; exit 2; }
      PATCH_FILE="$1"
      shift
      ;;
  esac
done

[ -n "$PATCH_FILE" ] || { usage; exit 2; }
[ -f "$PATCH_FILE" ] || { echo "[apply-patch] patch file not found: $PATCH_FILE" >&2; exit 2; }

PATCH_PATH="$(readlink -f "$PATCH_FILE")"
WORKDIR_PATH="$(readlink -f "$WORKDIR")"
CONFIG_PATH="$(find_config "$WORKDIR_PATH" "$CONFIG_NAME")" || {
  echo "[apply-patch] $CONFIG_NAME not found from $WORKDIR_PATH upward" >&2
  exit 2
}
PROJECT_ROOT="$(dirname "$CONFIG_PATH")"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cd "$PROJECT_ROOT"
git rev-parse --show-toplevel >/dev/null 2>&1 || {
  echo "[apply-patch] project root is not inside a git worktree: $PROJECT_ROOT" >&2
  exit 2
}

mapfile -t COMMANDS < <(python3 - "$CONFIG_PATH" <<'PY'
import sys
import yaml

cfg = yaml.safe_load(open(sys.argv[1])) or {}
for key in ("test_cmd", "lint_cmd"):
    value = cfg.get(key)
    print(value if value else "true")
PY
)
TEST_CMD="${COMMANDS[0]}"
LINT_CMD="${COMMANDS[1]}"

echo "# Implementation Report"
echo
echo "## Changed files"

CHECK_OUT="$TMP_DIR/git-apply-check.out"
if ! git apply --check "$PATCH_PATH" >"$CHECK_OUT" 2>&1; then
  echo
  echo "Patch check failed; no files were changed."
  echo
  echo '```text'
  cat "$CHECK_OUT"
  echo '```'
  echo
  echo "## Test result"
  echo "not run"
  echo
  echo "## Deviations from plan"
  echo "Patch did not apply cleanly."
  echo
  echo "## Status: blocked"
  exit 1
fi

APPLY_OUT="$TMP_DIR/git-apply.out"
if ! git apply "$PATCH_PATH" >"$APPLY_OUT" 2>&1; then
  echo
  echo "Patch apply failed after a successful check."
  echo
  echo '```text'
  cat "$APPLY_OUT"
  echo '```'
  echo
  echo "## Test result"
  echo "not run"
  echo
  echo "## Deviations from plan"
  echo "Patch apply failed unexpectedly."
  echo
  echo "## Status: blocked"
  exit 1
fi

STATUS_OUT="$TMP_DIR/git-status.out"
git status --short >"$STATUS_OUT"
if [ -s "$STATUS_OUT" ]; then
  echo
  echo '```text'
  cat "$STATUS_OUT"
  echo '```'
else
  echo
  echo "none"
fi
echo
echo "## Test result"

status="done"
exit_code=0
if ! run_project_command "test" "$TEST_CMD"; then
  status="blocked"
  exit_code=1
fi
if ! run_project_command "lint" "$LINT_CMD"; then
  status="blocked"
  exit_code=1
fi

echo "## Deviations from plan"
if [ "$status" = "done" ]; then
  echo "none"
else
  echo "Patch was applied, but at least one verification command failed."
fi
echo
echo "## Status: $status"
exit "$exit_code"
