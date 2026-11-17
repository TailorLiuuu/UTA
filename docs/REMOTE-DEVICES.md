# 单设备授权、远程烧录与日志

普通场景直接选择服务器和设备；只有需要表达多台机器的复杂关系时才使用链路工作台。Mac 编译，Linux 运行原烧录工具，Windows/Mac 提供 USB。USB 不再从 Linux 转发一遍到开发 Mac；Mac 通过 SSH 接收端口状态、工具输出和串口日志。

## 更新范围

这项功能需要新版 Mac/Windows 客户端和 Linux `uta-server`。云端 `doctor` 返回 `device_operations: 1`；仅有 `transfer_protocol: 1` 的上一版还没有设备授权和日志接口。保持原有 USB 数据通道及烧录加速设置，无需为此更换内核或加载新驱动。

在没有传输、日志和烧录任务时协调更新客户端和云端代理。不要仅因开发 Mac 显示没有本地设备，就替换同事正在使用的服务。源码构建命令：

```sh
python3 scripts/build-server.py
python3 scripts/build-windows.py
python3 scripts/build-transfer-kit.py
# Mac App 沿用 scripts/build.sh，需按原项目流程提供签名身份。
```

## 桌面操作：不必画链路

1. 提供 USB 的电脑把所选设备接入目标 Linux 服务器。直接使用这台电脑开发时，它本来就拥有这个设备，不需要额外授权。
2. 另一台开发 Mac 在相同服务器的「远程设备与日志」点击「复制接收端标识」。这是该配置对应的公共客户端 ID，不是密码。
3. 提供端授权**这一台设备**：Mac 在同一面板选择自己的设备，粘贴接收端标识，点击「授权 24 小时」；Windows 在设备行选择后点击 `Authorize client`。
4. 开发 Mac 点击「刷新远程设备」，选择已获授权的设备。选择会保存在本机，不会猜测列表中的第一个设备，也不会自动开始读写。
5. 「查看串口日志」按选定波特率和时间开始读取；停止或离开页面会结束本次读取。「复制烧录命令」提供 CLI 模板，填入项目原工具即可。

提供端可查看和撤销授权。默认 24 小时，CLI 可指定 1–720 小时；到期或撤权会在活动检查中结束该授权的操作。撤权可能中止烧录，请先协调。授权不赋予再次转授权、查看其他设备或断开 USB 的权限。不同 SSH 系统账号之间不共享此授权；共用 root 仍不是防恶意用户的安全边界。

当前授权绑定 UTA 连接/重连路由，而不是设备名字或 `/dev/ttyUSB0`。同一重连路由重新枚举时按新节点解析；手动断开后重新接入、换服务器或服务器重启，可能生成新 ID，需重新选择并授权。这是避免同型号设备被误接管的限制。Windows 预览仍不保证下载模式切换后自动接回；提供端需要时手动接入，再重新授权。普通状态查询不会打开串口，也不会续租他人的 USB 提供端连接。

## CLI：接收端身份与设备选择

以下 `SERVER` 是当前客户端保存的 UTA 服务器 ID 或唯一名称，不是任意主机地址。Mac 可使用 `/Applications/UTA.app/Contents/Resources/uta`；Windows 使用安装目录的 `uta.cmd`。

```sh
# 在开发 Mac 获取本配置的公共 ID，交给设备提供者。
uta port identity --to SERVER

# 在提供端操作；DEVICE_ID 来自该提供端的 port list。
uta port list --to SERVER
uta port share --to SERVER --id DEVICE_ID --recipient MAC_CLIENT_ID --hours 24
uta port grants --to SERVER --id DEVICE_ID
uta port revoke --to SERVER --id DEVICE_ID --recipient MAC_CLIENT_ID

# 开发 Mac 只能看到自己的设备和授权给自己的设备。
uta port list --to SERVER
uta port status --to SERVER --id DEVICE_ID
```

`list/status` 返回 `id`、`session`、`nodes`、`state`、`busy` 和 `access`。`busy` 是 UTA 管理的活动操作，不能证明没有外部软件占用；打开串口前还会尽力检查其他进程，但不构成对任意 root 程序的强制锁。

## 上传固件并执行原工具

```sh
# 按实际项目替换路径和参数。UTA 不定义芯片烧录协议。
uta port run --to SERVER --id DEVICE_ID \
  --upload ./firmware.bin --workspace my-project --timeout 900 -- \
  /absolute/path/original-flasher --port '{port}' '{upload}'
```

- `{port}` 自动解析本次设备的唯一串口；有多个串口时必须添加 `--node /dev/明确节点`，且这个节点必须仍属于所选设备。
- `{usb}` 解析当前 USB 设备节点；其他已枚举节点使用 `--node` 搭配 `{device}`。至少使用一个设备占位符，防止模板遗留固定串口路径。
- `{upload}` 是校验成功的远端固件文件或目录；`{dir}` 是上传包根目录。`--upload` 可省略，此时使用已经在服务器上的文件，不能再使用文件占位符。
- `--cwd /absolute/project/path` 指定已有工作目录。工具以 SSH 账号身份运行，即使 UTA 通过 sudo 读取设备状态，也不会让普通用户的工具自动变成 root。工具环境有基本 PATH/HOME；SDK 初始化请使用项目原有包装脚本。
- 按参数数组执行，不自动展开 shell 命令。烧录工具的 stdout/stderr 实时返回 Mac 的 stderr（JSON 事件），最终结果为 stdout JSON；非零退出码、超时、中止或输出上限均让 CLI 返回非零。
- 必须以前台方式执行工具。任意工具仍有 SSH 账号本来的权限，设备占位符和 UTA 锁不是进程沙箱；不要运行自行扫描所有端口的烧录工具。
- UTA 操作锁会让同一设备的日志和烧录互斥；先停止日志再烧录，不会擅自杀死另一位用户的任务。

固件上传仍使用原加密校验/续传流程。烧录操作不自动重试；所有协议交互仍在 Linux 烧录工具与 Windows/Mac 提供的 USB 之间，Mac 不参与每个烧录包的应答。

## 按需读取日志

```sh
# 机器可读 JSON 事件，时长默认 60 秒；波特率必须明确指定。
uta port logs --to SERVER --id DEVICE_ID --baud 115200 --seconds 60

# 将串口原始字节写到文件；诊断和结果 JSON 去 stderr，不混入原始字节。
uta port logs --to SERVER --id DEVICE_ID --baud 115200 --seconds 60 --raw > serial.log
```

当前内置日志读取为 8N1、无流控，不发送数据、复位或清缓冲命令。打开串口本身仍可能触发板卡的 DTR/RTS 行为，开始前应确认。协议帧通过 base64 保留任意字节；GUI 作为文本显示，保留最近 64 KiB，可丢弃旧显示内容但不会偷偷声称它是完整日志。需要保留完整采样请用 `--raw` 保存。

日志按需启动、用同一条 SSH 流返回；连续小包最多约每 20 ms 合并一次，不等待填满大块。没有日志订阅时不打开串口。每个活动任务约每秒检查一次连接/授权（输出阻塞时最多等待 15 秒后终止），空闲页面只在手动刷新时查询。SSH 结束、取消、USB 断开、授权到期或撤回都会关闭本次串口/结束前台工具；不会自动重新烧录。

## 查询、中止与资源限制

开始时会返回 `run_id`。连接中断后先查询，不能直接再运行一遍烧录：

```sh
uta port run-status --to SERVER --id DEVICE_ID --run-id RUN_ID
uta port stop --to SERVER --id DEVICE_ID --run-id RUN_ID
# 明确确认不再需要该操作记录后，才清理；清理后该 ID 不再提供防重复执行保证。
uta port forget --to SERVER --id DEVICE_ID --run-id RUN_ID
```

操作回执保存在 Linux 私有 `/var/lib/uta-operations`，可跨重启查询。相同 run ID 返回原回执，不重新执行；参数不同则拒绝。停止只限启动任务的客户端，设备提供者可通过撤权终止授权任务。异常结束且无法确认运行状态会报告 `unknown`，须人工核查工具和设备，不能推断成功。

每个 SSH 账号最多 4 个活动操作、128 条保留回执；整个服务器最多 512 条回执。回执满后拒绝新任务，需用 `forget` 明确清理已结束的记录，不自动删除幂等依据。单任务时长 1–3600 秒、输出最多 16 MiB；达到上限会终止并明确报告，不能用于无限期后台采集。回执只保存最多 4096 字符的工具输出尾部，不保存串口正文；GUI 无自动落盘。授权记录、回执与现有 USB 记录均不含接收客户端的秘密凭据。

## 验收边界

模拟 PTY 可验证字节回传、取消、占用锁和资源限制；Linux 隔离测试可验证提权代理启动的工具实际降为 SSH 用户。这些不能替代具体板卡、USB 驱动、烧录器的实机速度与稳定性验证。上线后先做短时日志采样，再在用户指定设备和固件上执行一次完整烧录校验。
