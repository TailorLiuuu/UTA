"""The real macOS inventory must never initialize USB access, even with devices present."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "macOS registry inventory")
class InventoryTests(unittest.TestCase):
    def test_list_cannot_open_usb_or_initialize_libusb(self):
        # Interpose access entrypoints, not the enumeration result. Any attempt
        # to regress to libusb init or a USB user-client open terminates the scan.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "deny_usb.c"
            dylib = Path(tmp) / "deny_usb.dylib"
            source.write_text('''
#include <unistd.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
extern int libusb_init(void *);
extern int libusb_open(void *, void *);
static int denied(void) { _exit(97); }
#define DENY(name) __attribute__((used)) static struct { const void *replacement; const void *original; } \\
    hook_##name __attribute__((section("__DATA,__interpose"))) = {(const void *)denied, (const void *)name};
DENY(libusb_init)
DENY(libusb_open)
DENY(IOServiceOpen)
DENY(IOCreatePlugInInterfaceForService)
''')
            usb = subprocess.check_output(["pkg-config", "--variable=libdir", "libusb-1.0"], text=True).strip()
            subprocess.run(["clang", "-dynamiclib", str(source), "-o", str(dylib),
                            "-L" + usb, "-lusb-1.0", "-framework", "IOKit", "-framework", "CoreFoundation"],
                           check=True, capture_output=True, timeout=30)
            env = dict(os.environ, DYLD_INSERT_LIBRARIES=str(dylib))
            binary = str(ROOT / "build/native/uta-native")
            # A non-list path must hit the trap, proving the interposer loaded.
            control = subprocess.run([binary, "test-denied-init"], env=env, capture_output=True, timeout=10)
            self.assertEqual(control.returncode, 97, control.stderr)
            result = subprocess.run([binary, "list"], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            devices = json.loads(result.stdout)
            self.assertEqual(len({d["busid"] for d in devices}), len(devices))
            for device in devices:
                self.assertIn("registry_id", device)
                self.assertRegex(device["busid"], r"^\d+-\d+(?:[.:]\d+)*$")
                self.assertNotEqual(device["class"], 9)


if __name__ == "__main__":
    unittest.main()
