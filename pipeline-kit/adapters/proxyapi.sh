#!/usr/bin/env bash
# Adapter contract: $1=role_prompt $2=task_file $3=workdir $4=mode(ro|rw)
# Text-only engine via any OpenAI-compatible endpoint (e.g. CLIProxyAPI).
# rw is unsupported by design: exit 1 so the dispatcher fails over to an
# agentic CLI engine. Config comes from env or ~/.config/pipeline-kit/proxyapi.env
set -euo pipefail
[ "${4:-ro}" = "rw" ] && { echo "[proxyapi] text-only engine, no rw" >&2; exit 1; }

ENV_FILE="${PROXYAPI_ENV:-$HOME/.config/pipeline-kit/proxyapi.env}"
# per-engine model from dispatcher (engines.<name>.env) wins over the env file
MODEL_OVERRIDE="${PROXYAPI_MODEL:-}"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
: "${PROXYAPI_URL:?set PROXYAPI_URL (e.g. http://192.168.88.115:8317)}"
: "${PROXYAPI_KEY:?set PROXYAPI_KEY}"
export PROXYAPI_URL PROXYAPI_KEY
MODEL="${MODEL_OVERRIDE:-${PROXYAPI_MODEL:-gpt-5.5}}"

cd "$3"
exec python3 - "$1" "$2" "$MODEL" <<'PY'
import json, os, sys, urllib.request

role, task, model = sys.argv[1], sys.argv[2], sys.argv[3]
prompt = open(role).read() + "\n--- TASK ---\n" + open(task).read()
req = urllib.request.Request(
    os.environ["PROXYAPI_URL"].rstrip("/") + "/v1/chat/completions",
    data=json.dumps({"model": model,
                     "messages": [{"role": "user", "content": prompt}]}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + os.environ["PROXYAPI_KEY"]})
with urllib.request.urlopen(req, timeout=1740) as r:
    body = json.load(r)
print(body["choices"][0]["message"]["content"])
PY
