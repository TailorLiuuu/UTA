"""Opt-in CH340 USB/IP throughput tuning; never interprets device firmware.

Builds the pinned GPL-2.0 driver for the running Linux 5.15 kernel. Loading is
session-only and requires all CH340 ports disconnected and a real root login.
"""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile

SOURCE_SHA256 = '0568953df7e73445ca16e21e685a8c06a3c33891e6976be34e28f6eee7cd1912'
SOURCE_GZIP_B64 = ''  # Filled by scripts/build-server.py; no runtime downloads.
MODULE = 'ch341_uta'
REVISION = 'uta-preview7'
STORE = Path('/var/lib/uta-ch341')
SYS_MODULE = Path('/sys/module')
SERIAL_DRIVER = Path('/sys/bus/usb-serial/drivers/ch341-uart')
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')


def supported(kernel):
    return bool(re.fullmatch(r'5\.15\.[0-9]+-[A-Za-z0-9._+-]+', kernel))


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def status():
    kernel = platform.release()
    loaded = (SYS_MODULE / MODULE).exists()
    managed = (read(SYS_MODULE / MODULE / 'version') == REVISION and
               read(SYS_MODULE / MODULE / 'parameters/uta_bulk_out_bytes') == '4096')
    state = 'enabled' if loaded and managed else ('external_driver' if loaded else 'disabled')
    return dict(state=state, kernel=kernel, supported=supported(kernel),
                bulk_out_bytes=4096 if loaded and managed else None,
                build_available=(Path('/lib/modules') / kernel / 'build/Makefile').is_file(),
                ports=sorted(p.name for p in SERIAL_DRIVER.glob('ttyUSB*')),
                persistent=False, revision=REVISION)


def root_only():
    # Existing sudo policies may delegate ordinary uta-server attach/list to
    # users. Loading kernel code must not inherit that delegated authority.
    if os.geteuid() != 0 or int(os.environ.get('SUDO_UID', '0')) != 0:
        raise PermissionError('Run from an administrator root shell (sudo -i), not delegated uta-server sudo')


def run(argv, timeout=30):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                env=ENV, cwd='/')
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'{Path(argv[0]).name} timed out; driver state must be checked') from exc
    if result.returncode:
        raise RuntimeError(f'{Path(argv[0]).name}: {(result.stderr or result.stdout)[-2500:].strip()}')
    return result.stdout.strip()


def source():
    raw = (gzip.decompress(base64.b64decode(SOURCE_GZIP_B64)) if SOURCE_GZIP_B64 else
           (Path(__file__).resolve().parents[1] / 'vendor/linux-ch341/ch341.c').read_bytes())
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('CH341 source checksum mismatch')
    text = raw.decode()
    needle = '\t.num_ports         = 1,'
    if text.count(needle) != 1:
        raise RuntimeError('Unexpected CH341 source layout')
    text = text.replace(needle, needle + '\n\t.bulk_out_size     = 4096, /* USB/IP: amortize network round trips */')
    text += '\nstatic unsigned int uta_bulk_out_bytes = 4096;\n'
    text += 'module_param(uta_bulk_out_bytes, uint, 0444);\n'
    text += 'MODULE_PARM_DESC(uta_bulk_out_bytes, "Fixed USB/IP transmit buffer; read only");\n'
    text += f'MODULE_VERSION("{REVISION}");\n'
    return text


def trusted(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError(f'Unsafe owner, permissions or symlink: {path}')


def store():
    # Protect every ancestor before creating or using privileged build files.
    for path in (Path('/var'), Path('/var/lib')):
        trusted(path, directory=True)
    STORE.mkdir(mode=0o700, exist_ok=True)
    trusted(STORE, directory=True)
    if STORE.stat().st_mode & 0o077:
        raise RuntimeError('Tuning directory must have mode 0700')
    return STORE


def require_supported():
    kernel = platform.release()
    if not supported(kernel):
        raise RuntimeError('This preview supports Linux 5.15 only; leave other kernels on their system driver')
    return kernel


def build():
    root_only()
    kernel = require_supported()
    base = store()
    headers = Path('/lib/modules') / kernel / 'build'
    if not (headers / 'Makefile').is_file():
        raise RuntimeError(f'Install build-essential and linux-headers-{kernel} first')
    if not shutil.which('make', path=ENV['PATH']):
        raise RuntimeError('Install build-essential first')
    # Fixed private build directory, trusted source and kernel headers only.
    work = Path(tempfile.mkdtemp(prefix='build-', dir=base))
    try:
        (work / 'ch341_uta.c').write_text(source())
        (work / 'Makefile').write_text('obj-m += ch341_uta.o\n')
        run(['/usr/bin/make', '-s', '-C', str(headers), f'M={work}', 'modules'], timeout=600)
        binary = work / 'ch341_uta.ko'
        if run(['/sbin/modinfo', '-F', 'vermagic', str(binary)]).split()[0] != kernel:
            raise RuntimeError('Built module does not match running kernel')
        if run(['/sbin/modinfo', '-F', 'version', str(binary)]) != REVISION:
            raise RuntimeError('Built module revision mismatch')
        manifest = dict(kernel=kernel, revision=REVISION,
                        sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        # Publish as a single rename; previous successful builds remain intact.
        (work / 'manifest.json').write_text(json.dumps(manifest))
        target = base / (kernel + '-' + manifest['sha256'][:16])
        if target.exists():
            trusted(target, directory=True)
            verify(target, kernel)
        else:
            work.rename(target)
        return dict(state='built', directory=str(target), **manifest)
    finally:
        if work.exists():
            shutil.rmtree(work)


def verify(directory, kernel):
    trusted(directory, directory=True)
    manifest = directory / 'manifest.json'
    binary = directory / 'ch341_uta.ko'
    trusted(manifest)
    trusted(binary)
    data = json.loads(manifest.read_text())
    if (data.get('kernel') != kernel or data.get('revision') != REVISION or
            data.get('sha256') != hashlib.sha256(binary.read_bytes()).hexdigest()):
        raise RuntimeError('Built module manifest/checksum mismatch; rebuild first')
    return binary


def idle(check_lockdown=False):
    ports = status()['ports']
    if ports:
        raise RuntimeError('Disconnect every CH340/CH341 device first; active ports: ' + ', '.join(ports))
    lockdown = read(Path('/sys/kernel/security/lockdown'))
    if check_lockdown and ('[integrity]' in lockdown or '[confidentiality]' in lockdown):
        raise RuntimeError('Kernel lockdown blocks this unsigned preview module; keep the system driver')


def enable():
    root_only()
    kernel = require_supported()
    info = status()
    if info['state'] == 'enabled':
        return info
    if info['state'] == 'external_driver':
        raise RuntimeError('A different ch341_uta driver is loaded; restore the system driver before enabling')
    base = store()
    candidates = sorted(base.glob(kernel + '-*'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError('Run uta-server tune-ch341 build first')
    binary = verify(candidates[0], kernel)
    idle(check_lockdown=True)
    if (SYS_MODULE / 'ch341').exists():
        run(['/sbin/rmmod', 'ch341'])
    try:
        run(['/sbin/modprobe', 'usbserial'])
        run(['/sbin/insmod', str(binary)])
        result = status()
        if result['state'] != 'enabled':
            raise RuntimeError('Loaded driver failed verification')
        return result
    except Exception as exc:
        # No force unload. If a hotplug races us, a busy module remains intact.
        try:
            if (SYS_MODULE / MODULE).exists():
                run(['/sbin/rmmod', MODULE])
            run(['/sbin/modprobe', 'ch341'])
        except Exception as rollback:
            raise RuntimeError(f'{exc}; restore failed: {rollback}') from exc
        raise RuntimeError(f'{exc}; system ch341 restored') from exc


def disable():
    root_only()
    if status()['state'] == 'external_driver':
        raise RuntimeError('Refusing to unload an unrecognized ch341_uta module')
    idle()
    if (SYS_MODULE / MODULE).exists():
        run(['/sbin/rmmod', MODULE])
    run(['/sbin/modprobe', 'ch341'])
    return status()


def dispatch(action):
    if action == 'status':
        return status()
    # Serialize privileged builds and module switches across concurrent callers.
    root_only()
    import fcntl
    base = store()
    lock = base / 'lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        trusted(lock)
        with os.fdopen(fd, 'w') as handle:
            fd = None
            fcntl.flock(handle, fcntl.LOCK_EX)
            return {'build': build, 'enable': enable, 'disable': disable}[action]()
    finally:
        if fd is not None:
            os.close(fd)


def bundle_agent(agent):
    """Build a single-file agent for both source CLI uploads and releases."""
    source()  # Verify the pinned upstream source before packaging.
    raw = (Path(__file__).resolve().parents[1] / 'vendor/linux-ch341/ch341.c').read_bytes()
    blob = base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    module = Path(__file__).read_text().replace("SOURCE_GZIP_B64 = ''", 'SOURCE_GZIP_B64 = ' + repr(blob), 1)
    bootstrap = 'import types\nuta_ch341 = types.ModuleType("uta_ch341")\nexec(compile(' + repr(module) + ', "<bundled uta_ch341>", "exec"), uta_ch341.__dict__)'
    if agent.count('import uta_ch341\n') != 1:
        raise RuntimeError('Unexpected agent import layout')
    return agent.replace('import uta_ch341\n', bootstrap + '\n')
