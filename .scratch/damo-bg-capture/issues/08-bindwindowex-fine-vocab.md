# 08 · BindWindowEx 细粒度词汇 —— 决策: 不全抄, 按需抄救路旋钮

Status: needs-triage
Category: decision
Reported: 2026-11-25 (用户问询: "大漠 bind 可以按细节参数去绑, 这个我们不用抄吗")

## 问题

大漠 BindWindowEx(hwnd, display, mouse, keypad, public_desc, mode) 支持逐
属性 `|` 组合(如 `dx.graphic.opengl|dx.public.graphic.revert`,
`dx.mouse.position.lock.api|dx.mouse.state.message|...`, 几十个属性),
BindWindow 的 "dx" 等只是缩写。潜影现状: display 字符串兼容(startswith
不炸)但无逐属性解析; 鼠标走 mouse_pack 全家桶预设。

## 决策(2026-11-25, 已写入 spec.md 决策记录)

**不全抄 BindWindowEx 逐属性解析器。**

理由:
1. 大漠细粒度词汇的存在意义是**手动试错面板** —— 用户对几十种窗口逐个
   试组合, 错了黑屏/不刷新。我们 `hook_auto`(GL→d3d9 自动选, host.py:277)
   把试错自动化了, 细粒度的主要痛点已消解。
2. 鼠标面已等价: `mouse="dx"` 的 mouse_pack 7 属性 = 大漠 "dx" 缩写逐钩
   对齐(2026-11-01); windows2 缩写语义已单独复刻。微调走独立方法旋钮
   (EnableFakeActive/mouse_lock), 不要求用户拼属性串。
3. 全抄成本: 注入件协议逐属性拆开关(dmcore 现为打包命令), 无目标驱动
   即死代码 —— AGENTS.md 简洁原则直接适用。

## 按需抄例外(出现场景再开工)

- 图色救路类公共参数: `dx.public.graphic.revert`(→ 工单 03, 唯一救路,
  值得做)。
- "全家桶副作用"类游戏(某单属性钩子引发异常行为): 届时拆鼠标单属性
  开关(如只要 position.lock 不要 ClipCursor 吞噬), 场景驱动。
- 词汇字符串兼容保持现状(startswith 不炸 + 未实现钩子明确报错), 不做
  词汇解析器。

## 已有等价物对照

| 大漠公共参数 | 潜影等价 |
|---|---|
| dx.public.active.api/message | DmSoft.EnableFakeActive(1)(消息级+API 级双锁) |
| dx.public.hide.dll | DxHost.hide_module(PEB 三链摘除) |
| dx.mouse.position.lock.* | mouse_pack 内光标锁 + mouse_lock 独立旋钮 |
| dx.mouse.cursor | mouse_pack 内 GetCursorInfo 改写 |
| dx.public.graphic.revert | **缺**(工单 03) |
