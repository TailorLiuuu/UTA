#!/bin/bash
# Package the verified, already signed App. Does not register services or install it.
set -euo pipefail
uta_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$uta_root"
uta_app=$(cat build/verified-app-path.txt)
test -d "$uta_app"
codesign --verify --deep --strict "$uta_app"
uta_version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$uta_app/Contents/Info.plist")
uta_stage=$(mktemp -d /private/tmp/uta-dmg.XXXXXX)
mkdir "$uta_stage/content"
ditto --norsrc "$uta_app" "$uta_stage/content/UTA.app"
ln -s /Applications "$uta_stage/content/Applications"
cp docs/INSTALL-macOS.md "$uta_stage/content/安装指南.txt"
cp THIRD_PARTY.md "$uta_stage/content/Third-party-notices.txt"
codesign --verify --deep --strict "$uta_stage/content/UTA.app"
uta_dmg="$uta_root/dist/UTA-${uta_version}-macOS-arm64.dmg"
hdiutil create -volname UTA -srcfolder "$uta_stage/content" -format UDZO -ov "$uta_dmg"
if [ -n "${UTA_SIGN_IDENTITY:-}" ] && [ "$UTA_SIGN_IDENTITY" != "-" ]; then
    codesign --force --sign "$UTA_SIGN_IDENTITY" --timestamp "$uta_dmg"
    codesign --verify --strict "$uta_dmg"
fi
hdiutil verify "$uta_dmg"
printf 'Installer: %s\n' "$uta_dmg"
