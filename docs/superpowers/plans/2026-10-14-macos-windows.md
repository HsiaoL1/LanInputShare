# macOS 与 Windows 互通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 两种主控方向的 Mac/Windows 键鼠、文本、图片和文件复制粘贴。
**Architecture:** 保留共享网络协议，按平台构造原生输入、剪贴板和目录适配器。三个互不重叠的工作范围可按 dispatching-parallel-agents 并行执行，主控负责集成。
**Tech Stack:** Python 3.13、PySide6、PyObjC Quartz/Cocoa、Pillow、pytest。
**Spec:** ../specs/2026-10-14-macos-windows-design.md

## Global Constraints
- Windows 10/11 与 macOS 13+，Python 3.13；不添加屏幕视频传输。
- 延迟导入 Quartz/AppKit；保持旧 Windows 协议兼容、断线释放和紧急返回。
- 外部原生 API 测试替身可用，所有可纯 Python 验证的转换使用真实数据。

## Review Focus
- 修饰键释放/重复与平台切换不能卡键；左右修饰键分别追踪。
- Mac 事件 tap 初始化失败、超时或退出时不能遗留输入拦截或隐藏光标。
- 图片写入格式转换不能造成循环同步；文件 URL 必须限定本地文件。
- Retina 下尺寸和坐标必须采用同一单位，光标重置不能吞掉真实位移。
- Finder 权限拒绝或虚拟目录必须安全降级，不能错误写入其他目录。

### Task 1: macOS 输入后端（独立范围）
Files: src/lan_input_share/input/macos_*.py、input/queue.py、win32_hook.py 队列提取、tests/unit/input/test_macos_*.py。
Interfaces: MacOSInputHook.start(callback)/stop()/set_remote_active(bool), MacOSCursorController.position()/confine_for_remote()/recenter_remote()/restore_local(Point|None), MacOSInputInjector.inject(InputEvent)/release_all(tuple)。键码转换输出既有 KeyEvent；无需在后端交换 Control/Command。
- [x] 写键码、修饰键、滚轮、事件抑制、光标恢复与注入测试，运行看到失败。
- [x] 实现 Quartz 原生适配与安全生命周期，导入延迟；提取 HookEventQueue 为跨平台模块并保留兼容导出。
- [x] 运行输入与路由/运行时测试，输出通过。

### Task 2: macOS 剪贴板与文件目标（独立范围）
Files: clipboard/macos_clipboard.py、clipboard/image_conversion.py、transfer/macos_destination.py、tests/unit/clipboard/test_macos_*.py、tests/unit/transfer/test_macos_destination.py。
Interfaces: MacOSClipboardWatcher 与 Win32ClipboardWatcher 相同方法；ClipboardImage 保持 dib/dibv5。FinderDestinationResolver.resolve_foreground() 返回既有 ResolvedDestination。
- [x] 写真实 DIB 转换/图片循环标记、文件 URL、多文件写入和 Finder 降级测试，运行看到失败。
- [x] 实现 NSPasteboard、changeCount 监听、循环保护与 Finder 解析。
- [x] 运行剪贴板和文件目标测试，输出通过。

### Task 3: 平台选择、协议、主控快捷键及交付（主控范围）
Files: platform_support.py、config/paths.py、app.py、core/models.py、network/session.py、runtime/{controller,service}.py、pyproject.toml、scripts/build-macos.sh、packaging/macos.spec、docs 与跨平台测试。
Interfaces: create_input_hook/create_cursor/create_injector/create_clipboard/create_destination_resolver/primary_screen/ensure_permissions；DeviceInfo.platform 默认 win32。
- [x] 写平台选择、Mac 路径、握手平台字段、双向快捷键与文件粘贴行为测试，运行看到失败。
- [x] 集成后端，使用 Qt 单实例锁，补权限反馈、Mac 依赖和构建、文档与验收。
- [x] 合并并行范围后运行 pytest 全套、Ruff、mypy、诊断与 Windows 打包 smoke 检查；Mac 打包须在 Mac 执行。
- [x] 按 requesting-code-review 技能进行独立代码审查，修复实质问题并验证。

## 执行记录
用户直接要求实现；按会话授权连续执行，不重复请求方案批准。在当前干净仓库工作，避免另建工作树使用户找不到改动。测试环境默认 Python 3.11 且注册的 3.13 路径失效，先用 uv 配置项目要求的 Python 3.13。

- 平台识别、数据路径、权限反馈、双向快捷键、单实例锁和原生粘贴已按失败测试到通过测试实现。
- 输入与剪贴板两个独立范围完成；主控完成集成与检查。
- 独立审查发现三项实质问题：HID 捕获需 root、光标隐藏/关联需前台、无对应键位会断开连接；均增加回归测试并修复。
- 决定：Mac 使用 session event tap 和仅移动光标的后台方案，操作远端时本机光标保持可见；避免依赖前台 API 或抢占用户当前应用焦点。
- 决定：macOS 的 PySide6 上限为 6.11 以下以保留 macOS 13 支持；Windows 保持原范围。Mac 静态检查排除 Windows 原生模块，Windows 上仍完整检查所有源文件。
- 审查未判定的真实 Mac 权限、输入、Finder 和打包/双机行为：明确标为未实机验收，提供显式原生集成测试和双机验收清单，不作为已通过结果。
- 额外回归：原生输入失败时释放粘贴修饰键；光标切换失败时暂停共享并返回本机，输入工作线程仍可恢复。
- 最终验证：pytest 453 通过、4 跳过；Ruff 通过；Windows 全部 82 源文件与 Darwin 排除 Windows 原生的 73 源文件 mypy 通过；Mac Bash 构建脚本语法检查通过；Windows PyInstaller 构建通过。
