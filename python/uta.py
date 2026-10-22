#!/usr/bin/env python3
"""UTA Mac controller, CLI and per-user Unix-socket daemon."""
import sys
sys.dont_write_bytecode = True
import argparse
import atexit
import copy
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import socket
import socketserver
import subprocess
import threading
import time
import uuid
from uta_diagnostics import inspect_devices, connection_error
import uta_privileged

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_HOME = Path.home() / "Library/Application Support/UTA"
LEGACY_HOME = Path.home() / "Library/Application Support/USBBridge"
HOME = Path(os.environ.get("UTA_HOME", os.environ.get("USBBRIDGE_HOME", str(DEFAULT_HOME))))
# Darwin's sockaddr_un has a 104-byte path limit.
SOCKET = Path("/tmp") / f"uta-{os.getuid()}-{__import__('hashlib').sha256(str(HOME).encode()).hexdigest()[:12]}.sock"
NATIVE = Path(os.environ.get("UTA_NATIVE", os.environ.get("USBBRIDGE_NATIVE",
                                                       str(HERE.parent / "bin/uta-native"))))
if not NATIVE.exists():
    NATIVE = HERE.parent / "build/native/uta-native"
REMOTE = "/usr/local/bin/uta-server"
BUSID = re.compile(r"[0-9]+-[0-9.:]+")
TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*")
ACTIVE = {"connecting", "enumerating", "attached", "waiting_reconnect"}
RECONNECT_SECONDS = 120
USB_ID = re.compile(r"[0-9a-f]{4}:[0-9a-f]{4}")


def reconnect_ids(values):
    if not isinstance(values, list) or len(values) > 16:
        raise ValueError("重连标识必须是最多 16 项的 VID:PID 列表")
    if any(not isinstance(v, str) or not USB_ID.fullmatch(v.lower()) for v in values):
        raise ValueError("USB 标识格式应为四位十六进制 VID:PID")
    return sorted(set(v.lower() for v in values))


def reconnect_matches(anchor, device):
    """Never follow a different physical port or an unrecognized PID change."""
    if anchor.get("busid") != device.get("busid"):
        return False
    before, after = anchor.get("vidpid"), device.get("vidpid")
    permitted = after in anchor.get("allowed_vidpids", [])
    if not before or (before != after and not permitted):
        return False
    serial = anchor.get("serial", "")
    other = device.get("serial", "")
    if serial and serial != other:
        # Explicitly approved re-enumeration IDs may omit the original serial.
        return bool(permitted and before != after and not other)
    return True


def usb_identity(device):
    return {key: device.get(key, "") for key in ("busid", "vidpid", "serial")}


def confirmable_mode(anchor, device):
    return (anchor.get("busid") == device.get("busid")
            and anchor.get("vidpid") != device.get("vidpid")
            and bool(USB_ID.fullmatch(device.get("vidpid", "")))
            and (not anchor.get("serial") or not device.get("serial")
                 or anchor["serial"] == device["serial"]))


def reconnect_rule_key(server_id, anchor):
    data = dict(server=server_id, **usb_identity(anchor))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def migrate_config(destination, legacy):
    """Copy existing server preferences once, without altering the previous data."""
    current = destination / "config.json"
    previous = legacy / "config.json"
    if current.exists() or not previous.is_file():
        return
    data = json.loads(previous.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("旧版本服务器配置格式无效，请先检查配置文件")
    atomic_json(current, data)


def run(args, timeout=20, input=None):
    proc = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr.strip() or proc.stdout.strip() or
                            f"Command exited {proc.returncode}")[-3000:])
    return proc.stdout.strip()


def validate_server(data):
    result = {k: str(data.get(k, "")).strip() for k in ("id", "name", "target", "identity")}
    result["id"] = result["id"] or uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", result["id"]):
        raise ValueError("Invalid server ID")
    if not result["name"] or len(result["name"]) > 80:
        raise ValueError("服务器名称不能为空，且不能超过 80 个字符")
    if not TARGET.fullmatch(result["target"]):
        raise ValueError("SSH 地址使用 user@host 或 ~/.ssh/config 中的别名，不要填写命令或空格")
    value = data.get("port", 22)
    result["port"] = 22 if value in ("", None) else int(value)
    if not 1 <= result["port"] <= 65535:
        raise ValueError("SSH 端口必须在 1–65535")
    if not isinstance(data.get("use_ssh_config", False), bool):
        raise ValueError("use_ssh_config must be a boolean")
    result["use_ssh_config"] = data.get("use_ssh_config", False)
    if result["use_ssh_config"]:
        result["identity"] = ""
    if result["identity"]:
        result["identity"] = str(Path(result["identity"]).expanduser())
        if not Path(result["identity"]).is_file():
            raise ValueError("SSH 私钥文件不存在")
    return result


def ssh_args(server):
    args = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-o", "LogLevel=INFO",
            "-o", "ForwardAgent=no", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
    if not server.get("use_ssh_config"):
        args += ["-p", str(server["port"])]
    if server["identity"] and not server.get("use_ssh_config"):
        args += ["-i", server["identity"]]
    return args


def discover_ssh_hosts(config=None):
    """List literal aliases, without evaluating Match exec or connecting anywhere.

    Includes are scanned for candidates even in conditional blocks. OpenSSH remains
    the authority for effective settings when the user explicitly connects.
    """
    base = Path.home() / ".ssh"
    root = Path(config) if config is not None else base / "config"
    hosts, warnings, visited = {}, [], set()

    def read(path, depth=0):
        path = path.resolve()
        if path in visited:
            return
        if depth > 16 or len(visited) >= 256:
            warnings.append("SSH Include 超过扫描限制：" + str(path))
            return
        visited.add(path)
        try:
            lines = path.read_text().splitlines()
        except OSError as exc:
            warnings.append(f"无法读取 {path}：{exc.strerror}")
            return
        except UnicodeError:
            warnings.append(f"无法读取非 UTF-8 配置：{path}")
            return
        for number, line in enumerate(lines, 1):
            match = re.match(r"^\s*([A-Za-z]+)(?:\s*=\s*|\s+)(.*)$", line)
            if not match or match[1].lower() not in ("host", "include"):
                continue
            try:
                values = shlex.split(match[2], comments=True)
            except ValueError:
                warnings.append(f"配置语法无法解析：{path}:{number}")
                continue
            if match[1].lower() == "host":
                for alias in values:
                    if TARGET.fullmatch(alias) and len(alias) <= 80:
                        hosts.setdefault(alias.lower(), dict(alias=alias, source=str(path)))
            else:
                for value in values:
                    included = Path(value).expanduser()
                    if not included.is_absolute():
                        included = base / included
                    for filename in sorted(glob.glob(str(included))):
                        read(Path(filename), depth + 1)

    if root.exists():
        read(root)
    return dict(hosts=list(hosts.values()), warnings=warnings)


def remote_command(server, argv, privileged=False, timeout=20):
    cmd = shlex.join(argv)
    if privileged:
        cmd = f'if [ "$(id -u)" = 0 ]; then exec {cmd}; else exec sudo -n {cmd}; fi'
    return run(ssh_args(server) + [server["target"], cmd], timeout=timeout)


def remote_json(server, argv, privileged=True):
    text = remote_command(server, [REMOTE] + argv, privileged)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("云端未返回 JSON，请检查 SSH 登录脚本是否输出了额外内容") from exc
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Remote command failed"))
    return data["result"]


def manual_install_plan(server):
    """Prepare reviewable commands; never execute SSH or change the server."""
    source = HERE / "uta_server.py"
    filename = "uta-agent-" + uuid.uuid4().hex + ".py"
    ssh = ["/usr/bin/ssh", "-t", "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no"]
    if not server.get("use_ssh_config"):
        ssh += ["-p", str(server["port"])]
        if server["identity"]:
            ssh += ["-i", server["identity"]]
    # SSH stdin avoids scp's ambiguity with colon-containing SSH aliases.
    upload_ssh = ["-T" if a == "-t" else a for a in ssh]
    upload_remote = f'umask 077; set -C; cat > "$HOME/{filename}"'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    install = f'install -o root -g root -m 755 "$HOME/{filename}" {REMOTE}'
    remote = (
        f'printf "%s  %s\\n" {digest} "$HOME/{filename}" | sha256sum -c - &&\n'
        f'if [ "$(id -u)" = 0 ]; then\n'
        f'  {install} && /sbin/modprobe vhci_hcd && {REMOTE} doctor\n'
        f'else\n'
        f'  sudo {install} && sudo /sbin/modprobe vhci_hcd && sudo {REMOTE} doctor\n'
        f'fi && rm -- "$HOME/{filename}"'
    )
    return dict(source=str(source), sha256=digest,
                upload=shlex.join(upload_ssh + [server["target"], upload_remote]) +
                       " < " + shlex.quote(str(source)),
                install=shlex.join(ssh + [server["target"], remote]))


def stop_process(proc):
    if proc and proc.poll() is None:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


class Controller:
    def __init__(self):
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(HOME, 0o700)
        (HOME / "logs").mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.config = {"servers": []}
        path = HOME / "config.json"
        if path.exists():
            self.config = json.loads(path.read_text())
        self.rules_path = HOME / "reconnect-rules.json"
        self.reconnect_rules = json.loads(self.rules_path.read_text()) if self.rules_path.exists() else {}
        self.devices = []
        self.scan_error = ""
        self.sessions = {}
        self.pending_path = HOME / "pending-cleanup.json"
        if self.pending_path.exists():
            for item in json.loads(self.pending_path.read_text()):
                item.pop("reconnect_candidate", None)
                item.update(state="local", nodes=[], remote_cleanup_pending=True,
                            reconnect_deadline=0,
                            cleanup_retry_at=0,
                            message="本地已断开，正在自动清理云端会话…")
                self.sessions[item["busid"]] = item
        self.checks = {}
        self.processes = {}
        self.busy = set()
        self.update_owner = None
        self.stopping = threading.Event()
        self.last_monitor = 0.0
        self.diagnostics = {}
        self.last_diagnostics = 0.0
        self.diagnostic_devices = []
        self.local_access = dict(available=False, message="尚未检查本机 USB 授权")
        threading.Thread(target=self.watch, daemon=True).start()

    def save(self):
        atomic_json(HOME / "config.json", self.config)

    def remembered_ids(self, server_id, anchor):
        return reconnect_ids(self.reconnect_rules.get(reconnect_rule_key(server_id, anchor), []))

    def remember_ids(self, item, ids):
        ids = reconnect_ids(ids)
        key = reconnect_rule_key(item["server_id"], item["reconnect_anchor"])
        rules = dict(self.reconnect_rules)
        if ids:
            rules[key] = ids
        else:
            rules.pop(key, None)
        atomic_json(self.rules_path, rules)
        self.reconnect_rules = rules
        item["reconnect_remembered"] = bool(ids)

    def server(self, server_id):
        item = next((s for s in self.config["servers"] if s["id"] == server_id), None)
        if item is None:
            raise ValueError("服务器不存在")
        return copy.deepcopy(item)

    def update(self, busid, **values):
        with self.lock:
            if busid in self.sessions:
                self.sessions[busid].update(values, updated=time.time())

    def background(self, key, task):
        with self.lock:
            if self.stopping.is_set() or self.update_reserved():
                raise RuntimeError("UTA 正在更新，请完成或取消更新后再操作")
            if key in self.busy:
                raise RuntimeError("该设备/服务器正在执行操作，请稍候")
            self.busy.add(key)

        def work():
            try:
                task()
            finally:
                with self.lock:
                    self.busy.discard(key)
        threading.Thread(target=work, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(dict(version=VERSION, servers=self.config["servers"],
                         devices=self.devices, sessions=list(self.sessions.values()),
                         checks=self.checks, scan_error=self.scan_error,
                         home=str(HOME), busy=sorted(self.busy), local_access=self.local_access))

    def check_server(self, server, install=False):
        sid = server["id"]
        agent_status = "unknown"
        with self.lock:
            self.checks[sid] = dict(state="checking", agent_status="checking",
                                   message="正在安装云端代理…" if install else "正在检查 SSH 和云端代理…")
        try:
            if install:
                source = HERE / "uta_server.py"
                # Upload without scp path interpolation. Installation requires existing
                # root / noninteractive sudo rights; never modify sudoers automatically.
                stage = f"/tmp/uta-agent-{uuid.uuid4().hex}.py"
                command = "umask 077; cat > " + shlex.quote(stage)
                run(ssh_args(server) + [server["target"], command], input=source.read_text())
                try:
                    remote_command(server, ["install", "-o", "root", "-g", "root",
                                           "-m", "755", stage, REMOTE], privileged=True)
                    remote_command(server, ["/sbin/modprobe", "vhci_hcd"], privileged=True)
                finally:
                    remote_command(server, ["rm", "-f", stage])
            probe = remote_command(server, ["sh", "-c",
                f"if test -f {shlex.quote(REMOTE)}; then printf installed; else printf missing; fi"])
            if probe not in ("installed", "missing"):
                raise RuntimeError("安装状态检查返回异常，请检查 SSH 登录输出")
            agent_status = probe
            if probe == "missing":
                with self.lock:
                    self.checks[sid] = dict(state="needs_setup", agent_status="missing",
                                           message="SSH 已连接，云端代理尚未安装",
                                           checked=time.time())
                return
            info = remote_json(server, ["doctor"])
            ready = info.get("root") and info.get("vhci_available")
            with self.lock:
                self.checks[sid] = dict(state="ready" if ready else "needs_setup",
                                       agent_status="installed",
                                       message="SSH 和虚拟 USB 已就绪" if ready else info["message"],
                                       details=info, checked=time.time())
        except Exception as exc:
            with self.lock:
                self.checks[sid] = dict(state="error", agent_status=agent_status,
                                       message=str(exc) + ("；可通过「手动安装」在终端交互授权" if install else ""),
                                       checked=time.time())

    def cleanup(self, busid):
        with self.lock:
            processes = self.processes.pop(busid, [])
        # Close tunnel before exporter so the importer loses its USB session.
        for proc in reversed(processes):
            stop_process(proc)

    def save_pending_cleanup(self):
        with self.lock:
            atomic_json(self.pending_path, [s for s in self.sessions.values()
                                           if s.get("remote_cleanup_pending")])

    def disconnect(self, busid, reason=None, preserve_reconnect=False):
        with self.lock:
            if not preserve_reconnect and busid in self.sessions:
                self.sessions[busid]["reconnect_deadline"] = 0
            if busid in self.sessions:
                self.sessions[busid].pop("reconnect_candidate", None)
            old = copy.deepcopy(self.sessions.get(busid))
        if not old:
            return
        self.update(busid, state="disconnecting", nodes=[], remote_cleanup_pending=True,
                    disconnect_reason=reason, message="正在自动释放云端设备…")
        # Persist before attempting SSH so an offline cleanup survives app restart.
        self.save_pending_cleanup()
        # Close the local transport immediately. Linux USB/IP releases the device
        # on socket EOF even when a separate SSH cleanup request cannot get through.
        self.cleanup(busid)
        error = None
        try:
            server = self.server(old["server_id"])
            if old.get("session"):
                remote_json(server, ["detach", "--session", old["session"]])
        except Exception as exc:
            error = str(exc)
        waiting = old.get("reconnect_enabled") and old.get("reconnect_deadline", 0) > time.time()
        self.update(busid, state="waiting_reconnect" if waiting else "local", nodes=[], message=(
            "设备已断开，等待同一插口的设备返回；将自动接回原服务器（最长 2 分钟）" if waiting else
            "本地已断开；云端暂不可达，恢复后会自动清理" if error else
            ("连接已断开，云端设备已自动释放；可重新选择接入" if reason else "已释放，可选择其他服务器")),
            remote_cleanup_pending=bool(error), cleanup_error=error,
            cleanup_retry_at=time.time() + 15 if error else 0)
        self.save_pending_cleanup()

    def retry_pending_cleanup(self):
        with self.lock:
            due = [(b, s.get("disconnect_reason")) for b, s in self.sessions.items()
                   if s.get("remote_cleanup_pending") and b not in self.busy
                   and time.time() >= s.get("cleanup_retry_at", 0)]
        for busid, reason in due:
            try:
                self.background(busid, lambda b=busid, r=reason: self.disconnect(b, r, preserve_reconnect=True))
            except RuntimeError:
                pass

    def connection_lost(self, busid, reason):
        with self.lock:
            item = self.sessions.get(busid)
            if item and item.get("reconnect_enabled") and item.get("reconnect_anchor"):
                if not item.get("reconnect_deadline"):
                    item.update(reconnect_deadline=time.time() + RECONNECT_SECONDS,
                                reconnect_attempts=0, reconnect_retry_at=0)
        self.disconnect(busid, reason, preserve_reconnect=True)

    def retry_reconnect(self):
        with self.lock:
            for busid, item in self.sessions.items():
                deadline = item.get("reconnect_deadline", 0)
                if not deadline or busid in self.busy:
                    continue
                if time.time() >= deadline:
                    item.pop("reconnect_candidate", None)
                    if busid in self.processes or item.get("state") in ("connecting", "enumerating"):
                        item["reconnect_deadline"] = 0
                        self.background(busid, lambda b=busid: self.disconnect(b, "自动重连等待已结束"))
                        continue
                    item.update(reconnect_deadline=0, state="local", nodes=[],
                                message="自动重连等待已结束；云端设备已释放，可手动重新接入" if not item.get("remote_cleanup_pending")
                                else "重连等待已结束；云端清理将在服务器恢复后继续")
                    continue
                if (self.scan_error or not item.get("reconnect_enabled") or item.get("remote_cleanup_pending")
                        or item.get("state") not in ("waiting_reconnect", "error")
                        or time.time() < item.get("reconnect_retry_at", 0)):
                    item.pop("reconnect_candidate", None)
                    continue
                if item.get("reconnect_attempts", 0) >= 3:
                    item.pop("reconnect_candidate", None)
                    item.update(reconnect_deadline=0, state="error", message="自动重连已尝试 3 次，请检查连接后手动接入")
                    continue
                device = next((d for d in self.devices if d["busid"] == busid), None)
                if not device:
                    item.pop("reconnect_candidate", None)
                    continue
                if not reconnect_matches(item["reconnect_anchor"], device):
                    if confirmable_mode(item["reconnect_anchor"], device):
                        candidate = item.get("reconnect_candidate")
                        if not candidate or usb_identity(candidate) != usb_identity(device):
                            item["reconnect_candidate"] = dict(usb_identity(device),
                                name=device.get("name", "USB 设备"), token=uuid.uuid4().hex)
                        item["message"] = "同一插口出现新的 USB 标识，请确认是否仍是刚才的设备"
                    else:
                        item.pop("reconnect_candidate", None)
                        item["message"] = "当前插口的设备身份不匹配，请手动检查设备"
                    continue
                item.pop("reconnect_candidate", None)
                item["reconnect_attempts"] = item.get("reconnect_attempts", 0) + 1
                item["reconnect_retry_at"] = time.time() + 5
                host = self.server(item["server_id"])
                self.background(busid, lambda b=busid, s=host: self.connect(b, s, automatic=True))

    def connect(self, busid, server, access_mode="auto", reconnect_enabled=None, automatic=False, allowed_ids=None):
        native_log = None
        privileged = False
        authorization_required = False
        try:
            with self.lock:
                old = copy.deepcopy(self.sessions.get(busid))
                if automatic and (not old or not old.get("reconnect_enabled")
                                  or old.get("reconnect_deadline", 0) <= time.time()):
                    return
            if old and (not automatic or old.get("remote_cleanup_pending") or busid in self.processes):
                self.disconnect(busid)
                with self.lock:
                    if self.sessions[busid].get("remote_cleanup_pending"):
                        raise RuntimeError("旧服务器未确认释放。请恢复旧服务器连接并断开后再切换。")
            with self.lock:
                device = next((d for d in self.devices if d["busid"] == busid), None)
                if not device:
                    raise RuntimeError("USB 设备已拔出，请刷新后重新选择")
                if automatic and not reconnect_matches(old["reconnect_anchor"], device):
                    raise RuntimeError("设备标识在重连前发生变化，未接入")
                enabled = reconnect_enabled if reconnect_enabled is not None else (old or {}).get("reconnect_enabled", True)
                anchor = old["reconnect_anchor"] if automatic else {k: device.get(k, "") for k in ("busid", "vidpid", "serial")}
                ids = (old or {}).get("reconnect_ids", []) if old and reconnect_matches(old.get("reconnect_anchor", {}), device) else []
                if not automatic:
                    ids = sorted(set(ids + self.remembered_ids(server["id"], anchor)))
                if allowed_ids is not None:
                    ids = reconnect_ids(allowed_ids)
                ids = sorted(set(ids + [anchor["vidpid"], device["vidpid"]]))
                anchor["allowed_vidpids"] = ids
                self.sessions[busid] = dict(busid=busid, name=device["name"],
                    vidpid=device["vidpid"], server_id=server["id"], session=uuid.uuid4().hex,
                    state="connecting", message="正在检查云端环境…", nodes=[],
                    created=time.time(), updated=time.time(), reconnect_enabled=enabled,
                    reconnect_anchor=anchor,
                    reconnect_ids=ids,
                    reconnect_remembered=bool(self.remembered_ids(server["id"], anchor)),
                    route_id=old.get("route_id", uuid.uuid4().hex) if automatic else uuid.uuid4().hex,
                    reconnect_deadline=old.get("reconnect_deadline", 0) if automatic else 0,
                    reconnect_attempts=old.get("reconnect_attempts", 0) if automatic else 0,
                    reconnect_retry_at=time.time() + 5)
                session = self.sessions[busid]["session"]
            access_state = (device.get("access") or {}).get("state")
            if access_state == "process_busy":
                raise RuntimeError("串口正被其他程序打开。请查看设备占用信息并关闭对应连接后重试；UTA 不会强制终止该程序。")
            needs_capture = access_state == "driver_bound"
            needs_capture = needs_capture or bool(old and old.get("authorization_required"))
            if access_mode == "authorized" or (access_mode == "auto" and needs_capture):
                self.update(busid, message="正在检查 Mac 本机 USB 授权…")
                info = uta_privileged.status()
                with self.lock:
                    self.local_access = info
                if not info["available"]:
                    authorization_required = True
                    raise RuntimeError("需要本机 USB 接管授权。请点击「启用 USB 接管」，在系统设置中批准后重新接入。")
                privileged = True
            self.update(busid, access_mode="authorized" if privileged else "standard")
            check = remote_json(server, ["doctor"])
            if not check.get("root") or not check.get("vhci_available"):
                raise RuntimeError("云端尚未就绪，请先安装代理并加载 vhci_hcd")
            native_log = HOME / "logs" / f"{session}-usb.log"
            ssh_log = HOME / "logs" / f"{session}-ssh.log"
            self.update(busid, log=str(native_log), ssh_log=str(ssh_log),
                        message="正在准备所选 USB 设备…")
            with native_log.open("w") as errors:
                exporter_args = ([sys.executable, str(HERE / "uta_privileged.py"), busid,
                                  device["vidpid"], device.get("serial", "")] if privileged else
                                 [str(NATIVE), "export", busid])
                exporter = subprocess.Popen(exporter_args,
                    stdout=subprocess.PIPE, stderr=errors, text=True)
            with self.lock:
                self.processes[busid] = [exporter]
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(exporter.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=10):
                    raise RuntimeError("USB 导出器启动超时")
                line = exporter.stdout.readline()
            if not line:
                raise RuntimeError("USB 导出失败：" + native_log.read_text()[-1800:])
            endpoint = json.loads(line)
            local_destination = endpoint["socket"] if privileged else f'127.0.0.1:{endpoint["port"]}'
            self.update(busid, message="正在建立加密 SSH 隧道…")
            args = ssh_args(server) + ["-o", "ExitOnForwardFailure=yes",
                "-R", f"127.0.0.1:0:{local_destination}",
                server["target"], "cat >/dev/null"]
            with ssh_log.open("w") as errors:
                tunnel = subprocess.Popen(args, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=errors)
            with self.lock:
                self.processes[busid].append(tunnel)
            remote_port = None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                text = ssh_log.read_text()
                match = re.search(r"Allocated port ([0-9]+) for remote forward", text)
                if match:
                    remote_port = int(match[1])
                    break
                if tunnel.poll() is not None:
                    raise RuntimeError("SSH 隧道失败：" + text[-2000:])
                time.sleep(0.1)
            if not remote_port:
                raise RuntimeError("SSH 未确认反向端口；请检查 AllowTcpForwarding、GatewayPorts 和 SSH 日志")
            self.update(busid, state="enumerating", message="隧道已连接，正在云端挂载 USB…")
            attach_args = ["attach", "--session", session, "--port", str(remote_port), "--busid", busid]
            if check.get("route_identity"):
                attach_args += ["--route", self.sessions[busid]["route_id"]]
                if enabled:
                    attach_args += ["--auto-reconnect"]
            info = remote_json(server, attach_args)
            if info.get("state") not in ("enumerating", "attached"):
                raise RuntimeError("云端 USB 接入未保持连接，尚未生成设备端口；请查看 USB 日志后重试")
            self.update(busid, state=info["state"], remote_port=remote_port,
                        nodes=info.get("nodes", []), message="等待 Linux 完成 USB 枚举…")
            self.monitor_one(busid)
        except Exception as exc:
            self.cleanup(busid)
            with self.lock:
                failed = copy.deepcopy(self.sessions.get(busid, {}))
            if failed.get("state") == "enumerating" and not failed.get("remote_cleanup_pending"):
                self.disconnect(busid, "接入未完成：" + str(exc), preserve_reconnect=automatic)
            try:
                log = native_log.read_text()[-12000:] if native_log and native_log.exists() else ""
            except OSError:
                log = ""
            authorization_required = authorization_required or (not privileged and "Access denied" in log)
            message = connection_error(str(exc), log)
            if privileged:
                message = "已使用授权服务，但接入未成功：" + message
            self.update(busid, state="permission_required" if authorization_required else "error",
                        authorization_required=authorization_required, message=message,
                        raw_error=str(exc), nodes=[])
            self.last_diagnostics = 0.0

    def monitor_one(self, busid):
        with self.lock:
            item = copy.deepcopy(self.sessions.get(busid))
            processes = list(self.processes.get(busid, []))
        if not item or item["state"] not in ("enumerating", "attached"):
            return
        try:
            if any(p.poll() is not None for p in processes):
                self.connection_lost(busid, "USB 导出器或 SSH 隧道已停止")
                return
            info = remote_json(self.server(item["server_id"]),
                               ["status", "--session", item["session"]])
            if info["state"] == "disconnected":
                self.connection_lost(busid, "设备已掉线或重启")
                return
            if info["state"] == "enumerating" and time.time() - item.get("created", time.time()) > 45:
                raise RuntimeError("云端 USB 枚举超时；请检查 USB 日志、设备权限及服务器内核日志，再重新接入。")
            self.update(busid, state=info["state"], nodes=info.get("nodes", []),
                        message="云端 USB 已挂载；可运行烧录工具" if info["state"] == "attached"
                        else "隧道已连接，Linux 正在枚举设备…",
                        last_verified=time.time())
            if info["state"] == "attached":
                self.update(busid, reconnect_deadline=0, reconnect_attempts=0)
        except Exception as exc:
            self.connection_lost(busid, str(exc))

    def watch(self):
        while not self.stopping.is_set():
            try:
                devices = json.loads(run([str(NATIVE), "list"], timeout=8))
                identities = [(d["busid"], d["vidpid"], d.get("serial")) for d in devices]
                if identities != self.diagnostic_devices or time.monotonic() - self.last_diagnostics > 10:
                    self.diagnostics = inspect_devices(devices)
                    self.diagnostic_devices = identities
                    self.last_diagnostics = time.monotonic()
                for device in devices:
                    device["access"] = self.diagnostics.get(device["busid"])
                with self.lock:
                    self.devices, self.scan_error = devices, ""
            except Exception as exc:
                with self.lock:
                    self.scan_error = str(exc)
            with self.lock:
                ids = [b for b, s in self.sessions.items()
                       if s["state"] in ("enumerating", "attached") and b not in self.busy]
            for busid in ids:
                try:
                    self.background(busid, lambda b=busid: self.monitor_one(b))
                except RuntimeError:
                    pass
            self.retry_pending_cleanup()
            self.retry_reconnect()
            self.stopping.wait(3)

    def update_reserved(self):
        if self.update_owner:
            try:
                os.kill(self.update_owner, 0)
            except ProcessLookupError:
                self.update_owner = None
        return self.update_owner is not None

    def prepare_update(self, owner):
        if not isinstance(owner, int) or isinstance(owner, bool) or owner <= 1:
            raise ValueError("Invalid updater process")
        os.kill(owner, 0)
        if self.update_reserved() and self.update_owner != owner:
            raise RuntimeError("另一个 UTA 窗口正在更新")
        if self.busy or self.processes or any(
                s["state"] in ACTIVE | {"disconnecting"} or s.get("remote_cleanup_pending")
                or s.get("reconnect_deadline", 0) > time.time() for s in self.sessions.values()):
            raise RuntimeError("请先断开 USB，取消等待重连，并等待云端清理完成后再更新")
        self.update_owner = owner

    def handle(self, request):
        action = request.get("action")
        with self.lock:
            if action in ("update_prepare", "update_cancel", "update_shutdown"):
                owner = request.get("pid")
                if action == "update_cancel":
                    if self.update_owner == owner:
                        self.update_owner = None
                    return {}
                self.prepare_update(owner)
                if action == "update_shutdown":
                    self.stopping.set()
                    threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
                return dict(pid=os.getpid())
            if self.update_reserved() and action not in ("snapshot", "permission_status", "diagnose"):
                raise RuntimeError("UTA 正在更新，请完成或取消更新后再操作")
        if action == "snapshot":
            return self.snapshot()
        if action == "ssh_hosts":
            return discover_ssh_hosts()
        if action == "diagnose":
            with self.lock:
                device = next((d for d in self.devices if d["busid"] == request.get("busid")), None)
            if device is None:
                raise ValueError("设备未连接到本机")
            return inspect_devices([device])[device["busid"]]
        if action == "permission_status":
            info = uta_privileged.status()
            with self.lock:
                self.local_access = info
            return info
        if action == "server_import":
            aliases = request.get("aliases")
            if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) for a in aliases):
                raise ValueError("请选择需要导入的 SSH 别名")
            available = {h["alias"] for h in discover_ssh_hosts()["hosts"]}
            if any(alias not in available for alias in aliases):
                raise ValueError("SSH 配置已变化，请刷新后重新选择")
            with self.lock:
                imported, skipped = [], []
                for alias in dict.fromkeys(aliases):
                    existing = next((s for s in self.config["servers"]
                                     if s["target"].lower() == alias.lower()), None)
                    if existing:
                        skipped.append(existing)
                        continue
                    item = validate_server(dict(name=alias, target=alias, use_ssh_config=True))
                    self.config["servers"].append(item)
                    imported.append(item)
                self.save()
            return dict(imported=imported, skipped=skipped)
        if action == "server_save":
            item = validate_server(request["server"])
            with self.lock:
                previous = next((s for s in self.config["servers"] if s["id"] == item["id"]), None)
                # A display-name edit does not change an existing device tunnel.
                name_only = previous is not None and all(
                    item[k] == previous.get(k, False if k == "use_ssh_config" else None)
                    for k in item if k != "name")
                if not name_only and (item["id"] in self.busy or any(
                    s["server_id"] == item["id"] and
                    (s["state"] in ACTIVE | {"disconnecting"} or s["busid"] in self.busy
                     or s.get("remote_cleanup_pending"))
                    for s in self.sessions.values()
                )):
                    raise RuntimeError("请先断开此服务器上的设备并等待操作结束")
                self.config["servers"] = [s for s in self.config["servers"] if s["id"] != item["id"]] + [item]
                if not name_only:
                    self.checks.pop(item["id"], None)
                self.save()
            return item
        if action == "server_remove":
            sid = request["id"]
            with self.lock:
                if sid in self.busy or any(
                    s["server_id"] == sid and (s["state"] != "local" or s["busid"] in self.busy
                    or s.get("remote_cleanup_pending")) for s in self.sessions.values()
                ):
                    raise RuntimeError("请先释放此服务器上的设备再删除")
                self.config["servers"] = [s for s in self.config["servers"] if s["id"] != sid]
                self.checks.pop(sid, None)
                self.save()
            return {}
        if action in ("server_check", "server_install"):
            server = self.server(request["id"])
            self.background(server["id"], lambda: self.check_server(server, action == "server_install"))
            return {"started": True}
        if action == "server_manual":
            return manual_install_plan(self.server(request["id"]))
        if action in ("reconnect_confirm", "reconnect_reject", "reconnect_forget"):
            busid = str(request["busid"])
            # Confirmation checks the physical device again, not just a UI snapshot.
            fresh = json.loads(run([str(NATIVE), "list"], timeout=8)) if action == "reconnect_confirm" else []
            if fresh:
                access = inspect_devices(fresh)
                for device in fresh:
                    device["access"] = access.get(device["busid"])
            with self.lock:
                item = self.sessions.get(busid)
                if not item or busid in self.busy:
                    raise RuntimeError("设备正在处理或连接已结束，请刷新")
                if action == "reconnect_forget":
                    self.remember_ids(item, [])
                    ids = [item["reconnect_anchor"]["vidpid"]]
                    item["reconnect_ids"] = ids
                    item["reconnect_anchor"]["allowed_vidpids"] = ids
                    item.pop("reconnect_candidate", None)
                    return {}
                candidate = item.get("reconnect_candidate")
                if (not candidate or candidate["token"] != request.get("token")
                        or item.get("state") not in ("waiting_reconnect", "error")
                        or not item.get("reconnect_enabled") or item.get("remote_cleanup_pending")
                        or item.get("reconnect_deadline", 0) <= time.time()):
                    raise RuntimeError("确认已失效，设备状态或等待时间已变化，请刷新")
                if action == "reconnect_reject":
                    item.update(reconnect_deadline=0, state="local", message="已取消重连，设备留在本机")
                    item.pop("reconnect_candidate", None)
                    return {}
                current = next((d for d in fresh if d["busid"] == busid), None)
                if (current is None or usb_identity(current) != usb_identity(candidate)
                        or not confirmable_mode(item["reconnect_anchor"], current)):
                    item.pop("reconnect_candidate", None)
                    raise RuntimeError("设备已拔出或标识再次变化，请等待新的确认提示")
                ids = sorted(set(item.get("reconnect_ids", []) +
                                 [item["reconnect_anchor"]["vidpid"], current["vidpid"]]))
                self.remember_ids(item, ids)
                item["reconnect_ids"] = ids
                item["reconnect_anchor"]["allowed_vidpids"] = ids
                item.pop("reconnect_candidate", None)
                item.update(reconnect_retry_at=0, message="已记住设备模式，正在重新接入原服务器…")
                self.devices = fresh
                self.scan_error = ""
            self.retry_reconnect()
            return {}
        if action == "reconnect_policy":
            busid, enabled = str(request["busid"]), request["enabled"]
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be boolean")
            allowed = reconnect_ids(request["vidpids"]) if "vidpids" in request else None
            with self.lock:
                if busid in self.busy:
                    raise RuntimeError("设备正在处理，请稍后修改重连设置")
                item = self.sessions.get(busid)
                if not item:
                    raise ValueError("请先接入设备")
                if allowed is not None:
                    ids = allowed
                    ids = sorted(set(ids + [item["reconnect_anchor"]["vidpid"], item["vidpid"]]))
                    self.remember_ids(item, ids)
                    item["reconnect_ids"] = ids
                    item["reconnect_anchor"]["allowed_vidpids"] = ids
                    item.pop("reconnect_candidate", None)
                item["reconnect_enabled"] = enabled
                if not enabled:
                    item.pop("reconnect_candidate", None)
                    item["reconnect_deadline"] = 0
                    if item["state"] == "waiting_reconnect":
                        item.update(state="local", message="自动重连已取消；云端清理仍会继续" if item.get("remote_cleanup_pending") else "自动重连已取消，设备在本地")
            return {}
        if action in ("connect", "disconnect"):
            busid = str(request["busid"])
            if not BUSID.fullmatch(busid):
                raise ValueError("Invalid USB busid")
            if action == "connect":
                mode = request.get("access_mode", "auto")
                reconnect = request.get("reconnect_enabled")
                ids = reconnect_ids(request["reconnect_ids"]) if "reconnect_ids" in request else None
                if reconnect is not None and not isinstance(reconnect, bool):
                    raise ValueError("reconnect_enabled must be boolean")
                if mode not in ("auto", "standard", "authorized"):
                    raise ValueError("Invalid access mode")
                server = self.server(request["server_id"])
                with self.lock:
                    if not any(d["busid"] == busid for d in self.devices):
                        raise ValueError("设备未连接到本机")
                self.background(busid, lambda: self.connect(busid, server, mode, reconnect, allowed_ids=ids))
            else:
                self.background(busid, lambda: self.disconnect(busid))
            return {"started": True}
        if action == "shutdown":
            self.close()
            threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return {}
        raise ValueError("Unknown command")

    def close(self):
        self.stopping.set()
        for busid in list(self.processes):
            self.cleanup(busid)


def daemon():
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (HOME / "daemon.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    # Hold the previous daemon's lock for this process lifetime during upgrades.
    # That prevents both versions from independently claiming the same USB.
    legacy_lock = None
    if HOME == DEFAULT_HOME:
        old_lock = LEGACY_HOME / "daemon.lock"
        if old_lock.exists():
            legacy_lock = old_lock.open("r")
            try:
                fcntl.flock(legacy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("请先通过旧版 UTA 菜单停止后台服务，再启动新版")
        migrate_config(HOME, LEGACY_HOME)
    if SOCKET.exists():
        if SOCKET.stat().st_uid != os.getuid():
            raise RuntimeError("IPC socket belongs to a different user")
        SOCKET.unlink()
    controller = Controller()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(10)
            try:
                line = self.rfile.readline(65537)
                if len(line) > 65536:
                    raise ValueError("Request too large")
                result = controller.handle(json.loads(line))
                response = dict(ok=True, result=result)
            except Exception as exc:
                response = dict(ok=False, error=str(exc))
            try:
                self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            except OSError:
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    os.umask(0o077)
    service = Server(str(SOCKET), Handler)
    os.chmod(SOCKET, 0o600)

    def finish(*_):
        controller.close()
        service.server_close()
        SOCKET.unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    atexit.register(controller.close)
    service.serve_forever(poll_interval=0.2)


def request(data, start=True):
    def send():
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(15)
            sock.connect(str(SOCKET))
            sock.sendall((json.dumps(data) + "\n").encode())
            return json.loads(sock.makefile("rb").readline(2 * 1024 * 1024))
    try:
        return send()
    except (ConnectionRefusedError, FileNotFoundError):
        if not start:
            raise
        HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (HOME / "daemon.log").open("a") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
        for _ in range(60):
            time.sleep(0.1)
            try:
                return send()
            except (ConnectionRefusedError, FileNotFoundError):
                continue
        raise RuntimeError("后台服务无法启动，请查看 " + str(HOME / "daemon.log"))


def main():
    parser = argparse.ArgumentParser(prog="uta", description="UTA — Mac USB to remote Linux via SSH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="run controller in foreground")
    sub.add_parser("status", help="JSON state: servers, devices and sessions")
    sub.add_parser("devices", help="JSON list of physical USB devices")
    diag = sub.add_parser("diagnose", help="read-only USB driver and serial-port ownership check")
    diag.add_argument("--device", required=True)
    sub.add_parser("permission", help="check the local authorized USB helper; enable it in UTA")
    sub.add_parser("stop", help="stop background controller and close tunnels")
    raw = sub.add_parser("rpc", help="JSON control API used by the desktop UI")
    raw.add_argument("json")
    server = sub.add_parser("server").add_subparsers(dest="server_command", required=True)
    server.add_parser("list")
    server.add_parser("discover", help="list literal aliases from ~/.ssh/config and Includes")
    imp = server.add_parser("import", help="import selected SSH aliases without connecting")
    imp.add_argument("aliases", nargs="+")
    add = server.add_parser("add")
    add.add_argument("--id", default="")
    add.add_argument("--name", required=True)
    add.add_argument("--target", required=True, help="user@host or SSH config alias")
    add.add_argument("--port", type=int, default=22)
    add.add_argument("--identity", default="")
    add.add_argument("--use-ssh-config", action="store_true", help="inherit SSH port and identities")
    for cmd in ("remove", "check", "install", "manual"):
        p = server.add_parser(cmd)
        p.add_argument("id")
    con = sub.add_parser("connect")
    con.add_argument("--device", required=True, help="busid from devices")
    con.add_argument("--server", required=True, help="ID from server list")
    con.add_argument("--access", choices=("auto", "standard", "authorized"), default="auto")
    con.add_argument("--reconnect", choices=("auto", "off"), help="bounded same-device mode reconnection (default: enabled)")
    con.add_argument("--reconnect-id", action="append", help="explicitly allow a VID:PID after re-enumeration; repeat for multiple IDs")
    dis = sub.add_parser("disconnect")
    dis.add_argument("--device", required=True)
    args = parser.parse_args()
    try:
        if args.command == "daemon":
            daemon()
            return 0
        if args.command == "rpc":
            data = json.loads(args.json)
        elif args.command in ("status", "devices"):
            data = {"action": "snapshot"}
        elif args.command == "stop":
            data = {"action": "shutdown"}
        elif args.command == "diagnose":
            data = {"action": "diagnose", "busid": args.device}
        elif args.command == "permission":
            data = {"action": "permission_status"}
        elif args.command == "server":
            cmd = args.server_command
            if cmd == "add":
                data = {"action": "server_save", "server": vars(args)}
            elif cmd == "list":
                data = {"action": "snapshot"}
            elif cmd == "discover":
                data = {"action": "ssh_hosts"}
            elif cmd == "import":
                data = {"action": "server_import", "aliases": args.aliases}
            else:
                data = {"action": "server_" + cmd, "id": args.id}
        elif args.command == "connect":
            data = dict(action="connect", busid=args.device, server_id=args.server, access_mode=args.access)
            if args.reconnect:
                data["reconnect_enabled"] = args.reconnect == "auto"
            if args.reconnect_id is not None:
                data["reconnect_ids"] = args.reconnect_id
        else:
            data = dict(action="disconnect", busid=args.device)
        output = request(data, start=args.command != "stop")
        if output.get("ok"):
            if args.command == "devices":
                output["result"] = output["result"]["devices"]
            elif args.command == "server" and args.server_command == "list":
                output["result"] = output["result"]["servers"]
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output.get("ok") else 1
    except Exception as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
