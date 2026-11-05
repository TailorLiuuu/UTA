# App updates

UTA build 15 adds Sparkle 2.10.0. The settings gear and application menu include
“检查更新”; automatic checks can be disabled in Settings. From build 16, scheduled
checks show a home-page banner without blocking USB or opening a dialog. The standard
Sparkle window presents the version, download progress, installation and relaunch.
Automatic installation and system-profile submission are disabled.

Preview 2 and older do not have an updater: install the new DMG once manually.
Updates affect the Mac App, not Linux agents, user settings or project firmware.

The controller atomically reserves an idle instance during a user-initiated update cycle.
Scheduled checks do not reserve the controller.
Active exports, operations, reconnect deadlines and pending remote cleanup block
updates. New CLI operations are refused while reserved. Cancelling or failing the
check releases the reservation; a dead UI process also releases it. Before app
termination the controller rechecks idle state, stops, and the UI waits for its
exit. A failed shutdown cancels application termination instead of replacing a
live exporter. USB connections are not automatically restored after an update.

## Cloud agent update prompt (Build 22)

The Mac server page checks the cloud CLI against the exact agent bundled with
this App. Selecting a server checks when its last result is over five minutes
old; the connection-check icon forces a fresh check. The check only reads the
remote version and SHA-256. It never installs or changes USB routing.

A matching file displays “云端代理已与本机提供的版本一致”. A recognized older
version displays “云端代理有更新”; an unrecognized or same-version modified file
is labeled as different, not assumed older. A recognized newer cloud version
is not offered a downgrade. A failed checksum check stays unknown rather than
being reported as current, without changing an otherwise ready connection.

The update button shows the cloud and bundled versions. Clicking it uses the
existing authenticated SSH installation flow and verifies the installed file
against the bundled SHA-256. Active USB sessions, reconnect waits and pending
cleanup on this Mac block installation; new connections are blocked while it
runs. Root/noninteractive sudo is still required. The manual installation path
remains available after disconnecting devices when automatic authorization fails.

This is a prompt in the Mac App, not a cloud self-updater. It compares with the
bundled agent, not an independently downloaded latest server release. Mac-only
updates do not prompt a cloud update when the bundled CLI is unchanged.

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
