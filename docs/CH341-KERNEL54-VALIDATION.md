# Linux 5.4 CH341 compatibility validation

Date: 2026-11-05. Included in Preview 12 / Build 24.

## Compatibility change

The pinned GPL-2.0 source remains Linux v5.15 `drivers/usb/serial/ch341.c`.
A direct build on Linux 5.4 failed because `usb_serial_driver.port_remove`
requires `int (*)(struct usb_serial_port *)`, while the pinned implementation
returns `void`. The 5.4 variant adds a wrapper that invokes the original
cleanup and returns zero. It does not cast function pointers, suppress compiler
errors, or change firmware protocols. Both IN and OUT buffers remain 4096 bytes.

- [Linux v5.4 CH341 source and integer remove callback](https://github.com/torvalds/linux/blob/v5.4/drivers/usb/serial/ch341.c)
- [Pinned Linux v5.15 CH341 source](https://github.com/torvalds/linux/blob/v5.15/drivers/usb/serial/ch341.c)
- [Building modules for the target kernel](https://docs.kernel.org/kbuild/modules.html)

Only the 5.4 and 5.15 kernel families are allowed. Linux 5.15 generated source
is byte-identical to the previous version. Unsupported families remain blocked.
The single-file server agent bundles and verifies the pinned source and generates
the correct variant using the running kernel version. No runtime source download.

## Verified on a Linux host

Environment: Ubuntu 20.04.6, Linux `5.4.0-162-generic`, existing kernel headers
and compiler, kernel lockdown inactive. No physical CH340 ports or UTA sessions
were present during driver switching.

- Native build succeeded after the callback adapter was applied; module kernel
  version, revision and SHA-256 passed the existing checks.
- Load succeeded: `state=enabled`, `supported=true`, both buffer parameters 4096.
- A second enable was idempotent.
- Disable removed the tuned module and loaded the system CH341 driver.
- Re-enable succeeded and both parameters remained 4096.
- The original system driver file's SHA-256 was unchanged.
- Kernel logs showed expected register/deregister events during the checked interval.
- Kernel version remained unchanged; no reboot, package upgrade, startup entry,
  sudoers change or system driver replacement was performed.

An isolated `0.1.0-preview.12-dev` server agent containing only this compatibility
change was installed on the test host after backing up the previous agent. It
does not include the separate, unreleased one-click setup feature. The tuned
module was left enabled for this boot; a reboot returns to the system driver.

The development tree passed 131 Python tests; the isolated release passed
115 Python tests and 3 Node tests, including both source
variants, standalone bundling, unsupported-kernel rejection, busy-port blocking,
load failure recovery and USB assignment protection.

## Follow-up: physical USB attachment

After a separately authorized VHCI capacity expansion on the same running
5.4.0-162-generic kernel, a physical CH340 (`1a86:7523`, full speed) was attached
through the installed UTA Mac App. It used global VHCI port 16 on the second
controller and enumerated as a ttyUSB node bound to `ch341_uta`.

The App reported `attached` continuously for at least 30 seconds with zero
monitor failures. Both driver buffer parameters stayed at 4096. The eight
pre-existing USB/IP devices and their autoimport service remained connected;
seven USB 2.0 slots remained free. The test did not open the serial device,
write protocol commands, or flash firmware. The USB connection was left attached
for the user's testing, with no serial reader process occupying the port.

The VHCI capacity change and CH341 tuning are temporary module loads for this
boot, not a kernel upgrade or verified persistent startup configuration.

## Not yet verified

The initial build/load/restore checks were empty-port tests; the follow-up
verified physical USB enumeration and a short stable connection. Serial RX/TX,
DTR/RTS behavior, disconnect/reconnect, flashing success, throughput
and log completeness still require a separately authorized hardware test.
No speed or loss-free guarantee follows from these module checks. Other Linux
5.4 distributions have not been individually validated.

## Recovery

After disconnecting all CH340/CH341 devices, an administrator can run:

```sh
/usr/local/bin/uta-server tune-ch341 disable
/usr/local/bin/uta-server tune-ch341 status
```

The original driver is retained. Re-enabling requires a Preview 12 or newer
agent and a matching built module: `uta-server tune-ch341 enable`.

The separate host-specific VHCI capacity module is not bundled or installed by
this Release. Updating UTA does not automatically expand server USB/IP ports.
