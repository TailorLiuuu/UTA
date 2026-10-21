"""Read-only macOS USB ownership observations. Never opens or claims an interface."""
import plistlib
import subprocess
import time


def walk(node):
    yield node
    for child in node.get("IORegistryEntryChildren", []):
        yield from walk(child)


def match_device(device, roots):
    candidates = [r for r in roots
                  if f'{r.get("idVendor", 0):04x}:{r.get("idProduct", 0):04x}' == device["vidpid"]]
    if device.get("serial"):
        candidates = [r for r in candidates
                      if r.get("USB Serial Number", r.get("kUSBSerialNumberString")) == device["serial"]]
    # Do not attribute another identical board's ports or owners to this device.
    return candidates[0] if len(candidates) == 1 else None


def parse_processes(text, ports):
    processes, current = [], None
    for line in text.splitlines():
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == "p":
            current = dict(pid=int(value), command="", user="", ports=[])
            processes.append(current)
        elif current is not None:
            if field == "c":
                current["command"] = value
            elif field == "u":
                current["user"] = value
            elif field == "n" and value in ports:
                current["ports"].append(value)
    return [p for p in processes if p["ports"]]


def inspect_device(device, roots):
    result = dict(state="unknown", message="尚未确认 USB 占用情况", interfaces=[], ports=[],
                  processes=[], clients=[], checked=time.time(), errors=[],
                  scope="进程检查仅覆盖当前权限可见的串口文件；USB 客户端不等于独占，未发现进程也不代表接口可接管。")
    node = match_device(device, roots)
    if node is None:
        result["message"] = "无法唯一匹配 USB 注册信息，未猜测设备占用者"
        return result
    for child in node.get("IORegistryEntryChildren", []):
        number = child.get("bInterfaceNumber")
        if number is not None:
            drivers = sorted({c["IOClass"] for c in child.get("IORegistryEntryChildren", [])
                              if c.get("IOClass")})
            result["interfaces"].append(dict(number=number, owner=str(child.get("UsbExclusiveOwner", "")),
                                             drivers=drivers))
    result["clients"] = sorted({n.get("IORegistryEntryName", "") for n in walk(node)
                                if "UserClient" in n.get("IOObjectClass", "")} - {""})
    result["ports"] = sorted({n[k] for n in walk(node) for k in ("IOCalloutDevice", "IODialinDevice")
                              if isinstance(n.get(k), str) and n[k].startswith("/dev/")})
    if result["ports"]:
        try:
            proc = subprocess.run(["/usr/sbin/lsof", "-nP", "-Fpcun", *result["ports"]],
                                  text=True, capture_output=True, timeout=3)
            if proc.returncode not in (0, 1) or proc.stderr.strip():
                raise RuntimeError(proc.stderr.strip() or f"lsof exited {proc.returncode}")
            result["processes"] = parse_processes(proc.stdout, result["ports"])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result["errors"].append("串口进程查询未完成：" + str(exc))
    bound = [i for i in result["interfaces"] if i["owner"] or i["drivers"]]
    if result["processes"]:
        result.update(state="process_busy", message="发现程序正在打开本机串口")
    elif bound:
        result.update(state="driver_bound", message="macOS 系统驱动已绑定 USB 接口")
    elif not result["errors"]:
        result.update(state="no_owner_detected", message="未发现已绑定驱动或可见的串口占用进程")
    return result


def inspect_devices(devices):
    try:
        proc = subprocess.run(["/usr/sbin/ioreg", "-a", "-r", "-c", "IOUSBHostDevice", "-l"],
                              capture_output=True, timeout=5, check=True)
        roots = plistlib.loads(proc.stdout)
        return {d["busid"]: inspect_device(d, roots) for d in devices}
    except Exception as exc:
        return {d["busid"]: dict(state="unknown", message="USB 占用信息暂时不可用",
                                interfaces=[], ports=[], processes=[], clients=[], checked=time.time(),
                                errors=[str(exc)], scope="查询失败不能视为设备空闲。") for d in devices}


def connection_error(error, log):
    """Keep the raw protocol error in logs; show the observed local cause first."""
    if "USB/IP request rejected" not in error:
        return error
    denied = [line for line in log.splitlines() if "Access denied" in line]
    busy = [line for line in log.splitlines() if "Resource busy" in line]
    if denied:
        return ("Mac 拒绝接管 USB 接口：权限不足。即使没有程序打开串口，系统驱动仍可能占用接口；"
                "请查看「USB 占用诊断」，并通过 UTA 的「启用 USB 接管」完成系统授权后重新接入。")
    if busy:
        return "Mac USB 接口正在被占用，无法接管；请查看「USB 占用诊断」和 USB 日志。"
    return error
