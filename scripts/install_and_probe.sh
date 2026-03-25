#!/usr/bin/env bash
set -u
ROOT=$(cd -- "$(dirname -- "$0")"/.. && pwd)
RUNTIME_DIR="$ROOT/runtime"
LOG="$RUNTIME_DIR/install_probe.log"
LOCK="$RUNTIME_DIR/requirements.lock"
SUMMARY="$RUNTIME_DIR/install_probe.json"
mkdir -p "$RUNTIME_DIR"
install_status=0
python3 -m pip install -r "$ROOT/requirements.txt" >>"$LOG" 2>&1 || install_status=$?
freeze_status=0
if [ "$install_status" -eq 0 ]; then
    python3 -m pip freeze > "$LOCK" 2>>"$LOG" || freeze_status=$?
else
    echo "pip install failed, skipping freeze" >>"$LOG"
    echo "install_status=$install_status" > "$LOCK"
    freeze_status=$install_status
fi
probe_status=0
python3 "$ROOT/scripts/check_runtime.py" >>"$LOG" 2>&1 || probe_status=$?
printf '{"install_status":%d,"freeze_status":%d,"probe_status":%d}\n' "$install_status" "$freeze_status" "$probe_status" > "$SUMMARY"
if [ "$install_status" -ne 0 ] || [ "$freeze_status" -ne 0 ] || [ "$probe_status" -ne 0 ]; then
    exit 1
fi
exit 0
