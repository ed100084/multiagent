#!/usr/bin/env bash
# Thin entry point: run-council.sh <question_file> [--rounds N] [--id NAME]
exec python3 "$(dirname "$(readlink -f "$0")")/council.py" "$@"
