#!/bin/bash
set -euo pipefail
uta_sign_identity=${UTA_SIGN_IDENTITY:--}
if [ "$uta_sign_identity" = "-" ]; then
    if [ "${UTA_ALLOW_ADHOC:-0}" != "1" ]; then
        echo "Set UTA_SIGN_IDENTITY to a valid Developer ID identity. UI-only ad-hoc builds require UTA_ALLOW_ADHOC=1." >&2
        exit 1
    fi
    echo "Warning: ad-hoc build is for UI/protocol development; use UTA_SIGN_IDENTITY for the macOS privileged service." >&2
fi
uta_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$uta_root"
mkdir -p build dist
cp python/uta_server.py dist/uta-server
swift -module-cache-path "$uta_root/build/swift-cache" scripts/generate-icons.swift "$uta_root/build/branding"
iconutil -c icns "$uta_root/build/branding/UTA.iconset" -o "$uta_root/build/branding/UTA.icns"
cmake -S native -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native -j 6
# File-provider metadata in a synced Documents folder can invalidate app signing.
# Sign and archive outside that folder; the ZIP is the distributable artifact.
stage=$(mktemp -d /private/tmp/uta-build.XXXXXX)
app="$stage/UTA.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/bin" "$app/Contents/Resources/python"
mkdir -p "$app/Contents/Resources/docs" "$app/Contents/Resources/licenses"
swiftc -swift-version 5 -parse-as-library -O \
    -module-cache-path "$uta_root/build/swift-cache" \
    macos/UTA.swift macos/USBPermission.swift macos/USBHelperProtocol.swift -o "$app/Contents/MacOS/UTA"
swiftc -swift-version 5 -parse-as-library -O \
    -module-cache-path "$uta_root/build/swift-cache" \
    macos/USBHelperClient.swift macos/USBHelperProtocol.swift -o "$app/Contents/Resources/bin/uta-helper-client"
codesign --force --sign "$uta_sign_identity" --options runtime --timestamp=none --identifier dev.uta.helper-client "$app/Contents/Resources/bin/uta-helper-client"
python3 - "$app/Contents/Resources/bin/uta-helper-client" "$stage/HelperPolicy.swift" <<'PY'
import subprocess,re,sys
info = subprocess.run(["codesign","-d","--verbose=4",sys.argv[1]],capture_output=True,text=True,check=True).stderr
digest = re.search(r"^CDHash=([0-9a-f]+)$",info,re.M).group(1)
with open(sys.argv[2],"w") as f:
    f.write('let utaHelperClientRequirement = "cdhash H\\\"'+digest+'\\\""\n')
PY
swiftc -swift-version 5 -parse-as-library -O \
    -module-cache-path "$uta_root/build/swift-cache" \
    macos/USBHelper.swift macos/USBHelperProtocol.swift "$stage/HelperPolicy.swift" \
    "$uta_root/build/native/libuta_capture.a" "$(pkg-config --variable=libdir libusb-1.0)/libusb-1.0.a" \
    -lc++ -lobjc -framework IOKit -framework CoreFoundation -framework Security \
    -o "$app/Contents/MacOS/UTAUSBHelper"
mkdir -p "$app/Contents/Library/LaunchDaemons"
cp macos/dev.uta.usb-helper*.plist "$app/Contents/Library/LaunchDaemons/"
rm "$stage/HelperPolicy.swift"
cp python/*.py "$app/Contents/Resources/python/"
cp dist/uta-server "$app/Contents/Resources/python/uta_server.py"
cp scripts/uta "$app/Contents/Resources/uta"
cp build/branding/UTAMark.png build/branding/UTA.icns "$app/Contents/Resources/"
cp build/native/uta-native build/native/libuta_transport.dylib "$app/Contents/Resources/bin/"
uta_libusb=$(pkg-config --variable=libdir libusb-1.0)/libusb-1.0.0.dylib
if [ -f "$app/Contents/Resources/bin/libusb-1.0.0.dylib" ]; then
    chmod u+w "$app/Contents/Resources/bin/libusb-1.0.0.dylib"
fi
cp "$uta_libusb" "$app/Contents/Resources/bin/libusb-1.0.0.dylib"
chmod u+w "$app/Contents/Resources/bin/libusb-1.0.0.dylib"
# Relocate only libraries we bundle. System libraries remain untouched.
python3 - "$app/Contents/Resources/bin" <<'PY'
import pathlib, subprocess, sys
base = pathlib.Path(sys.argv[1])
helper = base.parent.parent / "MacOS/UTAUSBHelper"
for path in [*base.iterdir(), helper]:
    deps = subprocess.check_output(["otool", "-L", str(path)], text=True).splitlines()[1:]
    for line in deps:
        old = line.strip().split(" (", 1)[0]
        name = pathlib.Path(old).name
        if name in ("libuta_transport.dylib", "libusb-1.0.0.dylib"):
            prefix = "@executable_path/../Resources/bin/" if path == helper else "@loader_path/"
            subprocess.run(["install_name_tool", "-change", old, prefix + name, str(path)], check=True)
    if path.suffix == ".dylib":
        subprocess.run(["install_name_tool", "-id", "@rpath/" + path.name, str(path)], check=True)
PY
cp docs/*.md "$app/Contents/Resources/docs/"
cp THIRD_PARTY.md "$app/Contents/Resources/licenses/"
cp vendor/usbipdcpp/LICENSE "$app/Contents/Resources/licenses/usbipdcpp-LGPL-3.0.txt"
cp vendor/ASIO_LICENSE.txt vendor/SPDLOG_LICENSE.txt "$app/Contents/Resources/licenses/"
cp vendor/LIBUSB_LICENSE.txt "$app/Contents/Resources/licenses/"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>UTA</string>
<key>CFBundleIdentifier</key><string>dev.usbbridge.mac</string>
<key>CFBundleName</key><string>UTA</string>
<key>CFBundleDisplayName</key><string>UTA</string>
<key>CFBundleIconFile</key><string>UTA.icns</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>13</string>
<key>LSMinimumSystemVersion</key><string>26.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
chmod +x "$app/Contents/Resources/uta" "$app/Contents/Resources/bin/uta-native"
xattr -cr "$app"
for binary in "$app/Contents/Resources/bin/"*; do
    # The helper pins this exact client's signature; never re-sign it afterwards.
    if [ "$(basename "$binary")" != "uta-helper-client" ]; then codesign --force --sign "$uta_sign_identity" --options runtime --timestamp=none "$binary"; fi
done
codesign --force --sign "$uta_sign_identity" --options runtime --timestamp=none --identifier dev.uta.usb-helper "$app/Contents/MacOS/UTAUSBHelper"
codesign --force --sign "$uta_sign_identity" --options runtime --timestamp=none "$app"
codesign --verify --deep --strict "$app"
chmod +x dist/uta-server
cp dist/uta-server "$stage/uta-server"
cp python/uta_usb_transport.py dist/uta-usb-io
chmod +x dist/uta-usb-io
cp dist/uta-usb-io "$stage/uta-usb-io"
cp linux/uta-usb-transport.mjs dist/uta-usb-transport.mjs
cp dist/uta-usb-transport.mjs "$stage/uta-usb-transport.mjs"
cp docs/TESTING.md "$stage/TESTING.md"
cat > "$stage/uta" <<'SH'
#!/bin/sh
set -eu
uta_release=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$uta_release/UTA.app/Contents/Resources/uta" "$@"
SH
chmod +x "$stage/uta"
cp "$stage/uta" dist/uta
if [ -d "$uta_root/dist/UTA.app" ]; then
    previous=$(mktemp -d "$uta_root/build/previous-app.XXXXXX")
    mv "$uta_root/dist/UTA.app" "$previous/UTA.app"
fi
ditto --norsrc "$app" "$uta_root/dist/UTA.app"
ditto -c -k --norsrc "$stage" "$uta_root/dist/UTA-0.1.0-macOS-arm64.zip"
printf '%s\n' "$app" > build/verified-app-path.txt
echo "Signed and verified: $app"
echo "Release: $uta_root/dist/UTA-0.1.0-macOS-arm64.zip"
