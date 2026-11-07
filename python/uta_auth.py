"""One-use, mutually authenticated preface; USB/IP bytes are unchanged afterwards.

Secrets travel only over SSH stdin and a local child pipe. Never put this object
in session records, logs, command arguments or exported diagnostics.
"""
import hashlib
import hmac
import json
import re
import secrets
import socket
import time

PROTOCOL = 1
MAGIC = b'UTA-AUTH\x01'
ACK = b'UTA-OK\x01'
MAX_CONFIG = 2048
HANDSHAKE_SECONDS = 3


def validate(value):
    if not isinstance(value, dict) or set(value) != {'key', 'session', 'busid', 'owner_uid'}:
        raise ValueError('Invalid session authentication configuration')
    if not isinstance(value['key'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['key']):
        raise ValueError('Invalid session credential')
    if not isinstance(value['session'], str) or not re.fullmatch(r'[0-9a-f]{32}', value['session']):
        raise ValueError('Invalid authentication session')
    if not isinstance(value['busid'], str) or not re.fullmatch(r'[0-9]{1,3}-[0-9.:]{1,24}', value['busid']):
        raise ValueError('Invalid authentication device')
    if type(value['owner_uid']) is not int or not 0 <= value['owner_uid'] <= 0xfffffffe:
        raise ValueError('Invalid authentication owner')
    return value


def create(session, busid, owner_uid):
    return validate(dict(key=secrets.token_hex(32), session=session, busid=busid, owner_uid=owner_uid))


def read_config(stream):
    raw = stream.readline(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG or not raw.endswith(b'\n'):
        raise ValueError('Session authentication is required; update both UTA clients')
    try:
        return validate(json.loads(raw))
    except (ValueError, TypeError):
        raise ValueError('Invalid session authentication configuration') from None


def encode(config):
    return json.dumps(validate(config), separators=(',', ':')).encode() + b'\n'


def proof(config, role, client_nonce, server_nonce):
    context = json.dumps([PROTOCOL, config['session'], config['busid'], config['owner_uid']],
                         separators=(',', ':')).encode()
    return hmac.digest(bytes.fromhex(config['key']), role + context + client_nonce + server_nonce, hashlib.sha256)


def receive(sock, length, deadline):
    result = bytearray()
    while len(result) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('USB session authentication timed out')
        sock.settimeout(remaining)
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise ConnectionError('USB session authentication closed')
        result.extend(chunk)
    return bytes(result)


def client(sock, config):
    validate(config)
    previous = sock.gettimeout()
    deadline = time.monotonic() + HANDSHAKE_SECONDS
    try:
        sock.settimeout(HANDSHAKE_SECONDS)
        cn = secrets.token_bytes(32)
        sock.sendall(MAGIC + cn)
        response = receive(sock, 64, deadline)
        sn, digest = response[:32], response[32:]
        if not hmac.compare_digest(digest, proof(config, b'server', cn, sn)):
            raise PermissionError('USB session authentication failed')
        sock.sendall(proof(config, b'client', cn, sn))
        if receive(sock, len(ACK), deadline) != ACK:
            raise PermissionError('USB session authentication failed')
    finally:
        sock.settimeout(previous)


def server(sock, config):
    validate(config)
    previous = sock.gettimeout()
    deadline = time.monotonic() + HANDSHAKE_SECONDS
    try:
        request = receive(sock, len(MAGIC) + 32, deadline)
        if request[:len(MAGIC)] != MAGIC:
            raise PermissionError('USB session authentication required')
        cn = request[len(MAGIC):]; sn = secrets.token_bytes(32)
        sock.sendall(sn + proof(config, b'server', cn, sn))
        if not hmac.compare_digest(receive(sock, 32, deadline), proof(config, b'client', cn, sn)):
            raise PermissionError('USB session authentication failed')
        # Acknowledge only once the relay has atomically reserved the lease.
        return ACK
    finally:
        sock.settimeout(previous)
