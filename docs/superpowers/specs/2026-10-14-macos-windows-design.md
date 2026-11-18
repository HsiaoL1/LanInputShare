# macOS 与 Windows 互通

## 目标与范围
用户使用一台 Mac 和一台 Windows，希望共享键鼠并跨设备复制粘贴。支持两种主控方向、现有文本/图片/文件与文件夹复制、最多三台设备的已有布局。用户明确不需要实时屏幕画面共享。

## 设计
复用 TLS、设备配对、路由、文件传输与 PySide6 界面。增加按运行平台选择输入钩子、光标、输入注入、剪贴板、屏幕尺寸和粘贴目标的工厂接口；Windows 后端保持原行为。macOS 使用 PyObjC 的 Quartz 事件 tap/Post 和 AppKit NSPasteboard，平台导入必须延迟以使其他平台能够加载与测试。

键盘事件沿用 Windows 扫描码和虚拟键作为线上的规范表示。Mac 捕获时转换 Apple 键码；握手增加可选 platform 字段，旧设备默认 win32。跨平台主控在发送时交换 Control 与 Command/Windows 修饰键，使 Ctrl+C/V 与 Command+C/V 适配目标系统；本机紧急返回始终 Control+Alt/Option+Esc。Retina 坐标按 Quartz 显示边界的逻辑坐标一致处理。

Mac 剪贴板监听 changeCount；文本、文件 URL、私有循环标记和图片读写通过 NSPasteboard。图片在 Mac 边界转换为现有 DIB 数据，以保持协议兼容；远端写入后保留原始图片及标记，避免格式转换引起广播循环。文件优先解析 Finder 的前台目录进行冲突预检；无法解析时沿用暂存后原生粘贴的兼容流程。源文件不删除。

macOS 配置位于 ~/Library/Application Support/LanInputShare。使用跨平台 Qt 文件锁避免重复运行。启动前检查辅助功能权限，主控还检查输入监控权限；缺失时显示具体系统设置路径。提供 Mac 构建 .app 的脚本和说明，支持 Apple Silicon 与 Intel 的本机架构构建。

## 验证与限制
Windows 使用 Python 3.13，运行现有 pytest、Ruff、mypy；增加平台选择、键码/快捷键、坐标、权限、剪贴板转换/防循环、文件目标的行为测试。Mac 原生接口在 Windows 用明确的 API 边界测试替身验证调用参数和生命周期；Mac 上提供显式启用的原生集成测试和双机验收清单。没有真实 Mac 测试结果时不宣称双机验收通过。Secure Input、登录界面和 UAC 安全桌面不支持。
