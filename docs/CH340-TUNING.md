# CH340 / CH341 USB/IP 串口加速

Preview 7 增加可选的 Linux 驱动调优。它调整 USB 发送缓冲，不加入任何芯片烧录协议。支持预览范围为 Linux 5.15；实机验证环境为 Ubuntu 5.15.0-173-generic、CH340 1a86:7523、Mac → SSH → Linux。

## 为什么需要

Linux CH341 驱动默认使用 USB 端点大小作为发送缓冲。测试设备的端点是 32 字节，跨网络时大量小传输反复等待往返。把波特率从 115200 提高到 921600，仍只有约 1.6–1.8 KB/s。将驱动 bulk OUT 缓冲改为 4096 后，外部工具的 4 KiB 写入成功，实测写入阶段平均约 30 KB/s。USB 包大小、设备协议和固件都不因此改变。

调优会影响服务器上使用同一个驱动的所有 CH340/CH341。启用、关闭前必须断开全部对应设备；工具发现仍有 ttyUSB 端口时会拒绝切换，不会强制卸载驱动。不要在烧录或日志采集中切换。

## 安装、检查和启用

1. 更新 Mac App，在服务器菜单中「重新安装云端代理」，或按手动安装说明部署 Release 的 `uta-server`。**仅升级 Mac App 不会升级代理或启用驱动调优。**
2. 在服务器运行只读检查：

```sh
/usr/local/bin/uta-server tune-ch341 status
```

3. 编译、加载内核模块需要完整管理员权限。直接使用 root 登录；如果使用 sudo，先进入管理员 shell：

```sh
sudo -i
unset SUDO_UID
```

`unset` 只在已获得完整 root shell 后执行；工具有意拒绝普通用户通过仅委派 `uta-server` 的 sudo 规则加载内核代码。不会修改 sudoers。

4. 安装当前内核的构建依赖（Ubuntu 示例）：

```sh
apt-get install build-essential linux-headers-$(uname -r)
/usr/local/bin/uta-server tune-ch341 build
```

驱动源码随代理打包并核对固定 SHA-256；构建不下载代码，也不安装缺失依赖。文件写入 root 私有的 `/var/lib/uta-ch341`。命令成功返回 `state: built`。

5. 停止烧录、日志工具，在 UTA 中断开所有 CH340/CH341；服务器直接插入的同类设备也需拔出。然后执行：

```sh
/usr/local/bin/uta-server tune-ch341 enable
/usr/local/bin/uta-server tune-ch341 status
```

看到 `state: enabled`、`bulk_out_bytes: 4096` 后重新接入 USB。端口号可能变化，使用 App 当前显示的节点。点击服务器的检查图标，可刷新界面上的加速状态。

外部烧录工具继续使用原协议；为获得本次验证的速度，使用 921600 和 4 KiB 写入。强制逐页写入、低波特率、网络延迟、Flash 擦写速度都会限制吞吐。不同芯片不保证相同结果。

## 恢复系统驱动

先断开全部 CH340/CH341，再在管理员 root shell 执行：

```sh
/usr/local/bin/uta-server tune-ch341 disable
```

系统原有 `ch341.ko` 不被覆盖。加载失败时尝试恢复系统驱动；失败信息会分别报告加载和恢复结果。没有配置开机加载或 DKMS，**服务器重启后调优失效**；同内核可以重新 `enable`，内核升级需重新检查支持范围并构建。

`external_driver` 表示已有其他版本的 `ch341_uta`，工具不会自动卸载它。管理员确认设备全部断开后，可手动 `rmmod ch341_uta`、`modprobe ch341`，再使用本版本命令。

本预览模块未签名。Secure Boot / 内核 lockdown 环境可能拒绝加载；工具不会关闭安全设置，应继续使用系统驱动。不支持的内核保持原 USB 透传行为。自编译模块会留下 Linux 的 out-of-tree/unsigned taint 标记，这是内核模块加载状态，不会因卸载而立即清除。

## 本次实测边界

4,614,072 字节镜像：最终续写 626 个扇区（2,564,096 字节），501 个已有正确扇区跳过；整体 145.252 秒。写入、必要擦除及逐扇区 CRC 合计 85.442 秒，平均 30,009.74 B/s。完整补齐后的镜像 CRC 校验通过，保留分区一致，Flash 保护恢复。4.6 MB 从头烧录预计约 3–4 分钟，此数字为估计，并非重新全量烧录计时。用户固件功能不在此次发布验收范围内。

## English quick start

Optional, session-only CH340/CH341 USB/IP transmit-buffer tuning. Preview support: Linux 5.15; physically tested on Ubuntu 5.15.0-173-generic. Update the server agent separately from the Mac app. Run `uta-server tune-ch341 status`, then `build` in an administrator root shell with matching kernel headers and build tools installed. If entering via `sudo -i`, run `unset SUDO_UID` inside that authorized root shell first. Delegated agent-only sudo is intentionally insufficient for kernel module loading.

Disconnect **all** CH340/CH341 devices, run `enable`, verify `state: enabled`, and reconnect. `disable` restores the system driver after disconnecting all matching devices. Reboot loses the tuning; no DKMS, startup installation, or original module replacement is performed. Secure Boot/lockdown and other kernel versions may not support this unsigned preview module. The original flashing application still supplies the device protocol and should use its large-block/high-baud mode. The measured ~30 KB/s is one hardware/network result, not a universal guarantee.
