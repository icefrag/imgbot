# 02 · dx2/dx3 原语义 —— 无注入挪屏外截屏备胎路

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口(语义漂移)

大漠 `dx2`/`dx3` 是**无注入**路: 目标窗口大部分挪出屏幕(留一小部分在屏内/
win7+ 可全出屏), 对可见部分截屏补全, 不 hook 目标进程, CPU 较大。
dx2 用于 dx 刷新窗口; dx3 同 dx2 的兼容变体(慢但治后台不刷新)。

我们现状: `BindWindow` 里 `startswith("dx")` 一律走注入件(dmsoft.py:75),
`"dx2"/"dx3"` 字面被吞进注入件路 —— 行为与大漠不同(大漠 dx2 完全不注入)。

## 价值

杀软敏感场景唯一备胎: 注入件会被 Defender 拦(见 AGENTS.md, injector64
反复被隔离需加排除)。dx2 路零注入零落盘 exe, "能截图但不能注入"的机器上
是唯一后台图色解。

## 方案草案

- capture 层加 dx2 路: 挪窗口出屏(参考 park_offscreen 既有细缝停靠经验,
  注意 DXGI 遮挡节流坑 —— 离屏过深截图变暗冻结, AGENTS.md 有结论)→ 截屏
  → 按窗口位移补全客户区 → 还原位置。
- dx3 = dx2 的重试变体(大漠语义: dx2 不刷新时用 dx3), 可先同实现标记区分。
- SetAero 依赖(DWM 合成关闭时 dx2 才稳定): win10+ 已无此概念, 按大漠文档
  仅 win7/vista 语义, 现代系统不用管。
- BindWindow 路由: display=="dx2"/"dx3" 不进注入件, 走 capture 层; 文档
  写明与大漠 dx 缩写的区别(dx = 2d|3d 注入)。

## 验收

1. 一个拒绝注入的目标(护甲进程如华夏 game.exe): dx2 绑定 → 后台截图内容正确。
2. 注入件已装但被杀软拦的机器上, dx2 路独立可用。
3. 挪窗进出屏不残留位移误差(截图坐标仍统一客户区)。

## Comments

### 2026-11-25 · 落地(agent)

- 机制定案(不猜大漠内部): 按官方字面语义 + 库内 park_offscreen 既有结论拼接 ——
  "保证窗口有一部分在屏幕外" = 挪窗停靠屏外细缝(防 DXGI 遮挡节流, 大漠文档同一坑);
  无注入截图 = PrintWindow 读 DWM 重定向表面(AGENTS.md"完全离屏触发节流→截图变暗
  冻结"反证: 细缝在屏内时 PrintWindow 帧是活的)。"占用 CPU 较大" = 游戏持续渲染。
- 实现: `BindWindow` display=="dx2"/"dx3" → `park_offscreen(True)` + `self._parked`
  记账; `screen()` dx2/dx3 → `capture_client`(PrintWindow); `UnBindWindow`/`SwitchBindWindow`
  还原/迁移停靠; dx 系键鼠在此词汇显式 NotImplementedError(无注入路的语义边界)。
  dx3 = dx2 同实现(官方"dx2 后台不刷新时的兼容变体", 慢路语义留真靶出现再分)。
- selfcheck win32_window: 绑定停靠断言 / 图色帧尺寸+底色断言 / dx3+dx 键鼠拒绝 /
  unbind 还原原位。夹具顺修: create_visible_window 改 AdjustWindowRectEx 反推外框
  (固定 +16/+39 只在 100% DPI 成立; 本机 200% DPI 感知进程下客户区会偏)。
- 过程坑留档: 本机 200% DPI, dx2 段因 BindWindow→ensure_dpi_aware 转感知后
  create_visible_window 旧夹具客户区偏 (236x68), 曾误判 dx2 全黑 —— 实为夹具尺寸
  断言写死 200x100, 已改读实际客户区。BitBlt err=6(前台截屏段)与 win32_input
  cursor_seed 断言经 HEAD 干净 worktree 对照为**基线既有环境敏感失败**(桌面会话
  状态), 不在本轮影响面, 不修。

Status: ready-for-human(selfcheck 消息级+还原语义验收; 杀软拦注入件真机靶出现时补实机对照)

### 2026-11-25 · 提交后 review 留痕

- 验收 1/2(护甲靶/杀软靶真机)仍未闭合, 待靶 —— 与机制定案裁定一并挂起。
