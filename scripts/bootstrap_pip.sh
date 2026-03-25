#!/usr/bin/env bash
set -u
ROOT=$(cd -- "$(dirname -- "$0")"/.. && pwd)
RUNTIME_DIR="$ROOT/runtime"
LOG="$RUNTIME_DIR/bootstrap_pip.log"
mkdir -p "$RUNTIME_DIR"
bootstrap_status=0
python3 -m ensurepip --upgrade >>"$LOG" 2>&1 || bootstrap_status=$?
pip_status=0
python3 -m pip --version >>"$LOG" 2>&1 || pip_status=$?
printf '{"ensurepip_status":%d,"pip_status":%d}\n' "$bootstrap_status" "$pip_status" > "$RUNTIME_DIR/bootstrap_pip.json"
if [ "$bootstrap_status" -ne 0 ] || [ "$pip_status" -ne 0 ]; then
    exit 1
fi
exit 0
