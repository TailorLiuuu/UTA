#!/usr/bin/env python3
"""UTA Linux CLI. Stdlib only. Uses the Linux vhci_hcd sysfs ABI.

All USB sockets are reached on loopback through an SSH reverse tunnel.
Install this file root-owned; do not grant sudo to a user-writable copy.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import socket
import struct
import stat
import subprocess
import sys
import time
import uta_ch341
import uta_auth
import uta_client

VERSION = "0.1.0-preview.15"
VHCI = Path("/sys/devices/platform/vhci_hcd.0")
STATE = Path("/run/uta")
# Keep existing live session records accessible when upgrading an older agent.
if not STATE.exists() and Path("/run/usbbridge").is_dir():
    STATE = Path("/run/usbbridge")


def receive(sock, size):
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise RuntimeError("USB/IP peer closed the connection")
        data.extend(block)
    return bytes(data)


def header(sock, expected):
    version, opcode, status = struct.unpack("!HHI", receive(sock, 8))
    if version != 0x111 or opcode != expected or status:
        raise RuntimeError(
            f"USB/IP request rejected (version={version:#x}, opcode={opcode:#x}, status={status}); "
            "check Mac device permissions / whether another program owns the USB interfaces")


def descriptor(raw):
    fields = struct.unpack("!256s32sIIIHHHBBBBBB", raw)
    return dict(busid=fields[1].split(b"\0")[0].decode("ascii"),
                busnum=fields[2], devnum=fields[3], speed=fields[4],
                vidpid=f"{fields[5]:04x}:{fields[6]:04x}", interfaces=fields[-1])


def parse_status(text):
    rows = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 5 or fields[0] not in ("hs", "ss"):
            continue
        rows.append(dict(hub=fields[0], port=int(fields[1]), status=int(fields[2]),
                         speed=int(fields[3]), devid=int(fields[4], 16),
                         socket=fields[5] if len(fields) > 5 else "",
                         local_busid=fields[6] if len(fields) > 6 else ""))
    return rows


def ports():
    if not VHCI.exists():
        return []
    result = []
    for file in sorted(VHCI.glob("status*")):
        result.extend(parse_status(file.read_text()))
    return result


def validate_session(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("session must be a 32 character hex ID")
    return value


def owner():
    value = os.environ.get("SUDO_UID", str(os.getuid()))
    if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) > 0xfffffffe:
        raise PermissionError("Invalid SSH account identity")
    return int(value)


def trusted_state_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("Unsafe UTA state file owner or permissions")
    if info.st_mode & 0o077:
        os.fchmod(fd, 0o600)


@contextlib.contextmanager
def locked():
    if os.geteuid() != 0:
        raise PermissionError("Use sudo -n uta-server for attach/detach/list operations")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError("Unsafe UTA state directory")
    fd = os.open(STATE / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as handle:
        trusted_state_file(handle.fileno())
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def client_scope(client=None, admin=False):
    if admin:
        if owner() != 0:
            raise PermissionError('Administrator mode requires an actual root login')
        return None
    if client is None:
        raise PermissionError('UTA client credential required; use --client-file or UTA_CLIENT_CREDENTIAL_FILE')
    return uta_client.validate(client)['client_id']


def read_record(session, client=None, admin=False):
    scope = client_scope(client, admin)
    fd = os.open(STATE / f"{validate_session(session)}.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        trusted_state_file(handle.fileno())
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("UTA session record is too large")
    record = json.loads(raw)
    if not admin and (record.get('client_id') != scope or record['owner'] != owner()):
        raise PermissionError('Session is not owned by this UTA client')
    return record


def socket_is_live(record):
    """Match the kernel-held TCP socket, not a reused numerical process fd.

    Linux hides devid/sockfd while vhci is in state 5 (enumerating). The
    transferred socket inode and loopback tuple still identify this connection.
    """
    try:
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            if (len(fields) > 9 and fields[3] == "01" and
                    fields[9] == str(record["socket_inode"]) and
                    fields[1] == f'0100007F:{record["local_tcp_port"]:04X}' and
                    fields[2] == f'0100007F:{record["tcp_port"]:04X}'):
                return True
    except (OSError, KeyError, ValueError):
        pass
    return False


def matching_port(record):
    row = next((p for p in ports() if p["port"] == record["vhci_port"]
                and p["status"] in (5, 6)), None)
    if not row:
        return None
    if "socket_inode" in record:
        if not socket_is_live(record):
            return None
        return row if row["status"] == 5 or row["devid"] == record["devid"] else None
    # Compatibility with old records; never detach a port of uncertain ownership.
    return row if row["devid"] == record["devid"] and row["socket"] == record.get("socket") else None


def restrict_device_node(path, expected_dev, uid):
    """Only discovered device nodes, never opening the serial port to chmod it.

    /dev paths are managed by root/udev. Validate the major/minor before touching
    permissions; the matching VHCI connection is checked again by the caller.
    """
    info = path.lstat()
    major, minor = map(int, expected_dev.strip().split(':'))
    if (not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) != major
            or os.minor(info.st_rdev) != minor):
        raise PermissionError("USB device node identity changed")
    os.chmod(path, 0o600)
    os.chown(path, uid, 0, follow_symlinks=False)


def record_status(record):
    row = matching_port(record)
    result = dict(record, state="disconnected", nodes=[])
    if row:
        result.update(state="enumerating" if row["status"] == 5 else "attached",
                      local_busid=row["local_busid"])
        root = Path("/sys/bus/usb/devices") / row["local_busid"]
        if root.exists() and row["local_busid"] != "0-0":
            node_specs = []
            # "Attached" means Linux has an enumerated USB device, not that a
            # vendor driver or flashing application has been validated.
            for cls in ("tty", "hidraw"):
                base = Path("/sys/class") / cls
                for entry in base.glob("*"):
                    try:
                        if root.resolve() in (entry / "device").resolve().parents:
                            node_specs.append((Path("/dev") / entry.name, (entry / "dev").read_text()))
                    except OSError:
                        pass
            try:
                bus = int((root / "busnum").read_text())
                dev = int((root / "devnum").read_text())
                node_specs.append((Path(f"/dev/bus/usb/{bus:03d}/{dev:03d}"), (root / "dev").read_text()))
            except (OSError, ValueError):
                pass
            if not matching_port(record):
                return dict(record, state="disconnected", nodes=[])
            for node, device_number in node_specs:
                restrict_device_node(node, device_number, record['owner'])
                result['nodes'].append(str(node))
            result['device_access'] = 'owner-only'
        else:
            result["state"] = "enumerating"
    return result


def doctor():
    return dict(version=VERSION, platform=sys.platform, python=sys.version.split()[0],
                route_identity=True, session_auth=uta_auth.PROTOCOL, client_identity=uta_client.PROTOCOL,
                session_lease=True,
                ch341_tuning=uta_ch341.status(),
                uid=owner(), root=os.geteuid() == 0, vhci_available=VHCI.exists(),
                free_ports=sum(p["status"] == 4 for p in ports()),
                message="ready" if VHCI.exists() else "Run sudo modprobe vhci_hcd; kernel must include CONFIG_USBIP_VHCI_HCD")


def save_record(record):
    """Caller holds the state lock; never follow an existing temporary file."""
    path = STATE / (validate_session(record['session']) + '.json')
    temp = path.with_suffix('.lease.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(record, handle)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def lease_tick(session, client):
    """Expire only the original owned socket/port. No USB traffic or replay."""
    with locked():
        try:
            record = read_record(session, client)
        except FileNotFoundError:
            return False
        if not record.get('lease_seconds') or record.get('lease_expired'):
            return False
        if not matching_port(record):
            return False
        if time.monotonic() < record['lease_deadline']:
            return True
        (VHCI / 'detach').write_text(str(record['vhci_port']))
        record['lease_expired'] = True
        save_record(record)
        return False


def watch_lease(session, client):
    validate_session(session)
    client_scope(client)
    if os.geteuid() != 0:
        raise PermissionError('Lease watchdog requires the same privilege as attach')
    print(json.dumps({'ready': session}), flush=True)
    while lease_tick(session, client):
        time.sleep(2)
    return dict(session=session, state='disconnected')


def start_lease_watchdog(session, client):
    # Detached from the SSH connection: it must still run when the Mac loses
    # power/network. The credential is delivered through a private pipe only.
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                              'watch-lease', '--session', session, '--client-stdin'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    try:
        child.stdin.write(uta_client.encode(client)); child.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(5):
                raise RuntimeError('Cloud USB lease watchdog did not start')
        if json.loads(child.stdout.readline(256)).get('ready') != session:
            raise RuntimeError('Invalid cloud USB lease watchdog response')
    except Exception:
        child.terminate()
        try: child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()
        raise
    finally:
        child.stdout.close()
    return child


def attach(session, tcp_port, busid, route=None, auto_reconnect=False, auth=None, client=None, lease_seconds=0):
    scope = client_scope(client)
    validate_session(session)
    if lease_seconds not in (0, 60):
        raise ValueError('Session lease must be disabled or 60 seconds')
    if auth is None:
        raise PermissionError("Session authentication required; update the Mac app")
    uta_auth.validate(auth)
    if (auth['session'], auth['busid'], auth['owner_uid']) != (session, busid, owner()):
        raise PermissionError("Session authentication owner or device mismatch")
    if route is not None:
        validate_session(route)
    if not 1024 <= tcp_port <= 65535 or not re.fullmatch(r"[0-9]+-[0-9.:]+", busid):
        raise ValueError("Invalid loopback TCP port or USB busid")
    with locked():
        path = STATE / f"{session}.json"
        if path.exists():
            existing = read_record(session, client)
            status = record_status(existing)
            if status["state"] in ("attached", "enumerating"):
                return status
            raise RuntimeError("Session already ended; create a new connection")
        if not VHCI.exists():
            subprocess.run(["/sbin/modprobe", "vhci_hcd"], check=True, timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with socket.create_connection(("127.0.0.1", tcp_port), timeout=10) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            uta_auth.client(sock, auth)
            sock.sendall(struct.pack("!HHI32s", 0x111, 0x8003, 0, busid.encode()))
            header(sock, 3)
            dev = descriptor(receive(sock, 312))
            if dev["busid"] != busid or not 1 <= dev["speed"] <= 6:
                raise RuntimeError("USB/IP descriptor does not match requested device")
            hub = "ss" if dev["speed"] >= 5 else "hs"
            free = next((p for p in ports() if p["status"] == 4 and p["hub"] == hub), None)
            if not free:
                raise RuntimeError("No free vhci_hcd port for this USB speed")
            devid = dev["busnum"] << 16 | dev["devnum"]
            sock.settimeout(None)
            (VHCI / "attach").write_text(f'{free["port"]} {sock.fileno()} {devid} {dev["speed"]}')
            try:
                row = next(p for p in ports() if p["port"] == free["port"])
                record = dict(session=session, owner=owner(), client_id=scope, tcp_port=tcp_port, busid=busid,
                              route_id=route, reconnect_enabled=bool(auto_reconnect),
                              vidpid=dev["vidpid"], vhci_port=free["port"],
                              devid=devid, socket=row["socket"],
                              socket_inode=os.fstat(sock.fileno()).st_ino,
                              local_tcp_port=sock.getsockname()[1], created=time.time())
                if lease_seconds:
                    record.update(lease_seconds=lease_seconds, lease_deadline=time.monotonic() + lease_seconds)
                temp = path.with_suffix(".tmp")
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    with os.fdopen(fd, 'w') as handle:
                        handle.write(json.dumps(record))
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                if lease_seconds:
                    start_lease_watchdog(session, client)
            except Exception:
                (VHCI / "detach").write_text(str(free["port"]))
                path.unlink(missing_ok=True)
                raise
        return record_status(record)


def detach(session, client=None, admin=False):
    client_scope(client, admin)
    with locked():
        path = STATE / f"{validate_session(session)}.json"
        if not path.exists():
            return dict(session=session, state="disconnected")
        record = read_record(session, client, admin)
        if matching_port(record):
            (VHCI / "detach").write_text(str(record["vhci_port"]))
        path.unlink()
        return dict(session=session, state="disconnected")


def list_sessions(session=None, client=None, admin=False):
    client_scope(client, admin)
    with locked():
        if session:
            record = read_record(session, client, admin)
            if record.get('lease_seconds') and not admin:
                # Only the owning client's explicit status heartbeat renews it.
                # list/resolve/admin queries cannot keep a lost Mac alive.
                if record.get('lease_expired') or time.monotonic() >= record['lease_deadline']:
                    return dict(record, state='disconnected', nodes=[], disconnect_reason='lease_expired')
                record['lease_deadline'] = time.monotonic() + record['lease_seconds']
                save_record(record)
            return record_status(record)
        result = []
        for path in STATE.glob("*.json"):
            try:
                result.append(record_status(read_record(path.stem, client, admin)))
            except PermissionError:
                continue
        return result


def resolve_route(route, client=None):
    validate_session(route)
    matches = [r for r in list_sessions(client=client)
               if r.get('route_id') == route and r.get('state') == 'attached' and r.get('nodes')]
    if len(matches) != 1:
        raise RuntimeError('No unique ready device for this client and route; wait for reconnection and query again')
    return matches[0]


def main():
    parser = argparse.ArgumentParser(prog="uta-server", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    tune = sub.add_parser("tune-ch341", help="Opt-in Linux 5.4/5.15 CH340 USB/IP throughput tuning")
    tune.add_argument("action", choices=("status", "build", "enable", "disable", "setup"))
    tune.add_argument("--install-dependencies", action="store_true",
                      help="Allow setup to install missing build packages via apt-get")
    listing = sub.add_parser("list")
    status = sub.add_parser("status")
    status.add_argument("--session", required=True)
    add = sub.add_parser("attach")
    add.add_argument("--session", required=True)
    add.add_argument("--port", type=int, required=True)
    add.add_argument("--busid", required=True)
    add.add_argument("--route")
    add.add_argument("--auth-stdin", action="store_true", help="Read one-use session credential from SSH stdin")
    add.add_argument("--auto-reconnect", action="store_true")
    add.add_argument('--lease-seconds', type=int, choices=(0, 60), default=0,
                     help='Opt-in cloud watchdog; owning client must query status at least every 60 seconds')
    watch = sub.add_parser('watch-lease', help=argparse.SUPPRESS)
    watch.add_argument('--session', required=True)
    remove = sub.add_parser("detach")
    remove.add_argument("--session", required=True)
    resolve = sub.add_parser('resolve', help='Resolve an owned route to its current device nodes after reconnection')
    resolve.add_argument('--route', required=True)
    for command in (listing, status, add, remove, resolve, watch):
        group = command.add_mutually_exclusive_group()
        group.add_argument('--client-stdin', action='store_true', help='Read client credential as the first line of stdin')
        group.add_argument('--client-file', help='Private credential file; otherwise use UTA_CLIENT_CREDENTIAL_FILE')
        if command in (listing, status, remove):
            group.add_argument('--admin', action='store_true', help='Explicit root administrator access, including legacy/unowned sessions')
    args = parser.parse_args()
    try:
        if sys.platform != "linux":
            raise RuntimeError("This CLI must run on Linux, not on the Mac")
        client = None
        admin = getattr(args, 'admin', False)
        if args.command in ('list', 'status', 'attach', 'detach', 'resolve', 'watch-lease'):
            if admin:
                client_scope(admin=True)
            elif args.client_stdin:
                client = uta_client.read_stream(sys.stdin.buffer)
            else:
                path = args.client_file or os.environ.get('UTA_CLIENT_CREDENTIAL_FILE')
                if not path:
                    client_scope()
                client = uta_client.read_file(path, owners={0, owner()})
        if args.command == "tune-ch341":
            result = uta_ch341.dispatch(args.action, args.install_dependencies)
        elif args.command == "doctor":
            result = doctor()
        elif args.command == "list":
            result = list_sessions(client=client, admin=admin)
        elif args.command == "status":
            result = list_sessions(args.session, client, admin)
        elif args.command == 'resolve':
            result = resolve_route(args.route, client)
        elif args.command == "attach":
            result = attach(args.session, args.port, args.busid, args.route, args.auto_reconnect,
                            uta_auth.read_config(sys.stdin.buffer) if args.auth_stdin else None, client,
                            lease_seconds=args.lease_seconds)
        elif args.command == 'watch-lease':
            watch_lease(args.session, client)
            return 0
        else:
            result = detach(args.session, client, admin)
        print(json.dumps(dict(ok=True, result=result), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
