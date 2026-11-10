# Changes

## Build 28 — v0.1.0-preview.16 · 2026-11-10

- Build bundled libusb 1.0.30 from hash-pinned source with a Darwin-only patch
  supporting interface numbers 0–255. A 256-bit bitmap replaces 32-bit shifts;
  endpoint lookup, configuration changes and capture/reset restoration retain
  high-numbered interfaces. Both the App and privileged helper use this library.
- Forward import-stage/interface/error diagnostics through each authorized
  session’s own log pipe. Failed claims no longer rely only on a generic
  permission/ownership message. Concurrent exports do not share log sinks.
- Drain final helper diagnostics briefly at stream EOF, outside the data path.
- Validation: 179 Python tests, 3 Node adapter tests, sanitizer-backed interface
  bitmap/core bounds tests and concurrent native diagnostic tests passed.
  EC600M descriptors showed interfaces 0,1,2,3,4,32; live import remains unverified
  because an existing USB session was explicitly preserved. No new flashing or
  throughput result is claimed.
- Updating requires all local USB sessions to finish, then an App update and
  USB helper disable/re-enable. Cloud CLI is unchanged at Preview 15.

## Build 27 — v0.1.0-preview.15 · 2026-11-09

- Opt new App connections into a 60-second cloud session lease. An independent
  watchdog releases the matching owned socket/port when client status heartbeats
  stop; other clients and reused ports are left alone. Older attachments opt out.
- The watchdog does not proxy USB data or add per-transfer processing. Existing
  flashing protocols remain external to UTA.
- Validation: 177 Python tests passed; a detached Linux watchdog using fake sysfs
  expired after 60.11 seconds without affecting a second session.
- An ML307 hardware run through UTA completed backup, AP writes, bootloader
  restoration and full readback in 171.76 seconds, then reconnected in runtime
  mode. The roughly 11x CP read improvement came from larger protocol blocks and
  bounded USB queues in the external project flasher, not an App-wide speed switch.
  Phone/audio functions and physical unplug during writes were not tested.


## Build 25 — v0.1.0-preview.13 · 2026-11-06

- CH340/CH341 发送缓冲和 FIFO 扩为至少 8192 字节，保留 4096 字节接收缓冲，并避免 TTY 层 2 KiB 拆包；Linux 5.4/5.15 共用此传输优化。
- Linux 5.4 上以官方工具完成一轮 921600 烧录：约 4.72 MB 镜像全程 107 秒，写入 103 秒；额外尾部读回一致。本次不声称长期稳定性或固件功能通过。
- 服务器页面新增 USB 优化检测、一键启用、管理员授权终端和配置日志；中英文界面同步提供。
- Mac CLI 新增 `uta server optimize ID [--interactive]`；云端 `tune-ch341 setup --install-dependencies` 可准备缺失依赖。
- 启用前后检查设备占用，构建成功后才切换已识别的旧驱动；读取 IN / OUT 实际状态后才显示成功。
- 保持通用 USB 透传，不加入芯片烧录协议或修改外部工具配置；当前优化限 Linux 5.4/5.15 CH340/CH341。

## Build 24 — v0.1.0-preview.12 · 2026-11-05

- 新增 Linux 5.4 CH340/CH341 加速适配：兼容旧版端口卸载回调，使用当前内核头文件构建；5.15 生成源码保持不变。
- 5.4.0-162-generic 已完成编译、加载、系统驱动恢复、再次启用及真实 CH340 接入验证，收发缓冲均为 4096 字节；连接观察 30 秒无监控错误。未进行新的烧录或日志完整性验证。
- 云端代理版本为 0.1.0-preview.12。需显式更新云端 CLI 并启用加速；不升级服务器内核、不覆盖系统驱动，不包含虚拟 USB 端口扩容模块。

## Build 23 — v0.1.0-preview.11 · 2026-11-05

- 已分配 USB 在其他服务器页面不可勾选，并显示当前归属。等待重连或清理期间保持锁定，完成显式断开后才能重新分配。
- 设备总览锁定已分配设备的目标服务器，移除隐式切换；后台状态变化时清除不再可选的勾选项。
- CLI 与后台接入同样拒绝抢占其他服务器的设备，拒绝操作不清理或修改原连接；串行保留新接入请求，避免并发抢占。

## Build 22 — v0.1.0-preview.10 · 2026-11-05

- 服务器页提示云端 CLI 更新，比较 App 自带代理与云端的版本及 SHA-256；检查只读，用户点击后才安装并核验。
- 设备接入、等待重连或清理时禁止更新，更新期间禁止新接入；支持中英文提示和手动安装。
- Mac App 升级不会自动更新云端代理；自带代理未变化时不重复提示更新。

## Build 21 — v0.1.0-preview.9 · 2026-11-04

- macOS USB 列表只读取 IOKit 系统缓存，不初始化 libusb、不打开 USB 或串口，避免后台刷新短暂申请独占访问。
- 修复 USB Hub 下设备占用诊断漏报，按系统注册节点区分同型号设备。
- 手动接入、自动重连前重新检查串口占用；云端检查期间端口被本地程序打开也停止接管。

## Build 20 — v0.1.0-preview.8 · 2026-11-02

- Increase both CH340 USB/IP receive and transmit buffers to 4096 bytes. Verify
  both read-only module parameters before reporting receive tuning as enabled;
  older OUT-only modules cannot be mistaken for the fixed driver.
- Document upgrading the Linux agent/driver and using the original BK7259 CLI's
  timing configuration. Chip protocols and firmware remain outside UTA.
- Physical validation: three matching 64 KiB reads and one complete 4.59 MB
  download with the official CLI at 921600 baud, taking 162.84 seconds. Official
  success, restored Flash protection and extra first/last readback passed.
  Firmware functions and universal USB compatibility were not evaluated.
- Remove private test identifiers from documentation and map local build paths
  to neutral prefixes in compiled code. Test firmware, raw logs, SSH settings
  and credentials are excluded from the release.

## Build 19 — v0.1.0-preview.7 · 2026-10-29

- Add opt-in Linux 5.15 CH340 transmit-buffer tuning to the single-file server
  CLI: status, build, enable and restore the system driver. Bundle verified GPL
  source; no runtime source downloads, persistent install or protocol changes.
- Refuse switching while any CH340 tty is attached, reject unknown modules and
  unsupported kernels, verify built artifacts, and restore the system driver
  after load failure. Kernel loading requires a full administrator root shell.
- Show acceleration state and a bilingual setup guide in the Mac server panel.
- Release CLI was physically tested on a Linux test server: build, enable, idempotent enable,
  disable/system-driver restore, and re-enable all passed. 96 Python tests,
  3 Node tests and 309 language entries passed.
- Physical pre-release tuning test: final 2.56 MB resume completed in 145.252 s,
  full image CRC and preserved regions passed; write/sector-check stage averaged
  30.0 KB/s. Prior page path was about 1.6 KB/s. Firmware functions not evaluated
  for release. App upgrade alone does not enable Linux tuning.

## Build 18 — v0.1.0-preview.6 · 2026-10-27

- Keep the active USB tunnel through transient control-SSH status failures.
  Throttle status checks to at least 10-second intervals, with at least three failures and
  30 seconds of failure grace before releasing an unconfirmed connection.
- Continue checking exporter/tunnel process exits on every watch cycle. An
  explicit remote disconnection still triggers cleanup without the probe grace.
- Show an amber unconfirmed-state warning and label ports as last confirmed
  while retrying. Clear the warning on a successful check.
- Record bounded connection-event logs with state transitions, probe failures,
  recovery and transport exit codes. Flash protocols remain external to UTA.

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
