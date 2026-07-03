#!/usr/bin/env bash
# Thin entry point: run-agent.sh <role> <task_file> [--workdir DIR]
exec python3 "$(dirname "$(readlink -f "$0")")/dispatcher.py" "$@"
