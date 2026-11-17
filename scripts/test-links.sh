#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/tests build/LinkWorkspaceTests.app/Contents/{MacOS,Resources}
swiftc -swift-version 5 -module-cache-path build/swift-cache macos/LinkGraph.swift tests/link_graph_tests.swift -o build/tests/link-graph-tests
build/tests/link-graph-tests
cp tests/link_cli_fixture.py build/LinkWorkspaceTests.app/Contents/Resources/uta
chmod +x build/LinkWorkspaceTests.app/Contents/Resources/uta
swiftc -swift-version 5 -D LINK_WORKSPACE_TESTS -parse-as-library -module-cache-path build/swift-cache \
  macos/LinkGraph.swift macos/LinkCanvas.swift macos/Localization.swift tests/LinkCanvasPreview.swift tests/link_workspace_tests.swift \
  -o build/LinkWorkspaceTests.app/Contents/MacOS/LinkWorkspaceTests
build/LinkWorkspaceTests.app/Contents/MacOS/LinkWorkspaceTests
swiftc -swift-version 5 -module-cache-path build/swift-cache macos/Localization.swift tests/localization_tests.swift -o build/tests/localization-tests
build/tests/localization-tests macos/Resources/en.json
