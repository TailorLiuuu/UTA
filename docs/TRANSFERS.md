# Encrypted transfers and remote tools (preview)

UTA can upload a file or directory and invoke a tool already installed on the
receiver. USB transport and file transport remain separate: a macOS receiver can
receive firmware and execute tools without supporting virtual USB import.
This feature does not include flashing protocols and never chooses a firmware,
device port, flash address, or reset sequence on the user's behalf.

## Design references

- [OpenSSH](https://man.openbsd.org/ssh.1): reuse its encryption, host-key checks,
  SSH keys, ProxyJump and existing account access. There is no extra network port,
  custom cipher, SSH-agent forwarding or root daemon for file transfer.
- [SFTP](https://man.openbsd.org/sftp.1): bounded streaming, durable writes and
  completion by rename. SFTP's unverified append can resume the wrong prefix;
  UTA verifies the prefix and complete file with SHA-256.
- [rsync](https://download.samba.org/pub/rsync/rsync.1): interrupted work is retained,
  then verified before reuse. UTA implements prefix resume, not rsync's rolling
  block-delta algorithm. No rsync installation or source-code copying is required.

UTA uses an application framing protocol over OpenSSH stdin/stdout, not the
SFTP wire protocol. Control records are bounded JSON lines; payload is an exact
binary byte stream. A 1 MiB buffer has no per-block acknowledgement round trip.
Compression is disabled by default for firmware/archive throughput. Disk hashing,
network latency/bandwidth and SSH encryption still bound actual performance.

## One receiver CLI, multiple users

Install one `uta-server` executable. Each SSH invocation runs as the logged-in
account and derives its namespace from a validated client credential, never a
user-supplied display name or arbitrary destination path:

```text
account-private transfer root/
  clients/<client-id>/<workspace>/<manifest-id>/
    record.json
    partial/        # incomplete; never advertised as usable
    files/          # committed package
    runs/           # bounded execution receipts and logs
```

Mac and Windows CLI reuse their existing persistent per-server UTA identity.
An account with two different UTA clients sees two namespaces, even when both
log in as root. Equal file names in different projects/clients cannot overwrite
each other. Different contents in the same project produce different task IDs.
An unchanged package can be retried with zero payload bytes after verification.
Copying a credential intentionally grants access to that client's namespace;
server names alone do not. Keep client credentials private and do not regenerate
them to fix a transport error.

This prevents mistakes through UTA, not a malicious shared-root or same-OS-account
user reading files directly. Remote commands execute with the SSH account's
normal access, not in a sandbox. Use separate OS accounts (or containers/VMs)
for a real security boundary. Different SSH keys for the same root login are
not different OS users. Encryption is in transit; files are not encrypted at rest.

Defaults: Linux `~/.local/share/uta/transfers`, macOS
`~/Library/Application Support/UTA/Transfers`, Windows
`%LOCALAPPDATA%\UTA\Transfers`. POSIX storage uses 0700/0600; Windows applies
current-account/SYSTEM/Administrators ACLs. `UTA_TRANSFER_HOME` can select an
account-controlled test/storage root. Do not run the receiver with sudo.

## Install and select the destination

Build the single-file POSIX receiver with `python3 scripts/build-server.py` and
install `dist/uta-server` as `/usr/local/bin/uta-server` or in your account's bin
directory. Python 3.9+ is required. The same file handles Linux USB commands and
`uta-server transfer serve` on Linux/macOS/Windows before loading Linux modules.
Install only after coordinating any existing deployment; testing can use a copy
at a separate path. This feature does not change Linux kernel modules.

The Windows preview installer provides `uta.cmd` and `uta-server.cmd`, sharing
the same transfer module. Build its allowlisted source package with
`python3 scripts/build-windows.py`. Python and OpenSSH remain prerequisites.

Choose a saved server ID or unique server name; ambiguous names are rejected.
File transfer does not use the USB-ready check. Check its own capability:

```sh
uta transfers check --to build-server
uta send ./firmware.bin --to build-server --workspace project-a
uta send ./firmware-package --to build-server --workspace project-a
uta transfers list --to build-server --workspace project-a
uta transfers status --to build-server --workspace project-a --id TASK_ID
uta transfers remove --to build-server --workspace project-a --id TASK_ID
```

For a receiver outside `/usr/local/bin`, add `--remote-cli /absolute/path/uta-server`.
For Windows, use `--remote-platform windows --remote-cli "C:/path/to/uta-server.cmd"`
and a server SSH configuration that uses the standard Windows cmd shell. Shell
paths using metacharacters are rejected. No global PATH or SSH settings are changed.

The completed result includes the task ID, client ID, workspace, absolute package
directory, entry path, payload bytes sent, reused prefix bytes and elapsed time.
Progress is JSON on stderr; the final result is JSON on stdout. The remote path
comes from the authenticated receiver, not from a guessed `/tmp/firmware.bin`.

## Upload and execute

```sh
uta run --to build-server --workspace project-a --upload ./firmware.bin \
  --timeout 600 -- /usr/local/bin/my-flasher --port /dev/serial/by-id/EXPLICIT_DEVICE '{upload}'
```

Use the actual tool syntax and verify the selected device first. `{upload}` is
replaced by the receiver's absolute file/directory path; `{dir}` is the package
directory. The command runs in that directory. Executable arguments are passed
as an array with `shell=False`, not interpolated into shell code. Source executable
bits are not copied: use an explicit interpreter for uploaded scripts. Shell or
batch scripts require explicitly naming their interpreter (and its quoting rules).

The upload is verified first. Execution receives a separate random run ID,
announced on stderr before invocation. It is never automatically retried:

```sh
uta transfers run-status --to build-server --workspace project-a \
  --id TASK_ID --run-id RUN_ID
```

Keep the printed run ID after a disconnect. An explicit repeat with the same
`--run-id` returns its existing receipt instead of executing again; changed
arguments for that ID are rejected. A crash can leave outcome `unknown`, which
requires device/tool inspection before a new run. This is conservative at-most-once
dispatch per run ID, not a guarantee that a disconnected flash completed.
Nonzero tool status/timeout makes `uta run` exit nonzero. Output is streamed with
stdout/stderr labels and UTF-8 boundaries preserved. Treat arbitrary tool output
as untrusted. Logs can contain tool-emitted secrets; they stay in the private
receiver directory and are never added to the source/release bundle.

## Reliability, limits and current scope

- Interrupted uploads retain the prefix. Retry the same source, server and workspace;
  changed/corrupt prefixes restart only that file. SHA-256 mismatch never commits.
- Successful files are fsynced; a package becomes complete through directory rename
  and an atomic receipt. A crash between these steps can resume safely.
- Files/directories only, with portable relative names. Symlinks, junctions,
  hard links, special files, traversal and case/Unicode collisions are refused.
  Archives are transferred as files; UTA does not automatically extract them.
- Up to 8 GiB and 4096 entries per package. Per SSH account: four concurrent
  upload/command operations, 1024 retained tasks and 64 GiB of declared reservations;
  per client: 16 GiB. Incomplete tasks and execution log reservations count.
- Each command has a 1–3600 second timeout (default 600), 16 MiB output cap and
  at most 32 executions per package. Logs reserve capacity before starting.
  No automatic file eviction or cleanup of other clients' work.
- Run foreground tools. UTA attempts process-tree termination on errors/timeouts;
  detached daemons and commands that modify outside the workspace are not sandboxed.
  Upload/log quotas do not limit disk, memory or CPU used by the invoked tool;
  use OS-level quotas or containers when running untrusted tools. Device selection
  remains explicit: file task isolation does not grant exclusive access to an
  arbitrary serial path passed to an external tool.
- SSH keepalives plus a 120-second no-progress watchdog bound stalled connections.
  Uploads retry up to twice by default; authentication/protocol/checksum errors
  fail immediately. Remote commands never enter that retry loop.
- Initial delivery is CLI-first. Existing desktop server configurations are reused;
  desktop transfer queue/UI, download/pull, delta synchronization, bandwidth
  scheduling and native macOS USB import are not implemented by this feature.

Tests use temporary stores, synthetic credentials and non-device commands. Do
not replace a running app/helper merely to try file transfer; use a separate
test receiver path and keep active USB sessions intact.
