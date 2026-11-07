"""Client-scoped management credentials. No USB data-path work or dependencies."""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

PROTOCOL = 1
MAX_BYTES = 2048


def identifier(secret):
    return hashlib.sha256(b'UTA control client v1\0' + bytes.fromhex(secret)).hexdigest()


def validate(value):
    message = 'Invalid UTA client credential'
    if not isinstance(value, dict) or set(value) != {'client_id', 'secret'}:
        raise PermissionError(message)
    if any(not isinstance(value[k], str) or not re.fullmatch(r'[0-9a-f]{64}', value[k])
           for k in ('client_id', 'secret')):
        raise PermissionError(message)
    if not hmac.compare_digest(identifier(value['secret']), value['client_id']):
        raise PermissionError(message)
    return value


def encode(value):
    return json.dumps(validate(value), separators=(',', ':')).encode() + b'\n'


def read_stream(stream):
    raw = stream.readline(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
        raise PermissionError('UTA client credential required; select a client credential file')
    try:
        return validate(json.loads(raw))
    except (ValueError, TypeError):
        raise PermissionError('Invalid UTA client credential') from None


def secure_file(fd, owners):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o077:
        raise PermissionError('UTA credential file must be private (0600) and owned by its user')


def read_file(path, owners=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        secure_file(stream.fileno(), {os.getuid()} if owners is None else owners)
        value = read_stream(stream)
        if stream.read(1):
            raise PermissionError('Invalid UTA client credential file')
        return value


def credential(home, server_id):
    """Stable local seed, with independent credentials for each saved server.

    Keep outside config.json and diagnostics; copying server settings alone must
    not copy a client's identity. Never regenerate a corrupt existing identity.
    """
    if not isinstance(server_id, str) or not re.fullmatch(r'[0-9a-f]{32}', server_id):
        raise ValueError('Invalid UTA server configuration ID')
    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = home.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('UTA identity directory must be private and owned by its user')
    lockfd = os.open(home / '.client-identity.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(lockfd, 'r+b') as lock:
        secure_file(lock.fileno(), {os.getuid()})
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = home / '.client-identity.json'
        try:
            seed = read_file(path)
        except FileNotFoundError:
            secret = secrets.token_hex(32)
            seed = dict(client_id=identifier(secret), secret=secret)
            export_file(path, seed)
        key = hmac.new(bytes.fromhex(seed['secret']), b'UTA server scope v1\0' + server_id.encode(), hashlib.sha256).hexdigest()
        return dict(client_id=identifier(key), secret=key)


def export_file(path, value):
    data = encode(value)
    # Never overwrite another client's credential or follow a destination link.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
