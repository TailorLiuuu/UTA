# libusb used by UTA on macOS

`libusb-1.0.30.tar.bz2` is the unmodified upstream source archive (LGPL-2.1-or-later).
Upstream: https://github.com/libusb/libusb/releases/tag/v1.0.30
SHA-256: `fea36f34f9156400209595e300840767ab1a385ede1dc7ee893015aea9c6dbaf`.

`darwin-interface-numbers.patch` changes the internal Darwin interface bitmap
from a scalar to 256 bits and supports every `bInterfaceNumber` value. It also
widens iteration variables so interface 255 cannot cause an infinite loop.
Configuration changes and device capture/reset preserve the complete bitmap.
Public libusb API signatures and non-Darwin backend limits are unchanged.

Build with `bash scripts/build-libusb.sh`; test with `bash scripts/test-libusb.sh`.
The test uses synthetic handles and never initializes or opens USB hardware.
It checks actual core API bounds, idempotent claims, sparse bitmap independence,
copy/restore and cleanup, with AddressSanitizer and UndefinedBehaviorSanitizer
on the test and inline bitmap operations.

The full App build consumes the resulting shared and static libraries from
`build/libusb/install`. To modify/relink, edit this patch or the build script,
then run `scripts/build.sh` with your own Developer ID signing identity. No
Homebrew library is modified. The signed helper must be reauthorized after
replacement; finish active USB sessions first.
