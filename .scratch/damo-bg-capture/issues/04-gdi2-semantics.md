# 04 · gdi2 原语义 —— PrintWindow 不刷新时的救路

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口(别名直通 = 语义丢失)

大漠官方原文(damo-BindWindow.txt:74 段):
- gdi: 用于窗口采用 GDI 方式刷新时。
- gdi2: "此模式兼容性较强,但是速度比gdi模式要慢许多,如果gdi模式发现后台
  不刷新时,可以考虑用gdi2模式" —— 即 gdi2 是**另一种**截图实现(PrintWindow
  类 API 换路/换标志), 不是 gdi 的别名。

我们现状: `gdi`/`gdi2` 同路直通 PrintWindow(dmsoft.py:73)。
后果: PrintWindow 不刷新的目标, 传 gdi2 也不会换路 —— 救路不存在。

## 方案草案

- 调研 gdi2 真身(大漠逆向资料 reverse-skill 侧): 候选实现有
  ①PrintWindow 换标志(PW_CLIENTONLY/不同 DWM 路) ②GetDC+BitBlt(仅窗口
  在前台时有效) ③Windows Font/Terminal Services API 路。
  大漠 7.x 语境下 gdi2 大概率是 WM_PRINT 消息路(PrintWindow 失败目标
  常吃 WM_PRINTCLIENT)。
- capture 层实现第二路, `gdi2` 路由过去; 不刷新判定可复用
  `vision.is_display_dead` 语义做自检提示。

## 验收

1. 造一个 PrintWindow 全黑/冻结帧的目标(最小化自停绘类, AGENTS.md 有
  现象描述): gdi 绑定不动, gdi2 绑定后截图内容正确。
2. gdi 正常目标不受影响(回归 gdi 烟测)。

## Comments

### 2026-11-25 · 落地(agent)

- 实现路: **SendMessage(WM_PRINTCLIENT, hdc, PRF_CLIENT|PRF_ERASEBKGND)** 走窗口过程自绘
  (工单草案候选③的标准形态; SMTO_ABORTIFHUNG+2s 超时防目标挂起拖死本进程)。
  注: 大漠 gdi2 真身未逆向(reverse-skill 侧无截图路分析), 按官方文档语义
  ("gdi 不刷新时的兼容救路")选实现; WM_PRINT(0x317)先行试过 —— DefWindowProc
  不响应, 改发 WM_PRINTCLIENT(0x318)标准做法。
- 骨架: capture.py 抽 `_capture_loop`(尺寸/最小化还原/黑帧重试公共骨架),
  `capture_client`(PrintWindow)/`capture_client_gdi2`(WM_PRINTCLIENT)两路各选通道,
  原有行为零变化。
- 路由: `DmSoft.screen()` display=="gdi2" → gdi2 路(不再是 gdi 别名); gdi 路不动。
- 验收: selfcheck win32_capture 同窗双路对照(测试窗 wndproc 显式响应 WM_PRINTCLIENT
  自绘底色 = 真实 GDI 应用标准行为), 颜色断言过; 一次偶发全黑(重试骨架兜底)复跑过。
- 适用面诚实记录: WM_PRINTCLIENT 只对响应此消息的目标有效(GDI 输出型/标准控件窗);
  两路都黑的目标回注入件 dx 路 —— 这正是 dx2(工单 02)的价值面。

Status: ready-for-human(selfcheck 消息级验收; "PrintWindow 黑但 gdi2 活"的真靶出现时补实机对照)
