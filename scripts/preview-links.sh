#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
app="$PWD/build/UTA Links Preview.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swiftc -swift-version 5 -parse-as-library -module-cache-path build/swift-cache \
  macos/LinkGraph.swift macos/LinkCanvas.swift macos/Localization.swift tests/LinkCanvasPreview.swift \
  -o "$app/Contents/MacOS/UTALinkPreview"
cp macos/Resources/en.json "$app/Contents/Resources/en.json"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>dev.uta.links-preview</string><key>CFBundleExecutable</key><string>UTALinkPreview</string><key>CFBundleName</key><string>UTA Links Preview</string><key>NSHighResolutionCapable</key><true/></dict></plist>
PLIST
printf '%s\n' "$app"
