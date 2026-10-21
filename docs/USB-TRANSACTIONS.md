# Linux USB 传输接口：减少跨网往返

`uta-usb-io` 是可选的 USB 事务工作进程，运行在已挂载设备的 **Linux 服务器**。
它不下载固件到 Mac，不模拟设备应答，不修改 USB/IP 协议，也不提供通用烧录命令。
外部应用仍负责型号校验、固件解析、备份、CRC、命令 ACK、复位与失败恢复。

依赖：Python 3.9+、`libusb-1.0.so.0`，以及所选 USB 节点的访问权限。
Node 适配层需要 Node 18+。安装交付包中的两个文件到同一目录：

```sh
sudo install -d -m 755 /usr/local/lib/uta
sudo install -m 755 uta-usb-io /usr/local/lib/uta/uta-usb-io
sudo install -m 644 uta-usb-transport.mjs /usr/local/lib/uta/uta-usb-transport.mjs
```

先用 `uta-server list` 按 UTA 会话与设备身份找到当前节点；不能固定节点编号，
也不能在多个候选中默认选择第一个。打开时再次检查 bus/address、VID、PID。
接口被占用时返回错误，不强制卸载 Linux 驱动。

```js
import {UtaUsbTransport} from '/usr/local/lib/uta/uta-usb-transport.mjs';
const usb = await UtaUsbTransport.open({
  path: selectedNode, vid: selectedVid, pid: selectedPid, iface: selectedInterface,
  input: inputEndpoint, output: outputEndpoint, packetSize: compatiblePacketSize,
});
try {
  // frame 由目标设备协议生成，received 仍必须经过协议校验。
  const received = await usb.exchange(frame, 60000);
  // 若响应分成多次 USB IN，继续 read，直到该协议完整响应到齐。
  // 只有校验和、状态码与长度全部正确，才允许发下一条协议命令。
} finally { await usb.close(); }
```

`write` / `read` 也可单独调用。`exchange` 提前提交一个 IN，然后一次排队
提交 **同一命令** 的所有 OUT 包；每个 OUT 完成都检查长度与状态。
物理包顺序由同一 USB endpoint 的传输队列保持。
`packetSize` 必须由应用指定，最大 512；整倍数时追加 ZLP；单帧最多 16 KiB。
此参数是已验证的设备接收粒度，不一定等于描述符的最大包长。

原始 JSON-lines API 示例（每行请求对应一行应答，禁止并发协议事务）：

```json
{"op":"open","path":"/dev/bus/usb/001/017","vid":4660,"pid":22136,"iface":0}
{"op":"exchange","out":1,"in":129,"hex":"由应用生成的十六进制帧","chunk":64,"size":16384,"timeout":60000}
{"op":"close"}
```

上面节点编号只是示例，hex 占位必须替换为真实协议帧。成功输出
`{"ok":true,"result":{"hex":"...","written":123}}`；异常输出 `ok:false`。
超时、短写或 USB 错误之后会使会话失效，不自动重试；未确认的写入不能安全重放。

## 产品边界

UTA 不包含任何芯片的下载、擦除、写入、备份或固件验证逻辑。
外部应用可以选择这个通用传输接口，也可以直接使用 Linux USB 节点与现成驱动。
增加设备类型不需要修改 UTA；具体兼容性仍取决于 USB 请求类型和系统驱动。

一个完整协议命令何时结束、怎样校验 ACK、是否需要 ZLP、数据块多大，均由外部
应用负责。该接口不会替现有同步应用自动合并请求或改变设备协议。
