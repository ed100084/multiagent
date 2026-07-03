#!/usr/bin/env bash
# One-shot project onboarding: init-project.sh [project_dir]
# Idempotent — safe to re-run; never overwrites an existing pipeline.yaml.
set -euo pipefail

KIT="$(dirname "$(readlink -f "$0")")"
PROJ="$(readlink -f "${1:-.}")"
cd "$PROJ"

# ── detect language / test / lint from project files ─────────────────
NAME="$(basename "$PROJ")"
LANG_="" TEST_CMD="" LINT_CMD=""
if   [ -f package.json ]; then
  LANG_=typescript; TEST_CMD="npm test"; LINT_CMD="npm run lint"
elif [ -f pyproject.toml ] || [ -f setup.py ] || [ -f requirements.txt ]; then
  LANG_=python; TEST_CMD="pytest"; LINT_CMD="ruff check ."
elif [ -f go.mod ]; then
  LANG_=go; TEST_CMD="go test ./..."; LINT_CMD="go vet ./..."
elif [ -f Cargo.toml ]; then
  LANG_=rust; TEST_CMD="cargo test"; LINT_CMD="cargo clippy"
else
  LANG_=unknown; TEST_CMD="true"; LINT_CMD="true"
  echo "[init] WARN: 無法偵測專案類型，test_cmd/lint_cmd 先設為 no-op，請手動編輯 pipeline.yaml"
fi

# ── pipeline.yaml ─────────────────────────────────────────────────────
if [ -f pipeline.yaml ]; then
  echo "[init] pipeline.yaml 已存在，略過（不覆蓋）"
else
  sed -e "s|^project: .*|project: $NAME|" \
      -e "s|^language: .*|language: $LANG_|" \
      -e "s|^test_cmd: .*|test_cmd: \"$TEST_CMD\"|" \
      -e "s|^lint_cmd: .*|lint_cmd: \"$LINT_CMD\"|" \
      "$KIT/pipeline.yaml.example" > pipeline.yaml
  echo "[init] pipeline.yaml 已建立（project=$NAME language=$LANG_）"
fi

# ── CLAUDE.md / AGENTS.md 引用段落（有 marker 就不重複加）──────────────
REF_MARKER="pipeline-kit/AGENTS.md"
REF_BLOCK="
## 多引擎派工（pipeline-kit）
涉及「新 feature 實作流程」或「技術選型/架構決策」時，先讀
$KIT/AGENTS.md 並遵守其硬性規則。
"
for f in CLAUDE.md AGENTS.md; do
  if [ -f "$f" ] && grep -q "$REF_MARKER" "$f"; then
    echo "[init] $f 已含引用，略過"
  else
    printf '%s' "$REF_BLOCK" >> "$f"
    echo "[init] $f 已加入 pipeline-kit 引用段落"
  fi
done

# ── .gitignore（logs 不進版控；其餘 pipeline/ 產物是稽核軌跡要 commit）──
if [ -f .gitignore ] && grep -qx 'pipeline/logs/' .gitignore; then
  echo "[init] .gitignore 已含 pipeline/logs/，略過"
else
  printf 'pipeline/logs/\n' >> .gitignore
  echo "[init] .gitignore 已加入 pipeline/logs/"
fi

mkdir -p pipeline

echo "[init] 完成。下一步："
echo "  mkdir -p pipeline/F-001 && echo '<需求>' > pipeline/F-001/00-spec.md"
echo "  $KIT/run-agent.sh architect pipeline/F-001/00-spec.md > pipeline/F-001/10-plan.md"
