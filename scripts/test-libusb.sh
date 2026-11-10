#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/build-libusb.sh
clang -std=gnu11 -fsanitize=address,undefined -g \
    -I build/libusb/libusb-1.0.30 -I build/libusb/libusb-1.0.30/libusb \
    tests/libusb_interface_mask.c build/libusb/install/lib/libusb-1.0.a \
    -lobjc -framework IOKit -framework CoreFoundation -framework Security \
    -o build/test-libusb-interface-mask
build/test-libusb-interface-mask
