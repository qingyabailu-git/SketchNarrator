#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SKETCH_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SKETCH_PY="$SKETCH_ROOT/renderer/.venv/bin/python"

supports_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' >/dev/null 2>&1
}

if [ -x "$SKETCH_PY" ] && "$SKETCH_PY" "$SKETCH_ROOT/renderer/scripts/prepare_env.py" --check >/dev/null 2>&1; then
  :
else
  if [ -x "$SKETCH_PY" ] && supports_python "$SKETCH_PY"; then
    BOOTSTRAP_PY=$SKETCH_PY
  elif [ -n "${SKETCHNARRATOR_BOOTSTRAP_PYTHON:-}" ] && [ -x "$SKETCHNARRATOR_BOOTSTRAP_PYTHON" ] && supports_python "$SKETCHNARRATOR_BOOTSTRAP_PYTHON"; then
    BOOTSTRAP_PY=$SKETCHNARRATOR_BOOTSTRAP_PYTHON
  elif command -v python3 >/dev/null 2>&1 && supports_python python3; then
    BOOTSTRAP_PY=python3
  elif command -v python >/dev/null 2>&1 && supports_python python; then
    BOOTSTRAP_PY=python
  elif [ -x "${HOME:-}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python" ] && supports_python "${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python"; then
    BOOTSTRAP_PY="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python"
  else
    echo "[err] Python 3.10+ was not found. The host can set SKETCHNARRATOR_BOOTSTRAP_PYTHON to a valid interpreter." >&2
    exit 2
  fi
  if [ "${1:-}" = doctor ]; then
    exec "$BOOTSTRAP_PY" -B "$SKETCH_ROOT/scripts/doctor.py" "$@"
  fi
  "$BOOTSTRAP_PY" "$SKETCH_ROOT/renderer/scripts/prepare_env.py"
fi

if [ "${1:-}" = doctor ]; then
  exec "$SKETCH_PY" -B "$SKETCH_ROOT/scripts/doctor.py" "$@"
fi
exec "$SKETCH_PY" "$SKETCH_ROOT/scripts/workflow.py" "$@"
