#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build
archive=build/sparkle.tar.xz
expected=c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c
if [ ! -f "$archive" ]; then
    curl -fL --retry 2 https://github.com/sparkle-project/Sparkle/releases/download/2.10.0/Sparkle-2.10.0.tar.xz -o "$archive.download"
    mv "$archive.download" "$archive"
fi
printf '%s  %s\n' "$expected" "$archive" | shasum -a 256 -c -
mkdir -p build/sparkle
tar -xJf "$archive" -C build/sparkle
