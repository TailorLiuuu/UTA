# Third-party code

This prototype uses the documented Linux USB/IP protocol. It contains no
VirtualHere or USB Network Gate proprietary code.

- Linux 5.15 CH341 driver, GPL-2.0, complete pinned source and license in
  `vendor/linux-ch341`. Optional server tuning builds a separate kernel module;
  it is not linked into the Mac app. Source modifications and reproducible build
  logic are in `python/uta_ch341.py`; no prebuilt kernel module is shipped.
  Upstream: https://github.com/torvalds/linux/blob/v5.15/drivers/usb/serial/ch341.c

- Sparkle 2.10.0, https://github.com/sparkle-project/Sparkle, permissive license
  with third-party notices in the bundled `SPARKLE_LICENSE.txt`.
  Official binary archive pinned and SHA-256 verified by `scripts/fetch-sparkle.sh`.
  Source: https://github.com/sparkle-project/Sparkle/tree/2.10.0

- usbipdcpp, https://github.com/yunsmall/usbipdcpp
  revision `7198c4b2547534599a6e7b92b1f67a0d0c4910fa`, LGPL-3.0.
  Source is included in `vendor/usbipdcpp`, linked as a replaceable dynamic
  library. Our CMake wrapper builds the core and libusb backend only.
  See `docs/CHANGES.md` for any local changes.
  The local privileged helper also statically links this core; its complete
  application source, modified library source and relinking build steps are
  provided in the source package (`scripts/build.sh`, target `uta_capture`).
- Asio headers copied from the existing ESP-IDF component checkout.
  Boost Software License 1.0; `vendor/ASIO_LICENSE.txt`.
- spdlog headers copied from the existing ESP-IDF component checkout.
  MIT; `vendor/SPDLOG_LICENSE.txt`.
- libusb 1.0.30, https://github.com/libusb/libusb, LGPL-2.1-or-later.
  The application bundles the locally installed dynamic library, replaceable
  in `Contents/Resources/bin`. Upstream corresponding source:
  https://github.com/libusb/libusb/releases/tag/v1.0.30
  The source/build package contains our application source and build scripts.
  The privileged helper statically links the same libusb version to avoid
  loading user-writable third-party code after authorization. Obtain its
  corresponding source from the release above and replace the library used
  by pkg-config before rebuilding the helper with `scripts/build.sh`.

Local application and agent sources are supplied for review and modification;
no commercial redistribution license is chosen for these new files yet.
Do not assume an existing vendor's SDK redistribution rights apply here.
