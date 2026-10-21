# Third-party code

This prototype uses the documented Linux USB/IP protocol. It contains no
VirtualHere or USB Network Gate proprietary code.

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
