# Verification — updated 2026-10-21

## Current state: build 13

- 64 Python regression tests and 3 Node USB-adapter tests passed.
- Signed Apple Silicon / macOS 26 App built and installed; strict/deep signature verification passed. The development build is not notarized.
- The Linux agent provides only doctor/list/status/attach/detach. No chip protocols or firmware profiles are bundled.
- ESP32-S3 ROM-mode flashing was verified as described below.
- ML307H flashing and boot were previously verified using a separately optimized project tool. After separation, USB attachment and read-only firmware identity/runtime-marker verification passed; a new full flash was not performed.
- Automatic reconnect supports explicitly configured VID:PID identities; it does not replay firmware commands.
- Historical sections below describe the state at each test date, not current unresolved status. Device identifiers and private server details are omitted from this public copy.

## Initial prototype validation

- Native USB/IP/libusb exporter compiled on Apple Silicon.
- SwiftUI application compiled and launched on macOS 26.
- GUI observed two actual local USB devices, including VID:PID `303a:1001`.
- GUI add/edit/remove server tested using a temporary localhost:1 configuration.
  Connection refusal appeared explicitly; temporary configuration was removed.
- 22 automated tests passed:
  - real TCP requests against the native USB/IP core with a **synthetic** handler:
    enumerate, import, bulk OUT/IN payload roundtrip, unlink, unknown-device rejection;
  - Linux-agent import handshake and sysfs write contract using **fake sysfs**;
  - short TCP reads, errors, VHCI parsing, cross-user record restrictions,
    reused-port detach protection, SSH argument validation, state failure handling,
    and refusal to switch when previous remote release cannot be confirmed.
- App was ad-hoc signed in /private/tmp, archived, extracted, and the extracted
  application passed `codesign --verify --deep --strict`.
- Packaged app contains the native libraries; the Python interpreter is an
  explicit runtime prerequisite rather than silently bundled.

## SSH import and server device selection (2026-10-20)

- Native UI discovered aliases from the real local SSH config.
- Imported one alias, opened its server page, selected both detected USB devices,
  and verified the connect button enabled while release remained disabled.
- Temporary imported entry was removed after verification; no server was contacted.
- Added tests for recursive Includes, cycles, wildcard exclusion, no command execution
  during discovery, empty config, import deduplication and inherited SSH options.

## the test Linux server connection test (2026-10-20)

- Imported the user-selected SSH alias and saved display remark `Development server`.
- SSH login succeeded as root on Linux 5.15.0-173-generic x86_64.
- Installed the bundled helper at `/usr/local/bin/uta-server` and loaded
  `vhci_hcd`. Doctor reported Python 3.10.12 and 16 free virtual USB ports.
- Tested a real SSH reverse tunnel with dynamically allocated loopback port;
  a 37-byte test payload round-tripped from the server to a Mac loopback echo socket.
  The temporary tunnel and echo socket were closed after the check.
- Added regression coverage that a remark-only edit preserves an active route,
  while connection-parameter edits remain blocked during use.
- This test did not claim, reset or flash a physical USB device.

## Physical ESP32 connection diagnosis (2026-10-20)

- Retried the actual Espressif `303a:1001` board at busid `2-1`, including after
  the user reinserted it. SSH reverse forwarding succeeded, but USB/IP import
  failed because the Mac exporter could not detach/claim interface 0:
  `Access denied (insufficient permissions)`.
- IORegistry identified `AppleUSBACMControl` on interface 0 and
  `AppleUSBACMData` on interface 1. The board exposed `/dev/cu.usbmodem2101`
  and `/dev/tty.usbmodem2101`; no visible process had these serial files open.
  Device-level USB clients were observed separately and are not proof of an
  exclusive interface claim.
- Linux doctor still reported ready and 16 free ports, with no leftover sessions.
- Added read-only diagnostics to both device views and `uta diagnose --device`.
  Access-denied import failures now display the concrete local permission cause.
- 38 automated tests passed, including diagnostic matching, ambiguity, process
  parsing, query failures and error classification.
- `sudo -n true` requires a password on this Mac. Privileged local capture and
  full end-to-end physical attachment remain unverified; no firmware was written.

## macOS authorization implementation (2026-10-20)

- Added a bundled SMAppService launch daemon and an explicit enable/disable panel.
  First registration reached `requiresApproval` in the real installed application.
  Canceling the pending registration returned the UI to the disabled state.
  Administrator approval itself is pending user action.
- UI, Python controller and SSH remain unprivileged. The root helper statically
  links the capture code, accepts an exact build-pinned signed XPC client, checks
  the console user and selected device identity, and does not execute commands
  or open a TCP listener.
- 45 automated tests passed, including actual local XPC FileHandle transfer,
  one-byte/short-packet request-response without waiting for buffer fill, EOF
  cleanup, a synthetic USB/IP bulk roundtrip over a connected Unix socket, and
  refusing to capture a serial port visibly opened by another process.
- A real SSH reverse forward to the test Linux server round-tripped a test payload
  through a private Mac Unix socket. This was not a physical USB transfer.
- The helper uses SMAppService APIs available from macOS 13, but this package
  targets Apple Silicon macOS 26. Older macOS versions and Intel are not tested.
  The development package is ad-hoc signed; public distribution still needs a
  stable Developer ID signing/update policy and notarization.

## Authorization timeout repair (2026-10-21, build 7)

- Reproduced the approved-but-unavailable state. launchd rejected the prototype
  helper with `OS_REASON_CODESIGNING / Launch Constraint Violation`; later attempts
  reported `EX_CONFIG`. Code-signature validation separately found two Python
  bytecode cache files added inside the signed application resources.
- Disabled Python bytecode writes in the launcher, inherited child environment,
  and direct controller entrypoint. The app and helper now share Developer ID
  signing with hardened runtime. This local test build is not notarized and uses
  no signing timestamp; it is not yet a public distribution release.
- Re-registering the old service label still failed the cached spawn constraint.
  Unregistered the prototype and migrated to `dev.uta.usb-helper.v1`. The system
  accepted the new registration under the existing approval. No global background
  item reset, SIP changes, or changes to other applications were made.
- Verified the real installed build: UI displays authorized, `uta permission`
  returns `available: true`, and launchctl reports the v1 system daemon running.
  The helper reports privileged readiness only when its effective UID is root.
  After launching and running the CLI, deep strict application signature checks pass.
- Failed health checks now display a service-unavailable state with retry, rather
  than indefinite checking; failed automatic checks are spaced 60 seconds apart.
- 46 regression tests passed, including packaged/direct CLI execution without
  creating bytecode inside the application. No USB device was connected during
  this repair, so this does not establish physical USB passthrough compatibility.

## Physical ESP32 passthrough (2026-10-21, builds 8–9)

- Fixed libusb control-IN responses: control transfers use endpoint zero, so the
  direction must come from setup `bmRequestType`. The old code returned a nonzero
  length without its descriptor payload, leaving Linux enumeration waiting.
- A real GET_DESCRIPTOR through the signed root helper returned all 18 bytes.
- Linux 5.15 masks device/socket identity in the enumerating VHCI row. New agent
  records verify the original live socket inode and loopback tuple during this
  phase, while retaining strict identity checks for older records.
- The actual Espressif `303a:1001` board at Mac busid `2-1` attached through the
  SSH reverse tunnel to the test Linux server. Linux identified Espressif's USB JTAG/serial
  debug unit and created `/dev/ttyACM0` using `cdc_acm`.
- Opened that remote serial port read-only/nonblocking and immediately closed it.
  No firmware command, erase or write was issued.
- 49 automated tests passed, including real libusb callback serialization for
  control IN, OUT and zero-length responses, plus enumerating socket ownership.
- GUI explicitly separates cloud serial/USB paths from Mac local serial paths.
  Captured sessions remain visible even when the ordinary Mac scan no longer
  lists the claimed device; the UI does not infer physical removal from this alone.
- Verified the installed build 9 server page displays `/dev/ttyACM0` with copy
  controls and an actionable disconnect button. Tested that button, then repeated
  attach → read-only serial open/close → disconnect: the cloud serial node was
  removed and the Mac device returned in about 3.4 seconds, without restarting
  the helper. Reconnection also succeeded.

## Physical ESP32-S3 flashing (2026-10-21)

ESP32-S3 flashing, independent verification and boot were subsequently tested:
see [2026-10-21 physical flash report](ESP32-FLASH-2026-10-21.md). This used
esptool `--no-stub`; default-stub bulk Flash reads failed and remain unresolved.

## Remaining hardware validation

- BK7258 identification, interface claiming, flashing and reboot.
- Network interruption during real flashing; automatic flash resume is not implemented.
- Broad device, driver and USB transfer-type compatibility.

ESP32-S3 attachment, ROM-mode flashing and startup are physically verified.
This remains a prototype, not proof of universal USB compatibility.

## Automatic disconnect cleanup (2026-10-21, build 10)

- Reproduced the old stale state: Mac exporter/tunnel had ended, the remote
  session was disconnected with no nodes, but its record remained and the UI
  continued to show an error requiring another user action.
- Added automatic session-specific detach and offline retry, persisting pending
  cleanup across controller restarts. Routing changes are blocked until cleanup
  is confirmed; no USB reconnect or flash retry is automatic.
- 52 tests passed. Regression tests cover exporter exit, remote disconnection,
  protecting a different device, offline retry throttling, recovery after restart,
  and not treating an ordinary-scan omission of a captured device as unplugging.
- Installed signed build 10, restored the authorized helper, and removed the
  previous test session from the test Linux server.
- After the user reinserted ESP32-S3 `303a:1001`, ran three
  actual Mac → the test Linux server attachment cycles. Terminating only its local exporter
  resulted in automatic release in 2.3 seconds; terminating only its SSH tunnel
  took 3.4 seconds; explicitly detaching its cloud session took 4.1 seconds.
  In each case the remote session record disappeared, `/dev/ttyACM0` was absent,
  and local state returned to `local` with no pending cleanup, without a user
  release action. The third attachment exposed its USB node before the test detach;
  serial enumeration had not yet appeared in that snapshot.
- Verified the installed UI displays “连接已断开，云端设备已自动释放；可重新选择接入”,
  zero cloud mounts, and the returned local device with an enabled connect button.
  Left the device local/disconnected. No Flash reads or writes were performed.
- These tests simulate process/tunnel loss and remote detach. Physical cable
  removal while attached and a real network outage/recovery were not reproduced;
  offline retry/restart recovery is covered by the automated tests above.
