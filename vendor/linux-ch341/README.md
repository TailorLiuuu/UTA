# Linux CH341 driver source

Upstream: https://raw.githubusercontent.com/torvalds/linux/v5.15/drivers/usb/serial/ch341.c
License: GPL-2.0 (SPDX header and LICENSE).
SHA-256: 0568953df7e73445ca16e21e685a8c06a3c33891e6976be34e28f6eee7cd1912

The original source is unmodified. `python/uta_ch341.py` checks this hash and
adds `bulk_in_size = 4096`, `bulk_out_size = 4096`, read-only identification
parameters and a module version. For a Linux 5.4 build, it also adds an integer
`port_remove` wrapper that runs the upstream void cleanup function and returns
zero, matching the 5.4 USB serial API. The 5.15 generated source is unchanged. `scripts/build-server.py` embeds the verified compressed source in the
standalone agent. `uta-server tune-ch341 build` writes the complete modified
source and Makefile to the private build directory before compiling it. All
patching/build code is included; no prebuilt kernel module is distributed.
