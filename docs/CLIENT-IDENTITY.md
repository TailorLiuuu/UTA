# 多人共用服务器：选择自己的 UTA 设备

Preview 14 客户端隔离需要配套 App/云端 CLI。App 会自动使用本机身份，默认界面仍只显示本机接入的设备。
两人都用 root 时，普通 UTA 命令也只显示并管理当前客户端的连接；管理员仍能主动绕过此限制。
此功能用于避免误操作，不是对服务器 root 的强安全隔离。

## 为远程开发会话配置一次客户端上下文

在 Mac 终端查看已保存服务器的 ID，再导出该配置对应的凭据到一个新文件：

```bash
uta server list
uta client show --server SERVER_ID
uta client export --server SERVER_ID --output "$HOME/uta-my-client.json"
```

导出文件为 `0600`，命令只显示 ID 和文件位置，不把凭据内容打印出来。`export` 不启动后台
控制器、不连接服务器、不操作 USB，也不会覆盖已有文件。服务器备注修改后身份保持不变；
删除服务器配置再重新添加会生成新的配置 ID，对应新的身份范围。

用 SSH/scp 将这个文件放到服务器上属于**这次开发会话**的私有目录，例如：

```bash
ssh YOUR_SSH_ALIAS 'umask 077; mkdir -p "$HOME/.uta-contexts/my-mac"'
scp "$HOME/uta-my-client.json" YOUR_SSH_ALIAS:.uta-contexts/my-mac/client.json
ssh YOUR_SSH_ALIAS 'chmod 700 "$HOME/.uta-contexts/my-mac"; chmod 600 "$HOME/.uta-contexts/my-mac/client.json"'
```

将 `my-mac` 换成你自己的目录名，同事使用另一个目录。凭据不是固件，不需要每次烧录传输。
不要使用同事已经使用的目录；不要把凭据放进项目 Git 仓库，也不要把文件内容贴进聊天。
在共用 root 环境中，这些文件权限不能阻止其他 root 主动读取。

在**自己的远程终端/开发会话**设置环境变量：

```bash
export UTA_CLIENT_CREDENTIAL_FILE="$HOME/.uta-contexts/my-mac/client.json"
uta-server list
uta-server status --session SESSION_ID
uta-server resolve --route ROUTE_ID
```

不要写入共用 root 的全局 shell 配置，否则会把其他人的终端默认指向你的客户端。
每个远程开发会话分别设置自己的文件路径。没有上下文时 CLI 会报错，不会显示服务器上全部设备。

非 root 登录、通过受限 sudo 调用时，直接传文件路径，避免依赖 sudo 保留环境变量：

```bash
sudo -n uta-server list --client-file "$HOME/.uta-contexts/my-mac/client.json"
sudo -n uta-server resolve --route ROUTE_ID --client-file "$HOME/.uta-contexts/my-mac/client.json"
```

凭据文件必须为普通文件、权限 `0600`、属于调用者或 root；符号链接和宽松权限会被拒绝。
不要把凭据本身放进命令参数，`--client-file` 的参数只是路径。

## 烧录和日志工具照常使用

`list` 返回自己的 session、route_id 以及当前 `nodes`。`resolve --route` 返回该客户端下
唯一就绪的连接与节点；设备切换下载模式并自动接回时 route_id 保持不变，但 `/dev/ttyUSB…`
可能变化。等待就绪并查询新节点后，再让原来的烧录/日志工具打开它。

设备不存在、仍在枚举或出现多个匹配时，查询会报错；不要退回到“第一个串口”，也不要继续用
失效句柄。手动断开后重新发起连接会有新的 route_id；这与一次接入中的自动模式重连不同。
UTA 不自动扫描并选中同事的端口，不介入烧录协议；原烧录工具若自行扫描全部串口，仍需配置
它只使用查询出的节点。这里提供的是节点查询，不是禁止 root 直接打开其他节点。

## 明确的管理员操作与旧连接

root 管理员有意处理全部连接时可以运行：

```bash
uta-server list --admin
uta-server status --admin --session SESSION_ID
# 只有确认需要释放目标连接后才执行：
uta-server detach --admin --session SESSION_ID
```

App 不自动使用管理员模式。旧版记录没有客户端身份时，普通客户端不会自动认领，管理员可
明确查看或清理。升级应等正在使用的设备完成工作、断开并清理后再进行；本次开发测试不替换
现有安装，也不操作现有连接。

私有身份种子保存在 Mac 的 UTA 数据目录 `.client-identity.json` 中，不随普通服务器配置导出。
不要删除它或把它复制给同事；删除会让旧连接失去正常管理上下文，复制则共享身份。
分享某份导出的凭据也会共享对应身份。丢失凭据时由管理员明确处理旧连接，不能自动冒认。
