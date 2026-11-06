# CH340 / CH341 USB/IP 串口加速

Preview 13 / Build 25 提供可选的 Linux 5.4 / 5.15 CH340/CH341 传输优化。
UTA 仍只负责 USB 接入和传输，烧录协议、波特率、复位、固件校验由原来的工具负责。

## 优化内容

- bulk IN 接收缓冲保持 4096 字节，减少跨网络接收突发响应时的请求间隙。
- bulk OUT 发送缓冲设为 8192 字节，端口发布前将发送 FIFO 扩至至少 8192 字节。
- 打开串口时使用 `TTY_NO_WRITE_SPLIT`，避免 TTY 层把约 4 KiB 的应用写入拆成 2 KiB 小块。
- 当前版本标识 `uta-ch341-tx2`。不承诺任意长度写入都对应单个 USB 请求。

源码来自固定哈希的 Linux 5.15 CH341 驱动。Linux 5.4 构建额外适配 `port_remove` 返回类型。
构建使用服务器当前内核头文件，不升级内核、不覆盖原系统驱动、不配置开机加载。

## 在 App 中启用或升级

1. 更新 UTA，选择服务器，查看「服务器 USB 优化」。刷新图标只检测状态。
2. 停止烧录、串口日志工具，断开该服务器的 UTA USB 并等待清理完成。
   其他客户端或服务器本地连接的 CH340/CH341 也必须断开。
3. 点击「一键启用」并确认。UTA 上传并核验随 App 打包的代理，准备缺失构建依赖，
   编译、启用并读回实际状态。只有当前版本、IN 4096、OUT 8192 均匹配才显示已启用。
4. 需要密码时点击「授权并启用」，在自动打开的系统终端完成 SSH / sudo 授权。
   UTA 不收集或保存密码，账号需具备完整管理员权限。
5. 重新接入设备，使用 App 显示的最新串口路径。查看问题可打开「配置日志」。

已知 Preview 7 和 `uta-ch341-rx1` 旧模块可以升级：先构建成功，再确认全部设备空闲后切换。
未知模块（包括临时测试版本）不会自动卸载。构建失败保留旧模块，加载失败尝试恢复系统驱动。

缺失依赖仅在 apt-get 系统上自动安装。不支持的内核、未知模块、Secure Boot/lockdown 拒绝均会报错；
不修改安全策略或 sudoers。仅升级 Mac App 不会自动更新云端代理或启用驱动。
此模块仅当前启动有效，服务器重启后再次点击启用即可。

## CLI

Mac 上异步启动，使用 `status` 查看进度：

```sh
uta server optimize SERVER_ID
# 状态为 authorization_required 时打开授权终端：
uta server optimize SERVER_ID --interactive
uta status
```

服务器上先更新 Release 的 `uta-server`。在完整管理员 root shell 中运行：

```sh
uta-server tune-ch341 status
uta-server tune-ch341 setup --install-dependencies
```

若通过 `sudo -i` 进入完整 root shell，在该 shell 中执行 `unset SUDO_UID` 后运行。
仅委派 `uta-server` 的 sudo 权限不允许加载内核模块。`--install-dependencies` 显式允许安装缺失
构建包，不带此选项时需自行准备 `build-essential` 和当前内核的 `linux-headers`。

分步流程仍可用：`tune-ch341 build`、`enable`、`status`、`disable`。
`setup` 支持已知旧模块升级；`enable` 不会直接替换已加载的旧版本。
正确状态应包含：

```json
{"state":"enabled","revision":"uta-ch341-tx2","bulk_in_bytes":4096,"bulk_out_bytes":8192,"persistent":false}
```

内核模块影响该服务器所有 CH340/CH341。不要在烧录或日志采集中切换。
恢复系统驱动：断开全部同类设备，在完整管理员 shell 执行 `uta-server tune-ch341 disable`。
系统原驱动文件保留。内核升级后必须重新检查支持范围并构建。

## 烧录速度和验证边界

在 Linux 5.4.0-162-generic 上，一轮 BK7259 官方 CLI 以 921600 烧录 4,723,744 字节镜像：
全程 106.983 秒，写入阶段 103 秒；工具报告 `Download complete, all pass.`，Flash 保护恢复，
额外读回末尾 4096 字节，其中镜像剩余 1056 字节与源文件一致。
此前同板 115200 写入阶段为 433.586 秒，本轮约快 4.21 倍。

这是单次硬件、已有 Flash 内容、工具配置及网络条件下的结果，原工具可能跳过已有正确内容。
不代表空白芯片全量擦写计时、长期稳定性、日志零丢失或固件功能验收。
使用高波特率、大块写入及覆盖网络延迟的工具超时，同时保留地址、响应及 CRC 校验。
UTA 不自动修改这些参数，也不保证每种芯片均达到相同速度。

详见 [高速验证记录](CH341-HIGHSPEED-VALIDATION.md)。早期版本记录见
[Linux 5.4 初始接入验证](CH341-KERNEL54-VALIDATION.md) 和
[BK7259 外部工具等待参数](BK7259-UTA-2026-11-02.md)。

## English quick start

Preview 13 / Build 25 supports optional, session-only CH340/CH341 tuning on Linux 5.4 and 5.15.
IN remains 4096 bytes; OUT and the minimum write FIFO are 8192 bytes. TTY write splitting is disabled
for this driver. The tuning remains protocol-neutral and does not change your flashing application.

Select a server, disconnect its USB sessions and all CH340/CH341 devices, then choose **Server USB
optimization → Enable optimization**. The app builds before switching drivers and verifies the result.
**Authorize and enable** opens Terminal when administrator credentials are required. Known Preview 7
and rx1 drivers can be upgraded; unknown modules and kernel security restrictions are not overridden.
The Mac CLI provides `uta server optimize SERVER_ID [--interactive]`. On the server, a full administrator
shell can run `uta-server tune-ch341 setup --install-dependencies`. No password storage, system kernel
upgrade, system driver overwrite or boot persistence is performed. Re-enable after reboot.

One Linux 5.4 high-speed flash completed in 107 seconds for a 4.72 MB image, with official tool success
and matching extra tail readback. This is not a universal performance or long-term stability guarantee.
