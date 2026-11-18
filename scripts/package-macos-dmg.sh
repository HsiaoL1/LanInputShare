#!/bin/bash
# Package the locally built app as a drag-to-Applications disk image.
set -euo pipefail

task_root="$(cd "$(dirname "$0")/.." && pwd)"
task_app="$task_root/dist/LanInputShare.app"
task_dmg="$task_root/dist/LanInputShare-macos.dmg"
if [[ ! -d "$task_app/Contents/MacOS" ]]; then
    echo "Build LanInputShare.app before creating the DMG." >&2
    exit 1
fi

task_stage="$(mktemp -d)"
trap 'rm -rf "$task_stage"' EXIT
mkdir -p "$task_stage/LanInputShare"
ditto "$task_app" "$task_stage/LanInputShare/LanInputShare.app"
ln -s /Applications "$task_stage/LanInputShare/Applications"
hdiutil create -volname LanInputShare -srcfolder "$task_stage/LanInputShare" \
    -format UDZO -ov "$task_dmg"
hdiutil verify "$task_dmg"
(cd "$task_root/dist" && shasum -a 256 LanInputShare-macos.dmg > LanInputShare-macos.dmg.sha256)
echo "Built: $task_dmg"
