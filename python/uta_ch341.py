"""Opt-in CH340 USB/IP throughput tuning; never interprets device firmware.

Builds the pinned GPL-2.0 driver for Linux 5.4 or 5.15. Loading is
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
REVISION = 'uta-ch341-tx2'
STORE = Path('/var/lib/uta-ch341')
SYS_MODULE = Path('/sys/module')
SERIAL_DRIVER = Path('/sys/bus/usb-serial/drivers/ch341-uart')
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')


def supported(kernel):
    return bool(re.fullmatch(r'5\.(?:4|15)\.[0-9]+-[A-Za-z0-9._+-]+', kernel))


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def status():
    kernel = platform.release()
    loaded = (SYS_MODULE / MODULE).exists()
    managed = (read(SYS_MODULE / MODULE / 'version') == REVISION and
               read(SYS_MODULE / MODULE / 'parameters/uta_bulk_in_bytes') == '4096' and
               read(SYS_MODULE / MODULE / 'parameters/uta_bulk_out_bytes') == '8192')
    state = 'enabled' if loaded and managed else ('external_driver' if loaded else 'disabled')
    loaded_revision = read(SYS_MODULE / MODULE / 'version')
    legacy = (loaded and read(SYS_MODULE / MODULE / 'parameters/uta_bulk_out_bytes') == '4096' and (
              (loaded_revision == 'uta-preview7' and
               not (SYS_MODULE / MODULE / 'parameters/uta_bulk_in_bytes').exists()) or
              (loaded_revision == 'uta-ch341-rx1' and
               read(SYS_MODULE / MODULE / 'parameters/uta_bulk_in_bytes') == '4096')))
    dependencies = missing_dependencies(kernel)
    lockdown = read(Path('/sys/kernel/security/lockdown'))
    return dict(state=state, kernel=kernel, supported=supported(kernel),
                setup_available=True, legacy_driver=legacy,
                missing_dependencies=dependencies,
                dependency_install_supported=Path('/usr/bin/apt-get').is_file(),
                lockdown=('[integrity]' in lockdown or '[confidentiality]' in lockdown),
                bulk_in_bytes=4096 if loaded and managed else None,
                bulk_out_bytes=8192 if loaded and managed else None,
                build_available=(Path('/lib/modules') / kernel / 'build/Makefile').is_file(),
                ports=sorted(p.name for p in SERIAL_DRIVER.glob('ttyUSB*')),
                persistent=False, revision=REVISION, loaded_revision=loaded_revision or None)


def missing_dependencies(kernel):
    packages = []
    if not all(shutil.which(tool, path=ENV['PATH']) for tool in ('make', 'gcc', 'ld')):
        packages.append('build-essential')
    if not (Path('/lib/modules') / kernel / 'build/Makefile').is_file():
        packages.append('linux-headers-' + kernel)
    return packages


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


def source(kernel=None):
    if kernel is not None and not supported(kernel):
        raise RuntimeError('Unsupported CH341 source target; expected Linux 5.4 or 5.15')
    raw = (gzip.decompress(base64.b64decode(SOURCE_GZIP_B64)) if SOURCE_GZIP_B64 else
           (Path(__file__).resolve().parents[1] / 'vendor/linux-ch341/ch341.c').read_bytes())
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('CH341 source checksum mismatch')
    text = raw.decode()
    if kernel is not None and kernel.startswith('5.4.'):
        # 5.4 usb_serial_driver.port_remove returns int; the pinned 5.15
        # implementation returns void. Preserve its cleanup and adapt the ABI.
        anchor = 'static struct usb_serial_driver ch341_device = {'
        callback = '\t.port_remove       = ch341_port_remove,'
        if text.count(anchor) != 1 or text.count(callback) != 1:
            raise RuntimeError('Unexpected CH341 remove callback layout')
        wrapper = ('static int uta_ch341_port_remove_54(struct usb_serial_port *port)\n'
                   '{\n\tch341_port_remove(port);\n\treturn 0;\n}\n\n')
        text = text.replace(anchor, wrapper + anchor).replace(
            callback, '\t.port_remove       = uta_ch341_port_remove_54,')
    needle = '\t.num_ports         = 1,'
    if text.count(needle) != 1:
        raise RuntimeError('Unexpected CH341 source layout')
    text = text.replace(needle, needle +
                        '\n\t.bulk_in_size      = 4096, /* USB/IP: drain UART bursts locally */' +
                        '\n\t.bulk_out_size     = 8192, /* USB/IP: amortize network round trips */')
    # Keep a userspace write slightly larger than 4 KiB in one USB request.
    # The TTY layer otherwise splits at 2 KiB, and usb-serial's default FIFO
    # is PAGE_SIZE. Resize only during probe, before any tty can be opened;
    # usb-serial core owns/frees the replacement FIFO on all cleanup paths.
    include = '#include <linux/kernel.h>'
    probe = ('static int ch341_port_probe(struct usb_serial_port *port)\n{\n'
             '\tstruct ch341_private *priv;\n\tint r;\n')
    opening = ('static int ch341_open(struct tty_struct *tty, struct usb_serial_port *port)\n{\n'
               '\tstruct ch341_private *priv = usb_get_serial_port_data(port);\n\tint r;\n')
    if any(text.count(anchor) != 1 for anchor in (include, probe, opening)):
        raise RuntimeError('Unexpected CH341 transmit setup layout')
    text = text.replace(include, include + '\n#include <linux/kfifo.h>')
    text = text.replace(probe, probe +
        '\tstruct kfifo larger_fifo;\n\n'
        '\t/* Probe runs before the tty is published; no queued bytes or writers. */\n'
        '\tif (port->bulk_out_size && kfifo_size(&port->write_fifo) < 8192) {\n'
        '\t\tr = kfifo_alloc(&larger_fifo, 8192, GFP_KERNEL);\n'
        '\t\tif (r)\n\t\t\treturn r;\n'
        '\t\tkfifo_free(&port->write_fifo);\n'
        '\t\tport->write_fifo = larger_fifo;\n\t}\n')
    text = text.replace(opening, opening +
        '\n\t/* Preserve userspace write boundaries up to the tty core limit. */\n'
        '\tif (tty)\n\t\tset_bit(TTY_NO_WRITE_SPLIT, &tty->flags);\n')
    text += '\nstatic unsigned int uta_bulk_in_bytes = 4096;\n'
    text += 'module_param(uta_bulk_in_bytes, uint, 0444);\n'
    text += 'MODULE_PARM_DESC(uta_bulk_in_bytes, "Fixed USB/IP receive buffer; read only");\n'
    text += '\nstatic unsigned int uta_bulk_out_bytes = 8192;\n'
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
        raise RuntimeError('This preview supports Linux 5.4 and 5.15 only; leave other kernels on their system driver')
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
        (work / 'ch341_uta.c').write_text(source(kernel))
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


def setup(install_dependencies=False):
    """Explicit administrator action; never changes a live USB driver."""
    root_only()
    kernel = require_supported()
    info = status()
    if info['state'] == 'enabled':
        return info
    if info['state'] == 'external_driver' and not info['legacy_driver']:
        raise RuntimeError('Unrecognized CH341 driver; administrator review is required')
    idle(check_lockdown=True)
    packages = missing_dependencies(kernel)
    if packages:
        if not install_dependencies or not Path('/usr/bin/apt-get').is_file():
            raise RuntimeError('Install missing build dependencies first: ' + ', '.join(packages))
        # Fixed package names only; no distribution upgrade, sudoers or boot changes.
        run(['/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get',
             '-o', 'DPkg::Lock::Timeout=60', 'update'], timeout=240)
        run(['/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get',
             '-o', 'DPkg::Lock::Timeout=60', 'install', '-y', '--no-install-recommends',
             *packages], timeout=600)
        if missing_dependencies(kernel):
            raise RuntimeError('Build dependencies are still unavailable after installation')
    build()  # Build before unloading any working driver.
    idle(check_lockdown=True)  # Devices may have arrived during the build.
    fresh = status()
    if fresh['state'] == 'enabled':
        return fresh
    if fresh['state'] == 'external_driver':
        if not fresh['legacy_driver']:
            raise RuntimeError('CH341 driver changed during setup; no module was unloaded')
        run(['/sbin/rmmod', MODULE])
        run(['/sbin/modprobe', 'ch341'])
    return enable()


def dispatch(action, install_dependencies=False):
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
            if action == 'setup':
                return setup(install_dependencies)
            return {'build': build, 'enable': enable, 'disable': disable}[action]()
    finally:
        if fd is not None:
            os.close(fd)


def bundle_agent(agent):
    """Build a single-file agent for both source CLI uploads and releases."""
    # Validate both generated variants before packaging the pinned source.
    for kernel in ('5.4.0-0-generic', '5.15.0-0-generic'):
        source(kernel)
    raw = (Path(__file__).resolve().parents[1] / 'vendor/linux-ch341/ch341.c').read_bytes()
    blob = base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    module = Path(__file__).read_text().replace("SOURCE_GZIP_B64 = ''", 'SOURCE_GZIP_B64 = ' + repr(blob), 1)
    bootstrap = 'import types\nuta_ch341 = types.ModuleType("uta_ch341")\nexec(compile(' + repr(module) + ', "<bundled uta_ch341>", "exec"), uta_ch341.__dict__)'
    if agent.count('import uta_ch341\n') != 1:
        raise RuntimeError('Unexpected agent import layout')
    return agent.replace('import uta_ch341\n', bootstrap + '\n')
