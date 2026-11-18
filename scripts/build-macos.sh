#!/bin/bash
# Run on the Mac whose architecture you want to distribute.
set -euo pipefail
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This build must run on macOS." >&2
    exit 1
fi
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
task_python="${PYTHON:-python3.13}"
"$task_python" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy --follow-imports=silent \
    --exclude '(^|/)(win32_[^/]+\.py|windows/|windows_destination\.py|pipe_transport\.py)' src
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/macos.spec
dist/LanInputShare.app/Contents/MacOS/LanInputShare --gui-smoke-test
dist/LanInputShare.app/Contents/MacOS/LanInputShare --diagnose
ditto -c -k --sequesterRsrc --keepParent dist/LanInputShare.app dist/LanInputShare-macos.zip
shasum -a 256 dist/LanInputShare-macos.zip > dist/LanInputShare-macos.sha256
echo "Built: $task_root/dist/LanInputShare.app"
