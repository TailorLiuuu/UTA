#!/bin/bash
set -euo pipefail
uta_root=$(cd "$(dirname "$0")/.." && pwd)
uta_source="$uta_root/vendor/libusb/libusb-1.0.30.tar.bz2"
uta_patch="$uta_root/vendor/libusb/darwin-interface-numbers.patch"
uta_build="$uta_root/build/libusb"
uta_prefix="$uta_build/install"
uta_expected=fea36f34f9156400209595e300840767ab1a385ede1dc7ee893015aea9c6dbaf
test "$(shasum -a 256 "$uta_source" | cut -d ' ' -f 1)" = "$uta_expected"
uta_digest=$(cat "$uta_source" "$uta_patch" "$0" | shasum -a 256 | cut -d ' ' -f 1)
if [ -f "$uta_prefix/lib/libusb-1.0.a" ] && [ -f "$uta_prefix/lib/libusb-1.0.0.dylib" ] &&
   [ "$(cat "$uta_build/source-digest" 2>/dev/null || true)" = "$uta_digest" ]; then
    exit 0
fi
mkdir -p "$uta_build"
rm -rf "$uta_build/libusb-1.0.30" "$uta_prefix"
tar -xjf "$uta_source" -C "$uta_build"
cd "$uta_build/libusb-1.0.30"
patch -p1 < "$uta_patch"
CFLAGS="-O2 -mmacosx-version-min=14.0 -ffile-prefix-map=$uta_root=/uta-src" \
    ./configure --disable-dependency-tracking --enable-shared --enable-static --prefix="$uta_prefix"
make -j 6
make install
printf '%s\n' "$uta_digest" > "$uta_build/source-digest"
