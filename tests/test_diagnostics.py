import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import uta_diagnostics as diag


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.device = dict(busid="2-1", vidpid="303a:1001", serial="test")
        self.node = {
            "idVendor": 0x303a, "idProduct": 0x1001, "USB Serial Number": "test",
            "IORegistryEntryChildren": [
                {"bInterfaceNumber": 0, "UsbExclusiveOwner": "AppleUSBACMControl"},
                {"bInterfaceNumber": 1, "IORegistryEntryChildren": [
                    {"IOClass": "AppleUSBACMData", "IORegistryEntryChildren": [
                        {"IOCalloutDevice": "/dev/cu.usbmodem1", "IODialinDevice": "/dev/tty.usbmodem1"}]}]},
                {"bInterfaceNumber": 2},
                {"IOObjectClass": "AppleUSBHostDeviceUserClient", "IORegistryEntryName": "browser"},
            ]}

    def test_driver_binding_is_reported_even_without_serial_process(self):
        with patch.object(diag.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="")) as run:
            result = diag.inspect_device(self.device, [self.node])
        self.assertEqual(result["state"], "driver_bound")
        self.assertEqual(result["interfaces"][0]["owner"], "AppleUSBACMControl")
        self.assertEqual(result["interfaces"][1]["drivers"], ["AppleUSBACMData"])
        self.assertEqual(result["processes"], [])
        self.assertEqual(result["clients"], ["browser"])
        self.assertEqual(run.call_args.args[0][-2:], ["/dev/cu.usbmodem1", "/dev/tty.usbmodem1"])

    def test_ambiguous_boards_do_not_report_other_devices_owners(self):
        with patch.object(diag.subprocess, "run") as run:
            result = diag.inspect_device(self.device, [self.node, self.node])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["ports"], [])
        run.assert_not_called()

    def test_lsof_records_only_requested_ports_and_processes(self):
        text = "p234\ncserial-monitor\nu501\nf3\nn/dev/cu.usbmodem1\np999\ncother\nu0\nn/dev/other\n"
        with patch.object(diag.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=text, stderr="")):
            result = diag.inspect_device(self.device, [self.node])
        self.assertEqual(result["state"], "process_busy")
        self.assertEqual(result["processes"], [dict(pid=234, command="serial-monitor", user="501",
                                                   ports=["/dev/cu.usbmodem1"])])

    def test_failed_process_scan_is_not_reported_as_clear(self):
        node = dict(self.node, IORegistryEntryChildren=[
            {"IOCalloutDevice": "/dev/cu.usbmodem1"}])
        with patch.object(diag.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="permission denied")):
            result = diag.inspect_device(self.device, [node])
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["errors"])

    def test_registry_failure_preserves_unknown(self):
        with patch.object(diag.subprocess, "run", side_effect=OSError("not allowed")):
            result = diag.inspect_devices([self.device])
        self.assertEqual(result["2-1"]["state"], "unknown")
        self.assertIn("not allowed", result["2-1"]["errors"][0])

    def test_local_error_is_used_only_for_failed_import(self):
        rejected = "USB/IP request rejected (version=0x111, opcode=0x3, status=1)"
        self.assertIn("权限不足", diag.connection_error(rejected, "无法声明接口 0: Access denied (insufficient permissions)"))
        self.assertIn("正在被占用", diag.connection_error(rejected, "Resource busy"))
        self.assertEqual(diag.connection_error("SSH timeout", "Access denied"), "SSH timeout")
        self.assertEqual(diag.connection_error(rejected, ""), rejected)
