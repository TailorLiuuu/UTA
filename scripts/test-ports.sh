#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -m unittest discover -s tests -p test_ports.py -v
mkdir -p build/RemoteDeviceTests.app/Contents/{MacOS,Resources}
cp tests/port_cli_fixture.py build/RemoteDeviceTests.app/Contents/Resources/uta
chmod +x build/RemoteDeviceTests.app/Contents/Resources/uta
swiftc -swift-version 5 -D LINK_WORKSPACE_TESTS -parse-as-library -module-cache-path build/swift-cache \
  macos/LinkGraph.swift macos/LinkCanvas.swift macos/RemoteDevices.swift macos/Localization.swift \
  tests/LinkCanvasPreview.swift tests/remote_device_tests.swift \
  -o build/RemoteDeviceTests.app/Contents/MacOS/RemoteDeviceTests
build/RemoteDeviceTests.app/Contents/MacOS/RemoteDeviceTests "$PWD/build/remote-devices-preview.png"
