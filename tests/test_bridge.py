import importlib.util
import json
import os
import shlex
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import sys
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "python" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load("client", "uta.py")
server = load("server", "uta_server.py")


class ConfigTests(unittest.TestCase):
    def test_migration_preserves_servers_and_original_file(self):
        with tempfile.TemporaryDirectory() as root:
            legacy, current = Path(root) / "old", Path(root) / "new"
            legacy.mkdir()
            current.mkdir()
            data = dict(servers=[dict(id="saved", name="我的服务器", target="cloud")])
            original = json.dumps(data)
            (legacy / "config.json").write_text(original)
            client.migrate_config(current, legacy)
            self.assertEqual(json.loads((current / "config.json").read_text()), data)
            self.assertEqual((legacy / "config.json").read_text(), original)
            self.assertEqual((current / "config.json").stat().st_mode & 0o777, 0o600)

    def test_migration_never_overwrites_current_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            legacy, current = Path(root) / "old", Path(root) / "new"
            legacy.mkdir()
            current.mkdir()
            (legacy / "config.json").write_text('{"servers": []}')
            (current / "config.json").write_text('{"servers": [], "saved": true}')
            client.migrate_config(current, legacy)
            self.assertTrue(json.loads((current / "config.json").read_text())["saved"])

    def test_migration_missing_or_invalid_source(self):
        with tempfile.TemporaryDirectory() as root:
            legacy, current = Path(root) / "old", Path(root) / "new"
            legacy.mkdir()
            current.mkdir()
            client.migrate_config(current, legacy)
            self.assertFalse((current / "config.json").exists())
            (legacy / "config.json").write_text('{"servers": "invalid"}')
            with self.assertRaises(ValueError):
                client.migrate_config(current, legacy)
            self.assertFalse((current / "config.json").exists())

    def test_manual_install_plan_is_read_only_and_allows_interactive_auth(self):
        host = client.validate_server(dict(name="lab", target="lab:2222", use_ssh_config=True))
        with patch.object(client, "run") as run:
            plan = client.manual_install_plan(host)
        run.assert_not_called()
        upload = shlex.split(plan["upload"])
        install = shlex.split(plan["install"])
        self.assertIn("-T", upload)
        self.assertIn("-t", install)
        self.assertNotIn("-p", install)
        self.assertNotIn("BatchMode=yes", install)
        self.assertIn("StrictHostKeyChecking=yes", install)
        self.assertEqual(install[-2], host["target"])
        self.assertIn("sudo install", install[-1])
        self.assertNotIn("sudo -n", install[-1])
        self.assertIn(plan["sha256"], install[-1])
        self.assertIn("sha256sum -c -", install[-1])
        for command in (plan["upload"], plan["install"], install[-1]):
            subprocess.run(["/bin/sh", "-n"], input=command, text=True, check=True)

    def test_manual_install_quotes_paths_and_preserves_explicit_connection(self):
        with tempfile.TemporaryDirectory(prefix="uta path ' with spaces ") as root:
            root = Path(root)
            (root / "uta_server.py").write_text("# test")
            (root / "private key").touch()
            host = client.validate_server(dict(name="lab", target="u@host", port=2222,
                                               identity=str(root / "private key")))
            with patch.object(client, "HERE", root):
                plan = client.manual_install_plan(host)
            upload, install = shlex.split(plan["upload"]), shlex.split(plan["install"])
            self.assertEqual(upload[-1], str(root / "uta_server.py"))
            for argv in (upload, install):
                self.assertEqual(argv[argv.index("-p") + 1], "2222")
                self.assertEqual(argv[argv.index("-i") + 1], str(root / "private key"))
            self.assertIn('set -C', upload[-3])

    def test_shell_metacharacters_rejected(self):
        for value in ("-oProxyCommand=echo", "u@h;touch /tmp/x", "$(id)", "u@h\nid", "host name"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.validate_server(dict(name="a", target=value))

    def test_ssh_alias_and_identity_path_preserved(self):
        with tempfile.NamedTemporaryFile(prefix="key space ") as key:
            s = client.validate_server(dict(name="开发机", target="dev-cloud", identity=key.name))
            argv = client.ssh_args(s)
            self.assertEqual(argv[argv.index("-i") + 1], key.name)
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("BatchMode=yes", argv)

    def test_invalid_port_and_id(self):
        for extra in (dict(port=0), dict(port=65536), dict(id="../../x")):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                client.validate_server(dict(name="a", target="host", **extra))

    def test_imported_alias_inherits_ssh_options(self):
        s = client.validate_server(dict(name="a", target="cloud", use_ssh_config=True,
                                       identity="/nonexistent/unused", port=22))
        argv = client.ssh_args(s)
        self.assertNotIn("-p", argv)
        self.assertNotIn("-i", argv)
        self.assertEqual(s["identity"], "")
        with self.assertRaises(ValueError):
            client.validate_server(dict(name="a", target="cloud", use_ssh_config="false"))

    def test_ssh_discovery_includes_cycles_patterns_and_no_exec(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            ssh = home / ".ssh"
            ssh.mkdir()
            (ssh / "parts").mkdir()
            (ssh / "config").write_text(
                'Host cloud cloud-alt *.example !excluded\n'
                '  HostName private.example\n'
                '  Port 2222\n'
                'Include "parts/*.conf"\n'
                'Match exec "touch /tmp/should-never-execute"\n'
                'Include conditional.conf\n'
                'Host=last # comment\n')
            (ssh / "parts/a.conf").write_text('Host CLOUD jump\nInclude config\n')
            (ssh / "conditional.conf").write_text('Host conditional\nHost "bad alias"\n')
            with patch.object(Path, "home", return_value=home), patch.object(client.subprocess, "run") as run:
                result = client.discover_ssh_hosts()
            self.assertEqual([h["alias"] for h in result["hosts"]],
                             ["cloud", "cloud-alt", "jump", "conditional", "last"])
            self.assertEqual(result["warnings"], [])
            run.assert_not_called()

    def test_missing_ssh_config_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(client.discover_ssh_hosts(Path(root) / "missing"),
                             dict(hosts=[], warnings=[]))

    def test_import_deduplicates_without_connecting_or_overwriting(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            original = client.validate_server(dict(name="Existing", target="cloud", port=2222))
            c.config["servers"] = [original]
            candidates = dict(hosts=[dict(alias="cloud"), dict(alias="new")], warnings=[])
            with patch.object(client, "discover_ssh_hosts", return_value=candidates), \
                 patch.object(client, "HOME", Path(root)), \
                 patch.object(c, "background") as background:
                result = c.handle(dict(action="server_import", aliases=["cloud", "new", "new"]))
                self.assertEqual(len(result["imported"]), 1)
                self.assertEqual(result["skipped"], [original])
                self.assertEqual(c.config["servers"][0], original)
                self.assertTrue(c.config["servers"][1]["use_ssh_config"])
                with self.assertRaises(ValueError):
                    c.handle(dict(action="server_import", aliases=["new", "vanished"]))
                self.assertEqual(len(c.config["servers"]), 2)
                background.assert_not_called()

    def test_remote_argument_escaping(self):
        s = client.validate_server(dict(name="a", target="host"))
        with patch.object(client, "run", return_value="") as mocked:
            client.remote_command(s, ["echo", "x; $(touch bad)"])
            self.assertTrue(mocked.call_args.args[0][-1].endswith("'x; $(touch bad)'"))

    def controller(self, root):
        with patch.object(client, "HOME", Path(root)), patch.object(threading.Thread, "start"):
            c = client.Controller()
        return c

    def test_active_server_cannot_be_edited_or_removed(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            s = client.validate_server(dict(name="a", target="host"))
            c.config["servers"] = [s]
            c.sessions["1-2"] = dict(busid="1-2", server_id=s["id"], state="attached")
            for request in (dict(action="server_save", server=dict(s, target="changed-host")),
                            dict(action="server_remove", id=s["id"])):
                with self.subTest(request=request), self.assertRaises(RuntimeError):
                    c.handle(request)

    def test_remark_can_change_during_active_session_without_changing_route(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            original = client.validate_server(dict(name="alias", target="alias", use_ssh_config=True))
            c.config["servers"] = [original]
            c.sessions["1-2"] = dict(busid="1-2", server_id=original["id"], state="attached")
            c.busy.add(original["id"])
            with patch.object(client, "HOME", Path(root)), patch.object(c, "cleanup") as cleanup:
                saved = c.handle(dict(action="server_save", server=dict(original, name="开发服务器")))
            self.assertEqual(saved["name"], "开发服务器")
            self.assertEqual(saved["target"], "alias")
            self.assertEqual(c.sessions["1-2"]["state"], "attached")
            cleanup.assert_not_called()

    def test_switch_does_not_connect_if_old_release_unconfirmed(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            a = client.validate_server(dict(name="a", target="host-a"))
            b = client.validate_server(dict(name="b", target="host-b"))
            c.config["servers"] = [a, b]
            c.sessions["1-2"] = dict(busid="1-2", server_id=a["id"],
                                      state="attached", session="a" * 32)
            with patch.object(client, "remote_json", side_effect=RuntimeError("offline")) as remote:
                c.connect("1-2", b)
            self.assertEqual(remote.call_count, 1)
            self.assertEqual(c.sessions["1-2"]["server_id"], a["id"])
            self.assertEqual(c.sessions["1-2"]["state"], "error")

    def test_offline_probe_does_not_report_ready(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            with patch.object(client, "remote_command", side_effect=RuntimeError("connection refused")):
                c.check_server(host)
            self.assertEqual(c.checks[host["id"]]["state"], "error")
            self.assertEqual(c.checks[host["id"]]["agent_status"], "unknown")

    def test_missing_agent_is_not_a_network_error(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            with patch.object(client, "remote_command", return_value="missing"), \
                 patch.object(client, "remote_json") as doctor:
                c.check_server(host)
            self.assertEqual(c.checks[host["id"]]["agent_status"], "missing")
            self.assertEqual(c.checks[host["id"]]["state"], "needs_setup")
            doctor.assert_not_called()

    def test_automatic_install_failure_suggests_manual_install(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            def command(_host, argv, **kwargs):
                if argv[0] == "install":
                    raise RuntimeError("sudo: a password is required")
                return ""
            with patch.object(client, "run", return_value=""), \
                 patch.object(client, "remote_command", side_effect=command):
                c.check_server(host, install=True)
            self.assertEqual(c.checks[host["id"]]["state"], "error")
            self.assertIn("手动安装", c.checks[host["id"]]["message"])

    def test_installed_agent_does_not_imply_kernel_ready(self):
        for kernel in (False, True):
            with self.subTest(kernel=kernel), tempfile.TemporaryDirectory() as root:
                c = self.controller(root)
                host = client.validate_server(dict(name="a", target="host-a"))
                info = dict(root=True, vhci_available=kernel, version="0.1.0", message="load module")
                with patch.object(client, "remote_command", return_value="installed"), \
                     patch.object(client, "remote_json", return_value=info):
                    c.check_server(host)
                check = c.checks[host["id"]]
                self.assertEqual(check["agent_status"], "installed")
                self.assertEqual(check["details"]["version"], "0.1.0")
                self.assertEqual(check["state"], "ready" if kernel else "needs_setup")

    def test_installed_but_doctor_fails_keeps_installation_separate(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            with patch.object(client, "remote_command", return_value="installed"), \
                 patch.object(client, "remote_json", side_effect=RuntimeError("sudo denied")):
                c.check_server(host)
            self.assertEqual(c.checks[host["id"]]["agent_status"], "installed")
            self.assertEqual(c.checks[host["id"]]["state"], "error")

    def test_changed_connection_clears_old_installation_check(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            c.config["servers"] = [host]
            c.checks[host["id"]] = dict(agent_status="installed", state="ready")
            with patch.object(client, "HOME", Path(root)):
                c.handle(dict(action="server_save", server=dict(host, target="host-b")))
            self.assertNotIn(host["id"], c.checks)

    def test_stale_attach_does_not_remain_green(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            c.config["servers"] = [host]
            c.sessions["1-2"] = dict(busid="1-2", server_id=host["id"], state="attached",
                                       session="a" * 32)
            with patch.object(client, "remote_json", return_value={"state": "disconnected"}) as remote:
                c.monitor_one("1-2")
            self.assertEqual(c.sessions["1-2"]["state"], "local")
            self.assertEqual(remote.call_args.args[1], ["detach", "--session", "a" * 32])
            self.assertFalse(c.sessions["1-2"]["remote_cleanup_pending"])

    def test_exporter_exit_automatically_detaches_only_its_session(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            c.config["servers"] = [host]
            c.sessions["1-2"] = dict(busid="1-2", server_id=host["id"], state="attached",
                                      session="a" * 32, nodes=["/dev/ttyACM0"])
            c.sessions["1-3"] = dict(busid="1-3", server_id=host["id"], state="attached",
                                      session="b" * 32, nodes=["/dev/ttyACM1"])
            dead = Mock(); dead.poll.return_value = 0
            alive = Mock(); alive.poll.return_value = None
            c.processes["1-2"] = [dead, alive]
            with patch.object(client, "remote_json", return_value={"state": "disconnected"}) as remote, \
                 patch.object(client, "stop_process") as stop:
                c.monitor_one("1-2")
            self.assertEqual([x.args[0] for x in stop.call_args_list], [alive, dead])
            remote.assert_called_once_with(host, ["detach", "--session", "a" * 32])
            self.assertEqual(c.sessions["1-2"]["nodes"], [])
            self.assertEqual(c.sessions["1-3"]["state"], "attached")
            self.assertIn("自动释放", c.sessions["1-2"]["message"])

    def test_offline_cleanup_retries_after_restart_without_reconnecting_usb(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            c.config["servers"] = [host]
            (Path(root) / "config.json").write_text(json.dumps(c.config))
            c.sessions["1-2"] = dict(busid="1-2", server_id=host["id"], state="attached",
                                      session="a" * 32, nodes=["/dev/ttyACM0"])
            dead = Mock(); dead.poll.return_value = 255
            c.processes["1-2"] = [dead]
            with patch.object(client, "remote_json", side_effect=RuntimeError("offline")):
                c.monitor_one("1-2")
            self.assertTrue(c.sessions["1-2"]["remote_cleanup_pending"])
            self.assertEqual(c.sessions["1-2"]["state"], "local")
            with patch.object(c, "background") as schedule:
                c.retry_pending_cleanup()
                schedule.assert_not_called()  # bounded retry, not every scan
            for action in (dict(action="server_remove", id=host["id"]),
                           dict(action="server_save", server=dict(host, target="other"))):
                with self.assertRaises(RuntimeError): c.handle(action)
            restored = self.controller(root)
            self.assertEqual(restored.processes, {})
            self.assertEqual(restored.sessions["1-2"]["nodes"], [])
            with patch.object(restored, "background", side_effect=lambda key, task: task()), \
                 patch.object(client, "remote_json", return_value={"state": "disconnected"}) as remote:
                restored.retry_pending_cleanup()
            remote.assert_called_once_with(host, ["detach", "--session", "a" * 32])
            self.assertFalse(restored.sessions["1-2"]["remote_cleanup_pending"])
            self.assertEqual(json.loads(restored.pending_path.read_text()), [])

    def test_captured_device_missing_from_scan_does_not_trigger_detach(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.controller(root)
            host = client.validate_server(dict(name="a", target="host-a"))
            c.config["servers"] = [host]
            c.devices = []
            c.sessions["1-2"] = dict(busid="1-2", server_id=host["id"], state="attached",
                                      session="a" * 32)
            with patch.object(client, "remote_json", return_value={"state": "attached", "nodes": ["/dev/ttyACM0"]}) as remote:
                c.monitor_one("1-2")
            self.assertEqual(c.sessions["1-2"]["state"], "attached")
            remote.assert_called_once_with(host, ["status", "--session", "a" * 32])


class ServerTests(unittest.TestCase):
    def test_attach_handshake_and_sysfs_contract(self):
        """Fake sysfs, real TCP import; verifies fd/speed/devid and persisted ownership."""
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            vhci = root / "vhci"
            vhci.mkdir()
            (vhci / "attach").touch()
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            tcp_port = listener.getsockname()[1]
            errors = []
            def exporter():
                try:
                    conn, _ = listener.accept()
                    with conn:
                        request = server.receive(conn, 40)
                        self.assertEqual(struct.unpack("!HHI32s", request)[3].rstrip(b"\0"), b"2-1.4")
                        reply = struct.pack("!256s32sIIIHHHBBBBBB", b"/device", b"2-1.4",
                                            2, 3, 2, 0x303a, 0x1001, 0x100, 0, 0, 0, 1, 1, 1)
                        conn.sendall(struct.pack("!HHI", 0x111, 3, 0) + reply)
                        while conn.recv(1):
                            pass
                except Exception as exc:
                    errors.append(exc)
                finally:
                    listener.close()
            worker = threading.Thread(target=exporter)
            worker.start()
            free = dict(hub="hs", port=0, status=4, devid=0, socket="0", local_busid="0-0")
            used = dict(hub="hs", port=0, status=5, devid=0x20003, socket="123", local_busid="0-0")
            with patch.object(server, "VHCI", vhci), patch.object(server, "STATE", root / "state"), \
                 patch.object(server.os, "geteuid", return_value=0), \
                 patch.object(server, "owner", return_value=1001), \
                 patch.object(server, "socket_is_live", return_value=True), \
                 patch.object(server, "ports", side_effect=[[free], [used], [used]]):
                result = server.attach("a" * 32, tcp_port, "2-1.4", route="b" * 32, auto_reconnect=True)
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result["state"], "enumerating")
            self.assertEqual(result["owner"], 1001)
            self.assertEqual(result["route_id"], "b" * 32)
            self.assertTrue(result["reconnect_enabled"])
            fields = (vhci / "attach").read_text().split()
            self.assertEqual(fields[0], "0")
            self.assertEqual(fields[2:], [str(0x20003), "2"])
            self.assertTrue((root / "state" / ("a" * 32 + ".json")).is_file())

    def test_vhci_high_and_super_speed_parsing(self):
        rows = server.parse_status("hub port sta spd dev socket busid\n"
            "hs 0000 004 000 00000000 0000000000000000 0-0\n"
            "ss 0008 006 005 00010002 0000000000abc123 3-1\n")
        self.assertEqual(rows[0]["status"], 4)
        self.assertEqual(rows[1]["devid"], 0x10002)
        self.assertEqual(rows[1]["local_busid"], "3-1")

    def test_partial_tcp_frames_and_eof(self):
        a, b = socket.socketpair()
        def send():
            b.sendall(b"abc")
            time.sleep(0.01)
            b.sendall(b"def")
            b.close()
        worker = threading.Thread(target=send)
        worker.start()
        with a:
            self.assertEqual(server.receive(a, 6), b"abcdef")
            with self.assertRaises(RuntimeError):
                server.receive(a, 1)
        worker.join()

    def test_rejected_import_and_wrong_version(self):
        for version, status in ((0x111, 1), (0x999, 0)):
            a, b = socket.socketpair()
            with a, b:
                b.sendall(struct.pack("!HHI", version, 3, status))
                with self.assertRaises(RuntimeError):
                    server.header(a, 3)

    def test_session_traversal_rejected(self):
        for session in ("../secret", "x" * 32, ""):
            with self.assertRaises(ValueError):
                server.validate_session(session)

    def test_detach_does_not_touch_reused_port(self):
        record = dict(vhci_port=0, devid=65538, socket="aaa")
        with patch.object(server, "ports", return_value=[
            dict(port=0, status=6, devid=65538, socket="bbb")
        ]):
            self.assertIsNone(server.matching_port(record))

    def test_enumerating_zero_identity_requires_live_original_socket(self):
        record = dict(vhci_port=0, devid=0x20001, socket_inode=123)
        row = dict(port=0, status=5, devid=0, socket="000000", local_busid="0-0")
        with patch.object(server, "ports", return_value=[row]):
            with patch.object(server, "socket_is_live", return_value=True):
                self.assertEqual(server.record_status(record)["state"], "enumerating")
            with patch.object(server, "socket_is_live", return_value=False):
                self.assertIsNone(server.matching_port(record))

    def test_socket_identity_checks_inode_tuple_and_connected_state(self):
        record = dict(socket_inode=1234, local_tcp_port=40000, tcp_port=30000)
        row = " 0: 0100007F:9C40 0100007F:7530 01 0:0 00:0 0 0 0 1234\n"
        with patch.object(Path, "read_text", return_value="header\n" + row):
            self.assertTrue(server.socket_is_live(record))
            self.assertFalse(server.socket_is_live(dict(record, socket_inode=5678)))
            self.assertFalse(server.socket_is_live(dict(record, tcp_port=30001)))
        with patch.object(Path, "read_text", return_value="header\n" + row.replace(" 01 ", " 08 ")):
            self.assertFalse(server.socket_is_live(record))

    def test_cross_user_record_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, "STATE", Path(root)):
            (Path(root) / ("a" * 32 + ".json")).write_text('{"owner":1001}')
            with patch.object(server, "owner", return_value=1002), self.assertRaises(PermissionError):
                server.read_record("a" * 32)


class NativeProtocolTests(unittest.TestCase):
    """Actual native core TCP roundtrip; synthetic USB handler, no real hardware."""
    def setUp(self):
        self.proc = subprocess.Popen([str(ROOT / "build/native/uta-test-fixture")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        line = self.proc.stdout.readline()
        self.assertTrue(line, "native test fixture could not listen")
        self.port = int(line.strip())

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.communicate("\n", timeout=5)
        else:
            self.proc.communicate(timeout=5)

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=3)

    def test_enumeration_import_bulk_roundtrip_and_unlink(self):
        with self.connect() as sock:
            sock.sendall(struct.pack("!HHI", 0x111, 0x8005, 0))
            server.header(sock, 5)
            self.assertEqual(struct.unpack("!I", server.receive(sock, 4))[0], 1)
            dev = server.descriptor(server.receive(sock, 312))
            self.assertEqual(dev["busid"], "99-1")
            self.assertEqual(dev["vidpid"], "1234:5678")
            server.receive(sock, 4 * dev["interfaces"])
        with self.connect() as sock:
            sock.sendall(struct.pack("!HHI32s", 0x111, 0x8003, 0, b"99-1"))
            server.header(sock, 3)
            server.receive(sock, 312)
            data = b"UTA native protocol integration" * 64
            submit = lambda seq, direction, ep, size: struct.pack(
                "!IIIIIIIIII8s", 1, seq, 0x10001, direction, ep, 0, size, 0, 0xffffffff, 0, b"\0" * 8)
            sock.sendall(submit(1, 0, 2, len(data)) + data)
            reply = struct.unpack("!IIIIIIIIII8s", server.receive(sock, 48))
            self.assertEqual(reply[:2], (3, 1))
            self.assertEqual(reply[5:7], (0, len(data)))
            sock.sendall(submit(2, 1, 1, len(data)))
            reply = struct.unpack("!IIIIIIIIII8s", server.receive(sock, 48))
            self.assertEqual(reply[5:7], (0, len(data)))
            self.assertEqual(server.receive(sock, len(data)), data)
            sock.sendall(struct.pack("!IIIIII24s", 2, 3, 0x10001, 0, 0, 2, b"\0" * 24))
            reply = struct.unpack("!IIIIII24s", server.receive(sock, 48))
            self.assertEqual(reply[:2], (4, 3))

    def test_unknown_device_import_rejected(self):
        with self.connect() as sock:
            sock.sendall(struct.pack("!HHI32s", 0x111, 0x8003, 0, b"99-99"))
            with self.assertRaises(RuntimeError):
                server.header(sock, 3)


if __name__ == "__main__":
    unittest.main()
