"""Unprivileged relay to the signed XPC USB helper. No passwords or sudo commands."""
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

CLIENT = Path(__file__).resolve().parent.parent / "bin/uta-helper-client"


def status():
    if not CLIENT.is_file():
        return dict(available=False, message="当前构建未包含本机 USB 授权组件")
    try:
        proc = subprocess.run([str(CLIENT), "status"], capture_output=True, text=True, timeout=10)
        if proc.returncode:
            return dict(available=False, message=proc.stderr.strip()[-1200:] or "辅助服务未启动")
        data = json.loads(proc.stdout)
        if data.get("protocol") != 1 or not data.get("available"):
            return dict(available=False, message="辅助服务版本不兼容，请在 UTA 中重新启用")
        return dict(available=True, message="本机 USB 接管服务已授权并就绪")
    except Exception as exc:
        return dict(available=False, message=str(exc))


def relay(busid, vidpid, serial):
    # Keep the pathname below Darwin's sockaddr_un limit; directory is mode 0700.
    directory = Path(tempfile.mkdtemp(prefix="uta-usb-", dir="/tmp"))
    endpoint = directory / "stream"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stopped = threading.Event()
    child = None
    connection = None

    def stop(*_):
        stopped.set()
        if connection:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parent = os.getppid()
    try:
        listener.bind(str(endpoint))
        os.chmod(endpoint, 0o600)
        listener.listen(1)
        listener.settimeout(0.25)
        print(json.dumps(dict(socket=str(endpoint))), flush=True)
        deadline = time.monotonic() + 30
        while not stopped.is_set() and os.getppid() == parent and time.monotonic() < deadline:
            try:
                connection, _ = listener.accept()
                break
            except socket.timeout:
                pass
        if connection is None:
            return 1
        listener.close()  # one device, one authenticated XPC lease, one import
        child = subprocess.Popen([str(CLIENT), "export", busid, vidpid, serial],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(10):
                raise RuntimeError("本机 USB 接管服务没有响应")
        line = child.stdout.readline(4096)
        if not line or not json.loads(line).get("ready"):
            raise RuntimeError("本机 USB 接管服务拒绝连接，请检查授权或版本")

        def upstream():
            try:
                while not stopped.is_set():
                    data = connection.recv(65536)
                    if not data:
                        break
                    child.stdin.write(data)
                    child.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                stop()

        def downstream():
            try:
                while not stopped.is_set():
                    data = child.stdout.read1(65536)
                    if not data:
                        break
                    connection.sendall(data)
            except (OSError, ValueError):
                pass
            finally:
                stop()

        threads = [threading.Thread(target=f, daemon=True) for f in (upstream, downstream)]
        for thread in threads:
            thread.start()
        while not stopped.wait(0.2) and os.getppid() == parent:
            pass
        stop()
        for thread in threads:
            thread.join(timeout=2)
        return 0
    finally:
        stop()
        if child:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if connection:
            connection.close()
        listener.close()
        shutil.rmtree(directory)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError("Expected busid, VID:PID and serial")
        sys.exit(relay(*sys.argv[1:]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
