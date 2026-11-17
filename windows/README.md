# UTA Windows preview

Windows USB exporter with a PowerShell WinForms desktop UI and Python CLI.
Uses installed usbipd-win 5.3.x and OpenSSH (prefers Git for Windows OpenSSH to
avoid redirected-pipe hangs observed with Windows inbox OpenSSH 9.5); the receiving Linux server
requires uta-server Preview 15+, vhci_hcd and root or narrowly delegated sudo.
No chip-specific flashing protocol is included.

Install from PowerShell: `./install.ps1 -Python C:\path\python.exe`.
Use the desktop UTA shortcut (Run as administrator for bind/unbind). Do not
change global PowerShell policy; if a local script is blocked, review it and
use your organization's approved per-process execution policy.

CLI in the installed folder:

```
uta.cmd server-add "My server" my-ssh-alias
uta.cmd status
uta.cmd check --server SERVER_ID
uta.cmd connect --device BUSID --server SERVER_ID
uta.cmd disconnect --device BUSID
uta.cmd cleanup --device BUSID
```

Configure SSH aliases/known host keys in the Windows account's `.ssh` folder.
Private keys stay on Windows. The GUI never asks for SSH or sudo passwords.
The controller rejects non-Linux targets before binding USB. Session and client
credentials use the same UTA authentication protocol; logs do not contain them.
The local relay permits only the selected USB/IP import, with bounded streaming
buffers. Local identity/config/state live in `%LOCALAPPDATA%\UTAWindows`, with
ACLs restricted to the current account, SYSTEM and Administrators.

Before using this preview, ensure the usbipd-win inbound firewall rule is disabled
or appropriately restricted: USB/IP on port 3240 has no authentication. UTA uses
loopback plus SSH and does not open firewall ports. Do not run another exporter
or auto-attach watcher for the selected device. Already-shared devices are refused.

Disconnect terminates the data stream and only that client's cloud session.
The 60-second cloud lease covers abrupt Windows/network loss. Explicit cleanup
retries are retained after remote failure. This preview does not yet reconnect
mode-switching USB automatically, run at startup, or provide a signed MSI.
Closing the GUI leaves active sessions running; disconnect before shutting down.
After a forced process termination, usbipd may retain the local sharing flag;
verify the device is no longer attached, then unbind that specific device in
usbipd before reconnecting. This initial preview requires Python 3.9+ and Git
for Windows on the tested configuration; it is not a standalone EXE installer.

Encrypted file transfer is also available through `uta.cmd send`, `transfers`
and `run`. It reuses saved server configurations and the client's identity,
supports Linux/macOS/Windows receivers, and does not bind or open USB devices.
See [the transfer guide](../docs/TRANSFERS.md) (included as `TRANSFERS.md` in the
preview ZIP) for checksummed resume, project isolation and explicit tool execution.

## Authorize a development Mac

Select a connected device and its server, then click **Authorize client** and paste the Mac's public ID from `uta port identity --to SERVER`. This grants access to that device for 24 hours; **Revoke client** stops access, including active operations. No full client credential is shared. The receiver must report `device_operations: 1`. See `REMOTE-DEVICES.md` for `uta port list/share/run/logs` and reconnection limits.
