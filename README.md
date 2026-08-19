# RIGOL DHO SCPI Skill

一个用于 RIGOL DHO800/DHO900 系列数字示波器的 Codex Skill，支持通过 LAN SCPI 或 USBTMC/VISA 查询、测量和控制示波器，也可以抓取前面板屏幕截图。

适用型号包括 DHO814、DHO924 等 DHO8xx/DHO9xx 设备。其他 RIGOL 产品系列的 SCPI 命令可能不同，使用前请查阅对应型号的编程手册。

## 功能

- 通过 LAN 连接示波器，无需安装额外 Python 依赖
- 通过 USBTMC/VISA 发现并访问 RIGOL 示波器
- 查询设备身份、触发状态、采样率、存储深度和时基
- 执行常用自动测量，例如峰峰值、有效值、频率和周期
- 读取文本或 IEEE-488.2 二进制响应
- 抓取示波器前面板 JPEG 截图
- 对会改变设备状态的命令强制要求显式确认
- 提供无需真实硬件的离线自测

## 安装为 Codex Skill

将仓库克隆到 Codex 的个人技能目录：

```powershell
git clone https://github.com/sendu2wfdx/rigol-dho-scpi.git "$HOME/.codex/skills/rigol-dho-scpi"
```

安装后开启一个新的 Codex 对话，即可使用 `$rigol-dho-scpi`。

也可以只克隆仓库并直接运行 `scripts` 目录中的工具。

## LAN 连接

示波器默认使用 TCP 端口 `5555`。首次连接建议先测试端口，再读取设备身份：

```powershell
python scripts/dho_scpi.py --host 192.168.1.100 probe
python scripts/dho_scpi.py --host 192.168.1.100 idn
```

确认返回的型号以 `DHO8` 或 `DHO9` 开头后，可以查询采集状态和测量值：

```powershell
python scripts/dho_scpi.py --host 192.168.1.100 status
python scripts/dho_scpi.py --host 192.168.1.100 measure VPP CHAN1
python scripts/dho_scpi.py --host 192.168.1.100 query ":CHANnel1:SCALe?"
```

也可以通过环境变量设置默认地址和端口：

```powershell
$env:RIGOL_DHO_HOST = "192.168.1.100"
$env:RIGOL_DHO_PORT = "5555"
python scripts/dho_scpi.py status
```

## USBTMC/VISA 连接

USB 模式需要安装 [PyVISA](https://pyvisa.readthedocs.io/) 以及可用的 VISA 后端。Windows 下优先使用设备厂商提供的 VISA Runtime。

先枚举资源，再识别设备：

```powershell
python scripts/dho_usb_scpi.py list
python scripts/dho_usb_scpi.py discover
```

如果只发现一台 USB VID 为 `0x1AB1` 的 RIGOL 设备，工具会自动选择它：

```powershell
python scripts/dho_usb_scpi.py idn
python scripts/dho_usb_scpi.py status
python scripts/dho_usb_scpi.py measure VRMS CHAN1
```

连接多台设备时，请使用 `list` 或 `discover` 返回的完整 VISA 资源名：

```powershell
python scripts/dho_usb_scpi.py --resource "USB0::0x1AB1::...::INSTR" idn
```

> 不要为了让 USB 发现功能工作而直接替换 Windows 设备驱动。把设备切换到 WinUSB/libusb 可能会导致厂商 VISA 软件失效。

## 截取屏幕

DHO 的屏幕截图服务默认位于 WebSocket 端口 `9003`：

```powershell
python scripts/dho_screen.py --host 192.168.1.100 --output screen.jpg
```

默认不会覆盖已存在的文件。确实需要覆盖时才使用 `--force`。

## 修改示波器设置

只读查询可以直接执行。任何可能改变设备状态的 SCPI 命令都必须使用 `write`，并显式添加 `--confirm-write`：

```powershell
python scripts/dho_scpi.py --host 192.168.1.100 write ":CHANnel1:SCALe 0.5" --confirm-write --check-error
```

USB 模式用法相同：

```powershell
python scripts/dho_usb_scpi.py write ":CHANnel1:SCALe 0.5" --confirm-write --check-error
```

重置、校准、固件操作、许可证变更，以及删除或覆盖示波器文件属于高风险操作，应在确认具体命令和影响后单独执行。不要把 `*RST` 当作常规故障排查手段。

## 离线自测

无需连接示波器即可验证本地解析和客户端逻辑：

```powershell
python scripts/dho_scpi.py self-test
python scripts/dho_usb_scpi.py self-test
python -m py_compile scripts/dho_scpi.py scripts/dho_screen.py scripts/dho_usb_scpi.py
```

## 常见问题

### `probe` 成功，但 `*IDN?` 超时

这表示 TCP 端口可以连接，但 SCPI 服务没有及时响应。请检查示波器的 LAN/远程控制服务是否开启，以及是否有其他控制器占用了连接。

### LAN 连接被拒绝

检查示波器 IP、电脑与示波器是否处于同一网络、SCPI Socket 服务是否开启，以及端口是否为 `5555`。

### USB 枚举失败

如果 `list` 在枚举前就失败，通常是 PyVISA 或 VISA 后端不可用；如果返回空列表，则应检查 USB 线、示波器的 USB Device 模式、Windows 设备管理器和驱动绑定。

### 示波器返回 `Undefined header`

优先使用完整的 SCPI 命令拼写，并确认该命令适用于当前型号和固件版本。

## 项目结构

```text
.
├── SKILL.md                         # Codex Skill 指令
├── agents/openai.yaml              # Skill 展示信息
├── references/scpi-quick-reference.md
├── scripts/dho_scpi.py             # LAN SCPI 客户端
├── scripts/dho_screen.py           # 屏幕截图客户端
└── scripts/dho_usb_scpi.py         # USBTMC/VISA 客户端
```

更多命令、测量项目和波形传输示例请参阅 [`references/scpi-quick-reference.md`](references/scpi-quick-reference.md)。

## 免责声明

SCPI 命令执行成功只代表仪器接受了命令，并不能自动证明测量结果在电气意义上有效。分析测量数据时还应同时考虑探头倍率、耦合方式、带宽限制、时基、采样率和采集状态。
