#!/usr/bin/env bash
set -u
ROOT=$(cd -- "$(dirname -- "$0")"/.. && pwd)
VENV="$ROOT/.venv"
RUNTIME_DIR="$ROOT/runtime"
LOG="$RUNTIME_DIR/bootstrap_venv.log"
SUMMARY="$RUNTIME_DIR/bootstrap_venv.json"
mkdir -p "$RUNTIME_DIR"
venv_status=0
python3 -m venv "$VENV" >>"$LOG" 2>&1 || venv_status=$?
pip_upgrade_status=0
install_probe_status=0
if [ "$venv_status" -eq 0 ]; then
    "$VENV/bin/python" -m pip install --upgrade pip >>"$LOG" 2>&1 || pip_upgrade_status=$?
    if [ "$pip_upgrade_status" -eq 0 ]; then
        PATH="$VENV/bin:$PATH" "$ROOT/scripts/install_and_probe.sh" >>"$LOG" 2>&1 || install_probe_status=$?
    fi
fi
printf '{"venv_status":%d,"pip_upgrade_status":%d,"install_probe_status":%d}\n' "$venv_status" "$pip_upgrade_status" "$install_probe_status" > "$SUMMARY"
if [ "$venv_status" -ne 0 ] || [ "$pip_upgrade_status" -ne 0 ] || [ "$install_probe_status" -ne 0 ]; then
    exit 1
fi
exit 0
