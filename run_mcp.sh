#!/bin/sh
# Run with the portable environment when present, otherwise the active Python.
set -eu
MEDIAOCR_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$MEDIAOCR_ROOT/.venv/bin/python" ]; then
    exec "$MEDIAOCR_ROOT/.venv/bin/python" "$MEDIAOCR_ROOT/server.py" "$@"
fi
exec python3 "$MEDIAOCR_ROOT/server.py" "$@"
