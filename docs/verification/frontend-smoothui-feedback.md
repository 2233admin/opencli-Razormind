# SmoothUI 操作反馈验收（范围已修订）

## 当前范围

本轮保留 SmoothUI 官方 [Button Copy](https://smoothui.dev/r/button-copy.json) 的源码来源与 `frontend/components/smoothui/LICENSE` MIT 许可。没有新增 npm 依赖或覆盖全局基础按钮。

- `AsyncActionButton` 负责真实 Promise 的 idle、pending、success、error 状态；失败可重试，成功自动恢复，卸载时清理计时器并防止旧异步结果更新状态。
- API/MCP 页面四个复制入口使用 `ButtonCopy`，反馈保留复制对象名称；剪贴板拒绝或不可用时保留错误重试状态，不展示被复制的秘密内容。
- Inbox 已明确保持现有 Linear 双栏信息架构、紧凑工具栏和普通计数，并保留原 SmoothUI 局部审批/反馈交互。Inbox 不接入本轮数字徽章、空状态重排或同步按钮改造。

## 已撤回范围

此前记录的 Inbox 数字计数动画、空状态单区重排、同步入口互斥与同步状态按钮，以及围绕这些内容的移动端、焦点和失败恢复验收，随用户纠正一并撤回，不作为当前验收依据。

## 当前验证状态

2026-09-05 撤回后重新验证：

- Inbox 页面与原 Linear 版本完全一致，Git 无该文件差异；原有 SmoothUI 审批组件保留。
- 浏览器 2/2 通过（43.3s）：原收件箱搜索无结果时的输入、键盘和 URL 稳定性，以及保留的 API 复制成功、失败重试、状态名称与减少动画。
- 控制台脚本 25/25 通过，类型检查和修改范围 ESLint 通过。
- 上一轮围绕 Inbox 重排的验收不再适用，对应测试与未使用的数字徽章组件已移除。
