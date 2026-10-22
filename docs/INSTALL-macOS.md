# UTA Mac 预览版安装指南

下载：https://github.com/TailorLiuuu/UTA/releases/tag/v0.1.0-preview.3

## 安装前确认

- Apple Silicon Mac（M 系列芯片），macOS 26 或更新版本；目前只在 macOS 26 上验证。
- Python 3.9 或更新版本。本预览包没有内置 Python。
  可以使用 https://www.python.org/downloads/macos/ 的 macOS 安装器；
  若已安装 Homebrew，也可以运行 `brew install python`。
- 一台可用密钥或 SSH Agent 登录的 Linux 服务器，内核支持 `vhci_hcd`。
  设备挂载需要管理员权限，普通受限容器可能不支持。

本预览版使用 Developer ID 签名和可信时间戳，但尚未经过 Apple 公证。
签名证书显示 CyberTronics Limited，项目发布仓库为 TailorLiuuu/UTA。
它不是免提示安装的正式发行版；系统批准和设备兼容性是不同的检查。

## 安装

1. 下载 `UTA-0.1.0-macOS-arm64.dmg` 并打开。
2. 将 UTA 拖入旁边的 Applications 文件夹。不要直接从磁盘映像中运行。
3. 弹出磁盘映像，在“应用程序”中打开 UTA。
4. 如果 macOS 因未公证而阻止打开，在确认下载来自上述仓库后，
   到“系统设置 → 隐私与安全性”使用系统提供的“仍要打开”，按提示确认。
   无需关闭 Gatekeeper 或 SIP。公司管理策略不允许时请联系管理员。

也可以下载 ZIP，解压后将其中的 UTA.app 移到“应用程序”。ZIP 还包含本地 CLI
启动器和 Linux 服务端脚本；一般桌面用户只需 DMG。

## 首次使用

1. 在终端运行 `ssh 你的服务器别名`，核对服务器指纹，确认密钥登录成功。
2. 在 UTA 中从 SSH 配置导入服务器，或手动添加。
3. 点击“安装云端代理”。如果账号没有自动安装所需权限，使用旁边的“手动安装”。
4. 如需接管 macOS 驱动占用的 USB，点击“启用 USB 接管”。在系统设置
   “通用 → 登录项与扩展”中允许 UTA，回到 App 等待授权就绪。
5. 插入开发板，选择服务器并接入。等到“云端已挂载”，使用卡片显示的云端端口。

云端开发工具仍需设备对应的驱动与协议支持。UTA 不会自动烧录，也不会保证第三方工具的烧录速度。
启用“允许设备切换模式后重新接入”后，开发板在同一 USB 插口切换模式、出现新标识时，
UTA 会询问是否仍是刚才的设备。确认后记住该模式并接回原服务器；也可拒绝接入。
已记住的模式可在高级设置中查看、修改或清除。重连等待限时 2 分钟，不会自动续烧。

## CLI

Mac 本地：

    /Applications/UTA.app/Contents/Resources/uta --help
    /Applications/UTA.app/Contents/Resources/uta status

Linux 服务器：

    sudo -n uta-server list

显示的 `/dev/tty*` 和 `/dev/bus/usb/*` 属于服务器。多个设备存在时，先核对身份再选择。

## 升级与卸载

Build 15 起可在侧栏或 UTA 菜单点击“检查更新”，或开启“自动检查更新”接收提示。
先断开 USB 并等待云端清理完成，再在更新窗口下载、安装并重启。
Preview 2 及更早版本需要手动安装一次新版：断开所有设备、停止 UTA 后台服务并退出界面，再替换 App。
若升级后辅助服务检查提示版本不匹配，先停用，再重新启用并完成系统批准。
不要同时运行来自磁盘映像、下载目录和“应用程序”的多个副本。

卸载前先释放设备、停用 USB 接管服务，再停止后台并退出 App，最后移除应用。
配置与日志保留在 `~/Library/Application Support/UTA/`。

## 校验与反馈

Release 附有 SHA256SUMS。在下载文件所在目录可运行：

    shasum -a 256 UTA-0.1.0-macOS-arm64.dmg

将结果与 SHA256SUMS 中同名文件的值比较。
问题反馈：https://github.com/TailorLiuuu/UTA/issues
请说明 macOS 版本、设备 VID:PID、Linux 版本和错误信息；上传日志前移除私人地址、密钥及设备身份。

完整说明：https://github.com/TailorLiuuu/UTA/blob/main/docs/TESTING.md
