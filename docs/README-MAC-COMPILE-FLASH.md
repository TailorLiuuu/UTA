# Mac 编译 → Linux 烧录 → Windows USB：项目接入 README

把本文交给负责固件的 Mac 项目会话。UTA 负责加密传文件和通用 USB 透传；编译、烧录、复位和校验仍使用项目原来的工具。本文不预设芯片型号、烧录地址、波特率或串口编号。

## 1. 这条链路在哪里运行

```text
Mac 项目 / 编译工具
    │ uta send / uta run：SSH 加密传输 + SHA-256 校验
    ▼
Linux 烧录服务器
    ├── 当前 Mac 客户端 / 项目专属的固件目录
    ├── 原有 Linux 烧录工具 → 明确指定的 USB / 串口节点
    ▲
    │ UTA USB 透传（Windows 主动接入这个服务器）
Windows + 实物开发板
```

固件从 Mac 发到 Linux；USB 从 Windows 接入 Linux。固件不需要下载回 Windows。链路工作台可以画出和保存关系，当前只有本机到目标的文件发送与传输能力检查接入了执行；连线本身不会启动编译、烧录或多跳调度。

## 2. 哪些 CLI 要更新

| 位置 | 要求 | 怎么确认 |
| --- | --- | --- |
| 实际编译的 Mac | 新版 UTA 客户端，支持 `send`、`transfers`、`run` | `uta send --help` |
| Linux 烧录服务器 | 新版 `uta-server`，支持传输协议 1 | `uta transfers check` 返回 `ok: true`、`result.protocol: 1` |
| Windows USB 提供端 | 已能稳定将目标 USB 接到服务器即可 | Windows UTA 中确认指定设备已接入；不需要为文件传输单独升级 Windows |

本机 Build 29 包含传输客户端；设备授权和按需日志需要更新到包含 `port` 命令的新版本，云端 `doctor` 还需返回 `device_operations: 1`。云端仍可能显示 `0.1.0-preview.15`：本次能力不能仅按这个版本字符串判断，需检查协议和文件 SHA-256。不要因为版本文字没变就反复安装。

**编译会话若在另一台 Mac 上，当前这台 Mac 的 App、SSH 配置和客户端身份不会自动出现在那里。** 先在真正执行编译命令的机器确认新版 CLI 和目标配置；没有就先安装新版 UTA，或在同版本源码目录使用 `scripts/uta`。不要把命令提交给 Linux 会话后误认为它在 Mac 编译。

### 已有 App 的更新入口

在 UTA 中选择目标服务器，检查状态；如果提示云端代理不同或需要更新，点击「更新云端代理」。权限不足时使用「手动安装」生成的步骤。替换系统 CLI 前先与使用这台服务器的 Windows / 同事协调，等待相关 UTA 操作结束；本机没有连接不代表别人的连接已经结束。文件传输不需要重装 USB 驱动、改内核或加载加速模块。

### 手动包与不影响现有 USB 的试用方式

开发目录运行 `python3 scripts/build-transfer-kit.py` 会生成 `dist/UTA-CLI-transfer-preview.zip`，含独立接收端 `uta-server`、本文及校验清单。**它是接收端与文档包，不是 Mac App 安装包，也不包含 Mac 发送端或任何本机配置。**

将包通过已有 SSH 发送到服务器自己的新目录，解压后执行：

```sh
# 在服务器、解压目录中执行。Linux 使用 sha256sum，macOS 使用 shasum -a 256。
sha256sum -c SHA256SUMS
python3 --version  # 需要 Python 3.9+
# 独立文件名用于试用，不覆盖运行中的 /usr/local/bin/uta-server。
mkdir -p "$HOME/.local/bin"
install -m 755 uta-server "$HOME/.local/bin/uta-server-transfer"
```

在 Mac 的每个传输命令中添加 `--remote-cli /该账号绝对路径/.local/bin/uta-server-transfer`。不要写远端 `~`、`$HOME` 或依赖远端 shell 展开。此方式只运行按需启动的 SSH 子进程，不启动新监听端口，不接管 USB。系统路径的正式升级可稍后通过 App 完成。

## 3. Mac 项目的一次性准备

在**实际编译所在的 Mac** 执行；以下代码块可在同一终端继续使用变量：

```sh
UTA='/Applications/UTA.app/Contents/Resources/uta'
UTA_TARGET='build-server'  # 改为这台 Mac 的 UTA 中保存的唯一名称或服务器 ID
UTA_WORKSPACE='my-firmware-project'  # 每个项目固定一个名字，使用英文、数字和连字符
"$UTA" send --help
"$UTA" transfers check --to "$UTA_TARGET" --workspace "$UTA_WORKSPACE"
```

`--to` 是 **UTA 保存的服务器名称 / ID**，不是任意 SSH 地址。通过 App 导入 SSH 配置并命名即可；也可使用 `uta server add --name NAME --target SSH_ALIAS --use-ssh-config`。先用普通 SSH 确认主机指纹及密钥，不要关闭主机密钥校验来绕过错误。

能力检查通过后，先做下面的无硬件测试。它只写一份测试文件、在远端读文件并计算校验值，不打开串口，不复位或烧录。

## 4. 首次传输测试（不接触开发板）

```sh
UTA_CHECK_DIR=$(mktemp -d)
printf 'UTA file transport check\n' > "$UTA_CHECK_DIR/check.txt"
"$UTA" run --to "$UTA_TARGET" --workspace "$UTA_WORKSPACE" \
  --upload "$UTA_CHECK_DIR/check.txt" --timeout 30 -- \
  python3 -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); b=p.read_bytes(); print(hashlib.sha256(b).hexdigest()); assert b == b"UTA file transport check\n"' \
  '{upload}' > "$UTA_CHECK_DIR/result.json" 2> "$UTA_CHECK_DIR/events.jsonl"
cat "$UTA_CHECK_DIR/result.json"
```

要求 `ok: true`、`result.transfer.state: complete`、`result.execution.state: completed`、`result.execution.exit_code: 0`。工具输出和进度在 `events.jsonl`，最终 JSON 在 `result.json`。不要只检查是否出现了文件路径。

清理自己这一份测试任务（不会清理其他任务或 USB）：

```sh
UTA_TEST_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["result"]["transfer"]["id"])' "$UTA_CHECK_DIR/result.json")
"$UTA" transfers remove --to "$UTA_TARGET" --workspace "$UTA_WORKSPACE" --id "$UTA_TEST_ID"
```

## 5. 编译与固件准备

项目会话先读取本项目 README / SDK / 已验证的烧录脚本，确定准确的板型、编译命令、产物及烧录参数，再执行编译。记录提交号、构建配置、产物大小和 SHA-256。

- 单固件可直接发送 `.bin` / 其他原工具支持的文件。
- 需要 bootloader、分区表及多个镜像时，准备只包含本次产物与清单的目录，一起发送；不能只发主固件再使用服务器残留的旧文件。
- 不要上传整个工程、SSH 配置、`.env`、密钥或其他客户的固件。软链接、硬链接不受支持；将需要的产物复制成普通文件后发送。
- 服务端使用已经安装并验证的原烧录工具。Mac 可执行程序不能直接拿到 Linux 运行；脚本依赖、SDK 路径与烧录工具版本需在服务器单独满足。

先单独发送，确认远端产物与本次编译对应：

```sh
UTA_ARTIFACT='/绝对路径/本次固件.bin'  # 必须替换成真实产物，也可以是产物目录
"$UTA" send "$UTA_ARTIFACT" --to "$UTA_TARGET" --workspace "$UTA_WORKSPACE"
```

返回 `result.id`、`result.entry`、`result.directory`。远端目录由 UTA 按账号、客户端、项目、内容分开管理；不要猜固定 `/tmp/firmware.bin`。相同内容再次发送会校验并复用；不同内容成为另一个任务。

## 6. 确认 Windows 设备归属，然后烧录

在 Windows UTA 确认**所选物理设备**接到了正确服务器；Windows `uta status` 可查看该连接的 `server_id`、`busid`、`session` 和 `nodes`。记录本次连接的 session 和当前 Linux 节点，不能沿用历史 `/dev/ttyUSB0` 或按“第一个串口”猜测。

**Mac 的文件传输身份和 Windows 的 USB 身份是两份独立凭据。不要复制 Windows 的整份身份文件。** 在 Mac 运行 `uta port identity --to SERVER`，把返回的公共客户端 ID 交给 Windows 提供者；Windows 在所选设备上点击 `Authorize client`，仅授权这一台设备。Mac 再运行 `uta port list --to SERVER` 获取自己的设备 ID 和当前节点。默认授权 24 小时，可撤销；手动重新接入后 ID 可能变化，需要重新选择授权。详细步骤见 [REMOTE-DEVICES.md](REMOTE-DEVICES.md)。

烧录前再次确认 `state` 已就绪、节点属于目标连接、没有日志窗口或另一烧录工具占用；向用户说明目标、动作、预计耗时与可能复位，取得本轮实机授权后执行。文件传输身份隔离不会替外部烧录工具锁住串口。

下面仅是**参数结构**，不可原样运行；项目会话必须用原工具的真实路径和真实参数替换：

```sh
"$UTA" port run --to "$UTA_TARGET" --id DEVICE_ID --workspace "$UTA_WORKSPACE" \
  --upload "$UTA_ARTIFACT" --timeout 900 -- \
  /绝对路径/原来的烧录工具 --port '{port}' '{upload}'
```

`{upload}` 被替换为这次上传的远端文件 / 目录绝对路径。目录包内的镜像可用 `'{upload}/firmware.bin'`。`{dir}` 是任务包根目录。命令按参数数组执行，不会自动解释 `&&`、管道或 shell 变量，也不会自动进入 SDK 工程目录；需要环境初始化或复杂流程时，显式调用项目已有的包装脚本。工具须以前台模式运行，不要在后台脱离 UTA 启动烧录。

`uta port run` 会先上传校验、重新解析设备节点并取得该设备的操作锁，再执行一次。`{port}` 只解析该设备的唯一串口；多串口必须用 `--node` 指定。前面的 `send` 不是必需步骤，但首测有助于单独排查；同内容的第二次上传可以复用。timeout 应覆盖本次烧录和原工具校验所需时间（最大 3600 秒），超时会尝试终止工具，烧录中止可能需要恢复下载模式。

## 7. 中断、重连和验收

- 保存设备 `DEVICE_ID` 和 stderr 中 `event: execution` 的 `run_id`。若 SSH 中断，先查回执，**不要重新运行烧录命令**：

  ```sh
  "$UTA" port run-status --to "$UTA_TARGET" --id DEVICE_ID --run-id RUN_ID
  ```

  `starting` / `running` / `unknown` 均不能当作已成功或安全重试。先核实原工具与设备状态，再决定是否开启新的烧录。
- 下载模式切换会让原串口消失；等待 USB 重新接入并查询当前节点，按原工具的重连流程继续。Windows 预览不能假定拥有与 Mac 相同的自动模式重连能力；若没有自动接回，由 Windows 操作者在 UTA 中重新接入，再取得新 session / nodes。不要反复抢占设备或重试擦写。
- 成功分层记录：编译成功；文件 SHA-256 校验成功；原工具烧录退出码；原工具报告的烧录 / 回读校验。**传输成功不等于烧录成功，退出码 0 也不能替代项目要求的校验。** 功能验收另行约定。
- 只有需要且获得授权时才读日志：使用项目正确的串口参数，限定采样时间，烧录和日志不能同时占用同一端口。
- SSH 密钥不同但共用 root，不构成对恶意用户的隔离；UTA 的客户端身份用于避免正常操作混用。需要真正权限隔离请用独立系统账号或容器。

## 8. 可直接交给 Mac 项目会话的任务说明

> 请按 UTA 的 `README-MAC-COMPILE-FLASH.md` 接入。先确认当前执行环境确实是编译项目所在的 Mac，CLI 支持 `send/transfers/run` 和 `port`，配置目标为我的烧录服务器。先做协议检查和无硬件传输测试，然后读取当前项目原有构建与烧录说明，编译正确板型并记录固件哈希。固件发到目标服务器，由该服务器的原烧录工具烧录 Windows 通过 UTA 提供的 USB。不要修改 UTA 来适配芯片协议，不要自动挑第一个串口，不要替换同事凭据，不要默认使用管理员模式。通过公共客户端标识获得单设备授权，再取得当前设备 ID 和明确节点；在实机操作前报告目标、烧录命令和耗时，由我确认。使用 `uta port run` 执行原工具，按需使用 `uta port logs` 查看日志；烧录中断先查 `uta port run-status`，不自动重新擦写。最终分别报告编译、传输、烧录和校验结果。

进一步说明见 [TRANSFERS.md](TRANSFERS.md)、[CLIENT-IDENTITY.md](CLIENT-IDENTITY.md) 和 [LINK-WORKSPACE.md](LINK-WORKSPACE.md)。
