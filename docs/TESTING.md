# UTA 0.1：Mac → Linux USB 透传测试

这是可运行的原型，不是所有设备兼容性已经验证的产品。
它导出真实 USB 请求，不是串口转 TCP，也不会下载固件到 Mac。

## 交付内容与运行要求

- `UTA.app`：Mac 界面，管理服务器和设备路由。
- `UTA.app/Contents/Resources/uta`：本地 CLI。
- `uta-server`：独立 Linux CLI，无第三方 Python 包。
- `uta-usb-io` / `uta-usb-transport.mjs`：可选的 Linux 异步 USB 传输工具与 Node 传输接口，供外部应用使用。
- 当前预编译 App：Apple Silicon / macOS 26，需 Python 3.9+。
  Mac 上需先安装 Python 3。
  libusb 和 USB/IP 动态库已包含在 App 中，不必另外装 VirtualHere。
- 发布预览包使用 Developer ID 签名与可信时间戳，尚未公证；本地构建默认不加时间戳。
  拷到其他电脑可能需要在“隐私与安全性”中允许打开。
- Linux：Python 3.9+、OpenSSH、支持 `CONFIG_USBIP_VHCI_HCD` 的内核；
  需要 root 或经管理员配置的 sudo 权限。普通受限容器通常不满足。
- SSH 服务端需要允许 remote TCP forwarding；
  `GatewayPorts` 使用 `no`（默认）或 `clientspecified`，不要使用强制公网监听的 `yes`。

## 第一步：准备 SSH

先在终端登录一次，核对服务器指纹，确保密钥或 SSH Agent 登录可用：

```bash
ssh developer@YOUR_SERVER
```

软件使用系统 SSH 配置，支持别名和 ProxyJump；不保存密码，不自动接受陌生主机密钥。
点击侧栏“从 SSH 配置导入”，勾选需要使用的 Host 别名。
会读取 `~/.ssh/config` 及其 Include 文件，跳过通配 Host 和已添加的地址。
导入不连接服务器、不读取或复制私钥内容。导入项保留别名，实际连接由系统 SSH
解释配置，因此后续更改 HostName、User、Port、IdentityFile、ProxyJump 会继续生效。
手动添加时也可勾选“沿用本机 SSH 配置的端口和密钥”；未勾选时使用界面填写的端口。

选中服务器后点击“编辑”，可修改“备注名称”。备注用于侧栏和设备目标选择，
不会改写 SSH 配置。仅修改备注时，即使已有设备连接，也无需断开；
修改 SSH 地址、端口或密钥等连接参数仍需先释放设备。

扫描只发现候选别名，不计算条件 Match 的有效配置，也不执行 Match exec。
条件 Include 中的别名也会列出，实际可用性以“检查连接”为准。
扫描限 16 层 Include、256 个文件；出现读文件或语法错误会显示提示。
连接操作由系统 SSH 正常解释你自己的配置，包括已有 ProxyCommand / Match exec。

CLI 等价操作：

```bash
./uta server discover
./uta server import dev-cloud lab-server
# 或手动保存一个继承 SSH 配置的地址
./uta server add --name 开发机 --target dev-cloud --use-ssh-config
```

## 第二步：安装云端 CLI

方式 A：在 App 中添加服务器，然后点“安装云端代理”。这要求现有 SSH
账号是 root 或已具备非交互 sudo 权限。软件不会自动修改 sudoers。

首次打开服务器页面会自动检查。确认未安装时显示安装按钮；已安装时显示
“云端代理已安装”和查询到的版本号，可在旁边的更多菜单重新安装。
SSH 不可达时显示“安装状态待确认”，不会按未安装处理。
已安装与内核就绪是两个状态：代理存在但缺少 `vhci_hcd` 或权限时，仍显示具体检查错误。

App 的服务器页面也始终提供“手动安装”入口。窗口按该服务器的 SSH 别名、
端口和密钥生成两条命令：先上传脚本，再以交互式 SSH 校验 SHA-256、安装并检查。
两步都在 **Mac 终端** 执行，允许输入 SSH / sudo 密码；复制命令不会执行安装。
每次打开窗口会生成不同的临时文件名，请复制同一窗口中的两条命令并按顺序运行。
也可以“导出服务端脚本”后自行传给服务器管理员，按下面的方式 B 安装。
安装后点击“安装完成，重新检查”，App 会重新识别安装状态。
CLI 可用 `uta server manual SERVER_ID` 返回同一套命令（不执行）。

方式 B：手动上传交付包中的 `uta-server`：

```bash
scp uta-server developer@YOUR_SERVER:~/uta-server.py
ssh -t developer@YOUR_SERVER
sudo install -o root -g root -m 755 ~/uta-server.py /usr/local/bin/uta-server
sudo modprobe vhci_hcd
sudo /usr/local/bin/uta-server doctor
```

`doctor` 应返回 `ok: true`、`vhci_available: true` 和可用端口。
找不到模块时，安装当前发行版对应内核模块包；不要仅凭网络连通判定可使用 USB。

本地软件需要通过非交互 SSH 执行挂载命令。若使用非 root 账号，由管理员
使用 `sudo visudo -f /etc/sudoers.d/uta` 配置最小范围，例如：

```sudoers
developer ALL=(root) NOPASSWD: /usr/local/bin/uta-server
```

把 `developer` 换成实际用户名。只对 **root 所有、普通用户不可写** 的已审查脚本授权。
这是你主动进行的系统配置；App 不会代替你修改。
若不希望配置 sudoers，可使用已有的适当 root 测试账号。

## 第三步：接入设备

本地 USB 掉线、导出器退出或 SSH 隧道结束时，UTA 自动释放该会话的云端设备。
若服务器暂不可达，界面显示等待自动清理，后台每 15 秒重试，重启 App 后继续；
无需用户手动删除残留记录。

当前版本接入时默认启用「允许设备切换模式后重新接入」，设备卡片可以关闭。
连接掉线后等待最多 120 秒；先确认旧云端会话清理，再将原插口的设备接回原服务器。
VID:PID 变化时，卡片会询问「是否仍是刚才的设备？」；确认后才记住并接回，取消则留在本机。
确认仅接受同一插口、没有明确序列号冲突的候选；设备再次变化、拔出或等待超时会使旧确认失效。
记忆按原服务器、插口和原设备身份保存到 reconnect-rules.json，重新启动后手动接入原设备可复用。
高级设置可查看、编辑和清除记忆；也可手填对应 VID:PID，
或首次连接时使用 `--reconnect-id VID:PID`（可重复）。UTA 没有内置芯片列表或协议。
有序列号时还会校验序列号；用户明确允许的跨 PID 重枚举可以不提供原序列号。
没有序列号的设备只能依据插口与 VID:PID 匹配，无法区分换到同一口的另一块同型号板。
等待期间不要换板；手动「断开」或关闭自动重连立即取消等待。
重连最多尝试 3 次，超时后需手动接入；控制器重启仅恢复清理，不恢复接管意图。
未确认的 VID:PID 变化不会自动接入；移动到其他 USB 插口也不自动接入。

重连会生成新的云端节点。烧录工具应关闭失效句柄、查询新节点后进入下一协议阶段；
UTA **不会重放未确认的烧录命令，也不会从中断位置自动续烧**。

1. 双击 UTA.app。
2. 从 SSH 配置导入服务器，或手动添加。
3. 点击“检查连接”，确认 SSH、管理员权限和云端虚拟 USB 已就绪。
4. 点击侧栏服务器，勾选要交给它的 USB，点击“接入 服务器名”。
   可以选多个设备，分别显示连接进度；“释放所选”仅释放当前服务器上的所选设备。
   设备总览仍可逐个选择目标服务器并接入。
5. 挂载过程可能短暂让该设备从 Mac 本地应用中消失，这是独占转发。
6. 只有状态变为 **云端已挂载**，才表示 Linux 已枚举设备。
   在设备卡片的“云端设备端口 · 服务器名”查看或复制 `/dev/tty*`、
   `/dev/bus/usb/*` 路径。这些是云端路径，不是 Mac 本地路径。
   Mac 普通扫描暂时看不到已接管设备时，UTA 仍保留会话卡片和端口。
7. 选择另一台服务器后点击“切换接入”：先确认旧服务器释放，再连接新服务器。
   旧服务器失联导致清理无法确认时，不自动切换，先恢复旧端连接并断开。
   在服务器页面勾选已分配给其他服务器的设备并点击接入，也执行相同的释放检查。
   若设备已从本地扫描中消失，先点击会话卡片“断开”，等待它回到 Mac，
   再选择其他服务器接入。

状态含义：

| 状态 | 已证实的事情 |
|---|---|
| 在本地 | Mac 发现物理设备，尚未交给服务器 |
| 正在连接 | 正在检查云端、准备导出器或建立 SSH |
| 等待自动重连 | 旧连接已断开，正在有限时间内等待原插口的匹配设备 |
| 等待枚举 | 隧道或导入已开始，尚未证实 Linux 完成枚举 |
| 云端已挂载 | Linux vhci_hcd 和 USB sysfs 中存在该设备 |
| 连接异常 | 有明确错误；展开日志查看 USB 端或 SSH 端原因 |

“已挂载”不代表厂家驱动已安装，不代表烧录成功，更不代表功能测试通过。

## 第四步：服务器上的远程开发会话

```bash
sudo -n /usr/local/bin/uta-server list
```

JSON 中按 `session`、来源 `busid` 和 `vidpid` 找设备，`nodes` 给出实际路径。
若节点为空，可能是厂家驱动缺失，或者设备本来就不提供串口。

接下来在服务器运行原来的烧录工具，使用服务器上的固件路径即可。
ESP32 示例（根据 esptool 版本、芯片、工程分区和构建输出选择实际命令）：

```bash
# 先做只读识别，PORT 替换为 list 返回的实际串口路径
python3 -m esptool --port PORT chip-id

# ESP-IDF 工程中，使用工程本身生成的正确烧录参数
idf.py -p PORT flash
```

不要在固件偏移、目标型号不明确时使用通用写入示例。
ML307H、BK7258 使用各自工具；Windows-only 工具不能直接在 Linux 运行。

UTA 服务端只管理 USB 路由，不提供烧录命令。进入下载模式、固件校验、烧录、
重启验证由服务器上的独立工具负责。外部工具可通过 `route_id` 跟随同一连接的重枚举。

建议给远程会话的操作说明：

> 本地开发板通过 UTA 接入。先运行 `sudo -n uta-server list`；
> 根据目标设备身份选择已挂载设备。若有多个候选，不要自动选第一个串口。
> 编译和固件文件留在服务器，使用本项目的烧录工具和正确参数。
> 分别报告编译、设备识别、烧录、重启日志和功能测试结果。

## 本地 CLI

```bash
CLI="/完整路径/UTA.app/Contents/Resources/uta"
"$CLI" server add --name "开发机" --target developer@YOUR_SERVER --port 22
"$CLI" server list
"$CLI" server check SERVER_ID
"$CLI" server install SERVER_ID
"$CLI" devices
"$CLI" connect --device BUSID --server SERVER_ID --reconnect auto
# 不需要模式重连时使用 --reconnect off
"$CLI" status
"$CLI" disconnect --device BUSID
"$CLI" stop
```

命令输出 JSON。`connect`、`disconnect`、`check`、`install` 返回 `started: true`
表示操作已经接受，**不代表完成**；请轮询 `status` 中的会话或检查状态。
成功返回退出码 0，拒绝/参数错误返回 1。

## USB 占用诊断

设备总览和服务器页面的设备卡片均显示「USB 占用诊断」。展开可查看：
接口绑定的系统驱动、串口路径、当前权限可见的串口占用进程（名称/PID/UID），
以及 IORegistry 中观察到的 USB 访问客户端。约每 10 秒更新一次，显示检查时间。
这些查询只读取信息，不打开串口、不接管 USB，也不会终止其他进程。

CLI 可立即查询单个设备：

```bash
./uta diagnose --device 2-1
```

“未发现串口占用进程”并不代表 USB 接口可接管：macOS 的串口系统驱动
可能仍独占接口；USB 访问客户端也不等同于独占者。设备信息无法唯一匹配、
权限不够或查询失败时，显示未知状态，不将其视为空闲。

如果接入日志报告 `Access denied`，App 会提示 **Mac USB 接管权限不足**，
而不是只显示云端的通用 USB/IP 拒绝消息。此问题发生在 Mac 本地，
提升云端权限不能修复。请使用下面的本机授权流程。

## Mac 本机 USB 授权

1. 将 UTA.app 放入 `/Applications` 或当前用户的 `~/Applications`，从该位置打开。
2. 点击「启用 USB 接管」，阅读用途说明，再点击「继续系统授权」。
3. 在 macOS「系统设置 → 通用 → 登录项与扩展」中允许 UTA，按系统提示验证管理员身份。
4. 回到 UTA，等待显示「本机 USB 接管已授权」，再选择设备接入服务器。

取消授权不会开始传输，授权完成也不会自动接管设备。系统批准与辅助服务连通
分别检查；辅助服务版本不匹配、未启动或被禁用时不会显示就绪。
活跃设备全部释放后，可点击「停用」撤销注册。升级开发版后如果版本校验失败，
先停用、退出旧版，再启动新版本重新启用。卸载 App 前也应先停用辅助服务。

CLI `uta permission` 查询辅助服务是否就绪；
`uta connect --device BUSID --server SERVER_ID --access auto` 为默认行为：
检测到系统驱动绑定或上次出现权限拒绝时使用已授权辅助服务；
`--access standard` 仅尝试普通权限，`--access authorized` 明确使用授权服务。
CLI 不读取密码，也不会绕过系统批准。取消、拒绝和缺少授权均保留设备在本机。

辅助服务使用 `SMAppService`/launchd，运行在管理员权限下，界面、Python 控制器和
SSH 继续使用普通用户权限。XPC 只接受本次构建的指定客户端，并限制为当前控制台用户；
每个请求只处理指定 busid、VID:PID 和可用序列号，最多 8 个独占会话。
root 服务静态链接 USB 实现，不执行来自客户端的命令、不接受程序或文件路径，
不开放 TCP 监听；通过 XPC 传递私有流和日志描述符。
SSH 通过权限为 0600、位于 0700 目录内的 Unix socket 连接该数据流。
网络断开、控制器退出或辅助服务被撤销时关闭数据流并尝试恢复本机接口驱动。

API 路线适用于 macOS 13+；**当前交付包仍仅构建、测试于 Apple Silicon / macOS 26**，
不能据此声明已兼容旧系统或 Intel。Build 7 已验证 Developer ID 签名辅助服务启动；
正式发布还需可信时间戳、公证、升级验证和更多设备实测。系统授权不等同于设备兼容性保证。

## 重要的第一版边界

- 首次接入由用户选定；没有自动烧录。已选设备支持上述有期限的模式重连，
  不覆盖任意 PID 变化；移动接口会改变 busid。
- macOS 的系统驱动可能占用某些 USB 接口。libusb claim 失败时会报告异常。
  不关闭 SIP、不安装未知内核扩展、不默认以 root 运行整个 App。
  需要特权或特殊 entitlement 的设备仍须单独解决，不能承诺任意 USB 兼容。
- 用于开发板首轮测试；音视频等时传输、Hub 多层拓扑、USB 存储等未做兼容性验收。
- 同一设备独占一台服务器；不同设备可分配到不同服务器。
- 云端记录按 SSH 用户限制查询/释放，但这是开发工具，
  **不是面向互不信任租户的安全隔离系统**。Linux 设备访问权限仍由管理员配置。
- 正常关闭窗口不停止后台服务；菜单“停止 UTA 后台服务”或 CLI `stop`
  会断开连接。后台服务崩溃时导出器和 SSH 输入管道会随之关闭。
  重启 App 不自动重新接管设备。
- 服务器检查状态是上次检查结果；本机传输进程在监测循环中检查，
  云端状态查询按至少 10 秒间隔节流。短暂查询失败显示「状态待确认」，
  保留最后确认的端口和现有数据通道；至少连续失败 3 次且距首次失败已过
  30 秒后才释放连接。真实进程退出或云端确认断开不使用该宽限期。
- USB 传输走 SSH 隧道，本地和远端监听请求均指定 loopback，
  无需在公网开放 3240；服务端 SSH 的 GatewayPorts 配置必须满足上述要求。
- 断网、云端退出或设备重启会中断烧录，不能从中断位置自动续传。

## 数据位置与清理

默认配置、日志：`~/Library/Application Support/UTA/`。
只保存服务器配置和诊断日志，不保存 SSH 私钥内容或密码。
测试可用 `UTA_HOME=/绝对路径` 指定隔离目录。
测试结束先执行 CLI `stop`，再移除 App。
云端卸载前先 `sudo uta-server list` 并按 session 执行 `detach --session ID`，
然后移除 `/usr/local/bin/uta-server` 和你手动创建的 sudoers 条目。

## 建议验收顺序

1. SSH 检查成功，安装代理后 `doctor` 成功。
2. 列出 Mac 实际 USB，接入后云端节点出现。
3. 先做不写入的芯片识别或设备描述符查询。
4. 执行一次已知固件烧录，确认启动日志。
5. 手动断开，确认设备回到 Mac；再接入服务器 B。
6. 两块不同设备分别接入服务器 A/B。
7. 拔插、断网、云端重启，确认显示异常而不是继续显示“已挂载”。

以上物理/远程步骤须在你的设备和服务器上执行；
本地构建和协议测试不替代这些步骤。
