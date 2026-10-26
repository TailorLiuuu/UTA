# Changes

## Build 17 — v0.1.0-preview.5 · 2026-10-26

- Hide completed sessions for absent USBs from the device cards after unplug/replug.
  Keep captured devices, pending cleanup, busy operations and reconnection visible.
- Device counts, cards and empty state use the same missing-session selection.
- Build outputs no longer accumulate loose App copies in dist or previous-app
  folders; packaging stages are excluded from indexing and DMG staging is cleaned.

## Build 16 — v0.1.0-preview.4 · 2026-10-22

- Settings gear at the bottom-left groups Chinese/English language selection and automatic updates.
- Language changes persist and immediately update UTA views; native system and
  Sparkle windows follow the preference on the next launch.
- Scheduled updates use a non-modal home banner and do not reserve USB.
  Clicking Update now retains the idle check and safe-restart protections.
- Added presentation-only English translations, preserving device names, paths,
  SSH commands and protocol values.

## Build 15 — v0.1.0-preview.3 · 2026-10-22

- Added Sparkle updates: automatic checks, manual check button, signed downloads,
  installation and relaunch. No silent installation or system-profile submission.
- Idle-controller reservation prevents updates from interrupting USB exports,
  reconnect waits or remote cleanup; relaunch waits for the old daemon to exit.
- Pinned the Sparkle dependency and added signed appcast publishing instructions.

## Build 14 — v0.1.0-preview.2 · 2026-10-22

- Server and overview sidebar rows accept clicks across their full area.
- New USB mode identities prompt for confirmation before reconnecting. Decisions
  are revalidated against a fresh USB scan and expire with the reconnect window.
- Confirmed modes persist per server, original USB port and original identity.
  Advanced settings can inspect, edit or forget them; restart never claims devices.

## Build 13 — generic USB boundary

- Removed the firmware command, bundled chip protocols, firmware profiles and
  Node runtime dependency from the routing agent and release package.
- The ML307 project workflow is a separate tool outside the UTA source/release.
- Reconnection now uses user-configured VID:PID identities, without built-in
  chip mode families. Route IDs and generic USB transport remain available.

## Build 12 (superseded)

- Added stable route IDs. The temporary integrated flasher was removed in build 13.

## Build 11 — mode reconnection and Linux USB transactions

- Added bounded reconnection to the same server and physical USB slot, with identity
  checks and a mode identity list (user-configured from build 13). Manual release cancels intent.
- Cleanup must finish first; waits expire after 120 seconds and at most three attempts.
  Controller restart restores cleanup only. UI and CLI expose the setting and state.
- Added `uta-usb-io` and a Node transport adapter. The optional Linux worker posts IN
  before queuing the OUT packets of one command, checking every completion.
  It never pipelines separate commands or retries uncertain writes. Protocol tools
  must explicitly adopt this API; generic USB/IP does not reinterpret firmware.

## Build 10 — automatic disconnect cleanup

- Exporter exit, tunnel loss and remote device removal now close local transport
  and explicitly detach the corresponding remote session, without a manual release.
- Offline cleanup retries every 15 seconds and persists across controller restart;
  pending routes cannot be edited/deleted or switched until cleanup is confirmed.
- A completed disconnection is shown as disconnected/local, with its cause retained
  in device details. Ended device cards no longer ask users to release a record.
- USB capture disappearing from the ordinary Mac scan alone never triggers cleanup.
- Reconnecting a USB device remains an explicit user action; flashing is not resumed.

- `usbipdcpp` is copied from the pinned revision in THIRD_PARTY.md.
- Initially no modifications to upstream source; custom host executable and
  CMake build are in `native/`.
- Export is limited to one explicitly selected device per native process and
  binds to loopback only. Hotplug auto-bind is disabled.
- Native exporter exits if its owning daemon exits.
- Added `Server::start_stream` and `LibusbServer::start_stream` to consume an
  already-connected private stream without a TCP listener. Existing TCP mode remains.
- Added the root helper's statically linked capture entry point, XPC client
  signature validation, selected-device identity check and lifetime-based release.
  The helper does not spawn executables or accept filesystem paths over IPC.

## Build 7 — 2026-10-21

- Fixed signed app resources being modified by Python bytecode caches.
- Added explicit Developer ID build signing, hardened runtime, and migration from
  the prototype service record to the stable `dev.uta.usb-helper.v1` record.
- Verified authorized helper startup on the actual Mac. Failed checks now end in
  a clear unavailable state and retry at a lower rate.

## Builds 8–9 — 2026-10-21

- Corrected upstream libusb control-IN direction detection so USB descriptor
  payloads reach the remote kernel.
- Track the original live socket while Linux VHCI enumeration masks its identity.
- Show cloud serial and USB nodes with copy buttons and their server name.
- Keep captured sessions visible when the Mac's ordinary device scan omits them;
  distinguish this from a disconnected device and retain the release action.
- Verified physical ESP32 attachment to the test Linux server and read-only opening of
  `/dev/ttyACM0`. Firmware flashing remains untested.
