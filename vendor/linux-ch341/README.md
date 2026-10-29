# Linux CH341 driver source

Upstream: https://raw.githubusercontent.com/torvalds/linux/v5.15/drivers/usb/serial/ch341.c
License: GPL-2.0 (SPDX header and LICENSE).
SHA-256: 0568953df7e73445ca16e21e685a8c06a3c33891e6976be34e28f6eee7cd1912

The original source is unmodified. `python/uta_ch341.py` checks this hash and
adds `bulk_out_size = 4096`, a read-only identification parameter and module
version. `scripts/build-server.py` embeds the verified compressed source in the
standalone agent. `uta-server tune-ch341 build` writes the complete modified
source and Makefile to the private build directory before compiling it. All
patching/build code is included; no prebuilt kernel module is distributed.
