import json
import os
from pathlib import Path
import socket
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import uta
import uta_privileged
import uta_server

ROOT = Path(__file__).resolve().parents[1]


class PermissionTests(unittest.TestCase):
    def test_packaged_cli_does_not_mutate_signed_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp) / "UTA.app/Contents/Resources"
            (resources / "python").mkdir(parents=True)
            for source in (ROOT / "python").glob("*.py"):
                shutil.copy2(source, resources / "python" / source.name)
            shutil.copy2(ROOT / "scripts/uta", resources / "uta")
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
            for command in (["/bin/sh", str(resources / "uta"), "--help"],
                            [sys.executable, str(resources / "python/uta.py"), "--help"]):
                result = subprocess.run(command, env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(resources.rglob("*.pyc")), [])
                self.assertEqual(list(resources.rglob("__pycache__")), [])

    def test_missing_helper_is_unavailable(self):
        with patch.object(uta_privileged, "CLIENT", Path("/no-helper")):
            self.assertFalse(uta_privileged.status()["available"])

    def test_version_mismatch_is_not_authorized(self):
        with tempfile.NamedTemporaryFile() as file:
            response = subprocess.CompletedProcess([], 0, '{"available":true,"protocol":99}', "")
            with patch.object(uta_privileged, "CLIENT", Path(file.name)), \
                 patch.object(uta_privileged.subprocess, "run", return_value=response):
                self.assertFalse(uta_privileged.status()["available"])

    def test_bound_device_requests_authorization_before_exporting(self):
        with tempfile.TemporaryDirectory() as home, patch.object(uta, "HOME", Path(home)), \
             patch.object(threading.Thread, "start"):
            controller = uta.Controller()
            host = uta.validate_server(dict(name="lab", target="lab"))
            controller.devices = [dict(busid="2-1", name="ESP", vidpid="303a:1001",
                                       access=dict(state="driver_bound"))]
            with patch.object(uta_privileged, "status", return_value=dict(available=False)), \
                 patch.object(uta.subprocess, "Popen") as launch, patch.object(uta, "remote_json") as remote:
                controller.connect("2-1", host)
            self.assertEqual(controller.sessions["2-1"]["state"], "permission_required")
            launch.assert_not_called()
            remote.assert_not_called()

    def test_bad_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as home, patch.object(uta, "HOME", Path(home)), \
             patch.object(threading.Thread, "start"):
            controller = uta.Controller()
            with self.assertRaises(ValueError):
                controller.handle(dict(action="connect", busid="2-1", access_mode="sudo", server_id="bad"))

    def test_visible_serial_owner_is_not_seized_even_with_authorized_mode(self):
        with tempfile.TemporaryDirectory() as home, patch.object(uta, "HOME", Path(home)), \
             patch.object(threading.Thread, "start"):
            controller = uta.Controller()
            host = uta.validate_server(dict(name="lab", target="lab"))
            controller.devices = [dict(busid="2-1", name="ESP", vidpid="303a:1001",
                                       access=dict(state="process_busy"))]
            with patch.object(uta_privileged, "status") as permission, \
                 patch.object(uta.subprocess, "Popen") as launch, patch.object(uta, "remote_json") as remote:
                controller.connect("2-1", host, access_mode="authorized")
            self.assertEqual(controller.sessions["2-1"]["state"], "error")
            self.assertIn("其他程序", controller.sessions["2-1"]["message"])
            permission.assert_not_called()
            launch.assert_not_called()
            remote.assert_not_called()


class ConnectedStreamTests(unittest.TestCase):
    def test_libusb_control_in_has_payload_out_and_no_data_have_none(self):
        local, peer = socket.socketpair()
        proc = subprocess.Popen([str(ROOT / "build/native/uta-test-fixture"), "--libusb-control"],
                                stdin=peer, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        peer.close()
        try:
            local.settimeout(3)
            first = uta_server.receive(local, 48)
            self.assertEqual(struct.unpack("!I", first[24:28])[0], 18)
            self.assertEqual(uta_server.receive(local, 18), bytes(range(1, 19)))
            second = uta_server.receive(local, 48)
            self.assertEqual(struct.unpack("!I", second[4:8])[0], 2)
            third = uta_server.receive(local, 48)
            self.assertEqual(struct.unpack("!I", third[4:8])[0], 3)
            self.assertEqual(local.recv(1), b"")
            self.assertEqual(proc.wait(timeout=3), 0)
        finally:
            local.close()
            if proc.poll() is None: proc.kill()
            proc.communicate()

    @unittest.skipUnless(sys.platform == "darwin", "macOS XPC")
    def test_xpc_short_packets_are_not_buffered_until_full_or_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = str(Path(tmp) / "xpc-fixture")
            subprocess.run(["swiftc", "-swift-version", "5", "-parse-as-library",
                            "-module-cache-path", str(ROOT / "build/swift-cache"),
                            str(ROOT / "macos/USBHelperProtocol.swift"),
                            str(ROOT / "tests/xpc_stream_fixture.swift"), "-o", binary],
                           capture_output=True, text=True, check=True, timeout=30)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_authorized_stream_import_bulk_and_release_without_tcp_listener(self):
        local, peer = socket.socketpair()
        proc = subprocess.Popen([str(ROOT / "build/native/uta-test-fixture"), "--stream"],
                                stdin=peer, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        peer.close()
        try:
            self.assertEqual(proc.stdout.readline().strip(), b"ready")
            local.settimeout(3)
            local.sendall(struct.pack("!HHI32s", 0x111, 0x8003, 0, b"99-1"))
            uta_server.header(local, 3)
            uta_server.receive(local, 312)
            data = b"authorized socketpair USB/IP" * 128
            submit = lambda seq, direction, ep, size: struct.pack(
                "!IIIIIIIIII8s", 1, seq, 0x10001, direction, ep, 0, size, 0, 0xffffffff, 0, b"\0" * 8)
            local.sendall(submit(1, 0, 2, len(data)) + data)
            reply = struct.unpack("!IIIIIIIIII8s", uta_server.receive(local, 48))
            self.assertEqual(reply[5:7], (0, len(data)))
            local.sendall(submit(2, 1, 1, len(data)))
            uta_server.receive(local, 48)
            self.assertEqual(uta_server.receive(local, len(data)), data)
            local.close()
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            local.close()
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
