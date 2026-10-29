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
import socket
import struct
import subprocess
import sys
import time
import uta_ch341

VERSION = "0.1.0-preview.7"
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
    return int(os.environ.get("SUDO_UID", os.getuid()))


@contextlib.contextmanager
def locked():
    if os.geteuid() != 0:
        raise PermissionError("Use sudo -n uta-server for attach/detach/list operations")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_record(session):
    record = json.loads((STATE / f"{validate_session(session)}.json").read_text())
    if owner() != 0 and record["owner"] != owner():
        raise PermissionError("Session belongs to a different SSH user")
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


def record_status(record):
    row = matching_port(record)
    result = dict(record, state="disconnected", nodes=[])
    if row:
        result.update(state="enumerating" if row["status"] == 5 else "attached",
                      local_busid=row["local_busid"])
        root = Path("/sys/bus/usb/devices") / row["local_busid"]
        if root.exists() and row["local_busid"] != "0-0":
            # "Attached" means Linux has an enumerated USB device, not that a
            # vendor driver or flashing application has been validated.
            for cls in ("tty", "hidraw"):
                base = Path("/sys/class") / cls
                for entry in base.glob("*"):
                    try:
                        if root.resolve() in (entry / "device").resolve().parents:
                            result["nodes"].append("/dev/" + entry.name)
                    except OSError:
                        pass
            try:
                bus = int((root / "busnum").read_text())
                dev = int((root / "devnum").read_text())
                result["nodes"].append(f"/dev/bus/usb/{bus:03d}/{dev:03d}")
            except (OSError, ValueError):
                pass
        else:
            result["state"] = "enumerating"
    return result


def doctor():
    return dict(version=VERSION, platform=sys.platform, python=sys.version.split()[0],
                route_identity=True, ch341_tuning=uta_ch341.status(),
                uid=owner(), root=os.geteuid() == 0, vhci_available=VHCI.exists(),
                free_ports=sum(p["status"] == 4 for p in ports()),
                message="ready" if VHCI.exists() else "Run sudo modprobe vhci_hcd; kernel must include CONFIG_USBIP_VHCI_HCD")


def attach(session, tcp_port, busid, route=None, auto_reconnect=False):
    validate_session(session)
    if route is not None:
        validate_session(route)
    if not 1024 <= tcp_port <= 65535 or not re.fullmatch(r"[0-9]+-[0-9.:]+", busid):
        raise ValueError("Invalid loopback TCP port or USB busid")
    with locked():
        path = STATE / f"{session}.json"
        if path.exists():
            existing = read_record(session)
            status = record_status(existing)
            if status["state"] in ("attached", "enumerating"):
                return status
            raise RuntimeError("Session already ended; create a new connection")
        if not VHCI.exists():
            subprocess.run(["/sbin/modprobe", "vhci_hcd"], check=True, timeout=10,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with socket.create_connection(("127.0.0.1", tcp_port), timeout=10) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
                record = dict(session=session, owner=owner(), tcp_port=tcp_port, busid=busid,
                              route_id=route, reconnect_enabled=bool(auto_reconnect),
                              vidpid=dev["vidpid"], vhci_port=free["port"],
                              devid=devid, socket=row["socket"],
                              socket_inode=os.fstat(sock.fileno()).st_ino,
                              local_tcp_port=sock.getsockname()[1], created=time.time())
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(record))
                temp.replace(path)
            except Exception:
                (VHCI / "detach").write_text(str(free["port"]))
                raise
        return record_status(record)


def detach(session):
    with locked():
        path = STATE / f"{validate_session(session)}.json"
        if not path.exists():
            return dict(session=session, state="disconnected")
        record = read_record(session)
        if matching_port(record):
            (VHCI / "detach").write_text(str(record["vhci_port"]))
        path.unlink()
        return dict(session=session, state="disconnected")


def list_sessions(session=None):
    with locked():
        if session:
            return record_status(read_record(session))
        result = []
        for path in STATE.glob("*.json"):
            try:
                result.append(record_status(read_record(path.stem)))
            except PermissionError:
                continue
        return result


def main():
    parser = argparse.ArgumentParser(prog="uta-server", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    tune = sub.add_parser("tune-ch341", help="Opt-in Linux 5.15 CH340 USB/IP throughput tuning")
    tune.add_argument("action", choices=("status", "build", "enable", "disable"))
    sub.add_parser("list")
    status = sub.add_parser("status")
    status.add_argument("--session", required=True)
    add = sub.add_parser("attach")
    add.add_argument("--session", required=True)
    add.add_argument("--port", type=int, required=True)
    add.add_argument("--busid", required=True)
    add.add_argument("--route")
    add.add_argument("--auto-reconnect", action="store_true")
    remove = sub.add_parser("detach")
    remove.add_argument("--session", required=True)
    args = parser.parse_args()
    try:
        if sys.platform != "linux":
            raise RuntimeError("This CLI must run on Linux, not on the Mac")
        if args.command == "tune-ch341":
            result = uta_ch341.dispatch(args.action)
        elif args.command == "doctor":
            result = doctor()
        elif args.command == "list":
            result = list_sessions()
        elif args.command == "status":
            result = list_sessions(args.session)
        elif args.command == "attach":
            result = attach(args.session, args.port, args.busid, args.route, args.auto_reconnect)
        else:
            result = detach(args.session)
        print(json.dumps(dict(ok=True, result=result), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
