# Mac 与 Mac、Windows 互通

macOS 原生适配支持 Mac 与 Mac 连接，也支持 Mac/Windows 任一方向作为主控。共享键盘、鼠标、文字、截图以及文件/文件夹复制粘贴。每台电脑使用一个显示器；程序不传输屏幕视频。

macOS 代码已在 Mac 上完成自动测试和本机打包烟测；真实双机输入和权限尚未验收。

## 在 Mac 上运行

要求 macOS 13 或更高版本、Python 3.13。Apple Silicon 与 Intel 使用相应架构的 Python；Windows 的 EXE 不能在 Mac 上运行。

在 Mac 终端进入项目目录：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m lan_input_share --diagnose
python -m lan_input_share
```

首次运行，在“系统设置 → 隐私与安全性”中允许：

- **辅助功能**：两种角色均需要，用于模拟输入与恢复光标。
- **输入监控**：Mac 作为主控时需要，用于监听全局键鼠。
- **自动化 → Finder**：文件粘贴时可能请求，用于读取当前目录并在传输前检查重名。拒绝或无法取得目录时使用剪贴板暂存后原生粘贴，重名处理由目标应用负责。

使用源码运行时，系统可能显示所用 Terminal、Python 或 IDE 的名称；允许实际运行的程序。修改权限后完全退出并重新打开 LanInputShare，再点击“保存并启动”。主窗口提供辅助功能与输入监控设置入口，`--diagnose` 只检查权限，不请求权限。

## 连接两台电脑

两端使用同一版本；先启动客户端，设置设备名、客户端角色、实际 Wi-Fi/以太网网卡，然后“保存并启动”。在主控选择主控角色、摆放本机和客户端位置、填写客户端局域网 IPv4 和端口 `24870`。连接后到客户端核对设备名、证书指纹并确认配对。两端防火墙允许专用局域网连接，VPN/TUN 的局域网地址走物理网卡。

将鼠标移出相邻屏幕边缘即可控制另一台电脑。Mac 到 Mac 时 Command、Control 和 Option 保持原样。Windows 主控的 `Ctrl+C/V` 在 Mac 目标上转换成 `Command+C/V`；Mac 主控的 `Command+C/V` 在 Windows 上转换成 `Ctrl+C/V`。跨平台时 Control 和 Command/Windows 修饰键互换，其余按键按位置映射，字符、输入法及键盘布局由目标系统决定。

Mac 主控操作远端时，本机光标会停驻在屏幕中央并保持可见，以兼容应用在托盘和后台运行；返回本机后恢复位置。Mac 没有对应键位的 Windows 按键（如 Print Screen、菜单键）会被忽略，连接保持正常。

Mac 主控的紧急返回为 **Control+Option+Esc**，设备切换为 **Control+Option+1/2/3**；Windows 仍使用 **Ctrl+Alt+Esc** 和 **Ctrl+Alt+1/2/3**。

文字和截图自动同步。在来源复制文件/文件夹后切换到目标，使用主控自身的 `Ctrl+V`（Windows）或 `Command+V`（Mac）开始传输。已解析的 Finder/资源管理器目录会在传输前检查冲突；其他应用由其原生粘贴流程接收文件。剪切按复制处理。

## 安装与收集日志

打开 `LanInputShare-macos.dmg`，将 `LanInputShare.app` 拖到“应用程序”，再从“应用程序”启动。首次使用按上文授予权限。当前测试包按构建机器的 CPU 架构生成；Apple Silicon 与 Intel 需要各自对应的包。本次 arm64 测试包尚未使用 Developer ID 签名和公证；若首次启动被 macOS 阻止，尝试打开应用后进入“系统设置 → 隐私与安全性”，选择“仍要打开”。仅对确认来源的测试包执行此操作。

遇到问题时，在**两台 Mac 上分别**点击主窗口或托盘菜单的“导出诊断与日志”，各选一个保存位置。生成的 ZIP 包含诊断信息、运行日志及近期轮转日志、原生崩溃日志；文件名默认包含本机名称和导出时间。请把两个 ZIP 一起提供，并说明哪台是主控、操作步骤和发生时间。若程序无法打开，可手动从 `~/Library/Application Support/LanInputShare/logs` 取出日志。分享前请检查设备名、局域网 IP 等信息。

## 构建 Mac 应用

必须在 Mac 上执行，不支持从 Windows 交叉生成 `.app`：

```bash
bash scripts/build-macos.sh
```

生成 `dist/LanInputShare.app`、`dist/LanInputShare-macos.zip`、`dist/LanInputShare-macos.dmg` 及各包的 SHA-256 文件。构建前运行测试、Ruff、mypy，构建后检查 Qt 加载和只读诊断。以 Apple Silicon Python 构建 arm64 包，以 Intel Python 构建 x86_64 包。可通过 `MACOS_CODESIGN_IDENTITY` 指定本机签名身份；公开分发所需的签名与公证仍需在 Mac 上完成。复制到 `/Applications` 后再授予权限，避免移动程序后需要重新授权。

配置、身份、日志和暂存文件位于 `~/Library/Application Support/LanInputShare`。诊断里 `platform` 应为 `darwin`。

## 验证

普通测试：`python -m pytest -q`。原生 Mac 集成测试使用 `LAN_INPUT_SHARE_RUN_MACOS_INTEGRATION=1 python -m pytest -q -m macos_integration` 显式启用，它们会暂时读写剪贴板或测试输入，先保存正在编辑的内容。

真实设备检查见 [Mac/Mac 双机验收清单](acceptance/macos-macos-checklist.md)和 [Mac/Windows 双机验收清单](acceptance/macos-windows-checklist.md)。Secure Input（例如密码输入）、登录界面、锁屏、Windows UAC 安全桌面以及多显示器布局不在当前范围内。
