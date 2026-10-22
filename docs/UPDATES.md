# App updates

UTA build 15 adds Sparkle 2.10.0. The sidebar and application menu include
“检查更新”; automatic checks can be disabled in the sidebar. The standard
Sparkle window presents the version, download progress, installation and relaunch.
Automatic installation and system-profile submission are disabled.

Preview 2 and older do not have an updater: install the new DMG once manually.
Updates affect the Mac App, not Linux agents, user settings or project firmware.

The controller atomically reserves an idle instance during an update cycle.
Active exports, operations, reconnect deadlines and pending remote cleanup block
updates. New CLI operations are refused while reserved. Cancelling or failing the
check releases the reservation; a dead UI process also releases it. Before app
termination the controller rechecks idle state, stops, and the UI waits for its
exit. A failed shutdown cancels application termination instead of replacing a
live exporter. USB connections are not automatically restored after an update.

## Publishing

1. Increment `CFBundleVersion` in `scripts/build.sh` for every update. Sparkle uses
   this integer, not the GitHub title, to decide whether an update is newer.
2. Build with the Developer ID signing identity and secure timestamp, then run
   `scripts/package-macos.sh`. The build fetches the fixed official Sparkle archive
   and verifies its pinned SHA-256. The framework's nested executables are signed.
3. Generate the feed with `python3 scripts/make-appcast.py vX.Y.Z-preview.N`.
   The `uta-updates` Ed25519 private key is in the publisher's macOS Keychain;
   only the public key is in the application. Never commit or upload the private
   key. Keep a secure backup separately; losing it affects future update signing.
4. Create the corresponding GitHub Release and upload the exact DMG used in step 3,
   ZIP, corresponding source, third-party source and SHA256SUMS. Verify all assets.
5. Publish the Release **before** pushing `updates/appcast.xml` to `main`. The feed
   is served by raw.githubusercontent.com. Never advertise a draft/missing asset.
   A source archive can precede the feed-only commit; all executable source must
   match the Release tag. Do not modify the signed feed after generation.

Sparkle verifies the EdDSA archive signature and the application's signing
identity. HTTPS and the SHA256SUMS file alone are not the update trust mechanism.
The App remains Developer ID signed but not notarized; publishing updates does
not change that limitation. Downloads and installation errors stay in the update
window for retry. Permissions on a system-owned installation may require macOS
administrator authorization.

Reference: https://sparkle-project.org/documentation/
