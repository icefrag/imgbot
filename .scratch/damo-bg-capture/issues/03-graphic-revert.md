# 03 · dx.public.graphic.revert —— GL 截图上下反转救路

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口

大漠公共参数 `dx.public.graphic.revert`(BindWindowEx 词汇, 公共参数按 `|`
拼在 display 里): 有些 OpenGL 窗口截出来上下反向, 加此参数反转。
官方原文(damo-BindWindowEx.txt:201 段): "有些窗口采用这个参数截图后,上下
是反向的,可以在公共参数里增加 dx.public.graphic.revert 来反转"。

我们现状: GL 钩已通但无反转旋钮。遇反向目标(较少但存在, 大漠专门为此
做了参数说明是实打实的坑)帧全废, 没有救路。

## 方案草案

- 反转实现放**帧捕获侧**(注入件 or 宿主后处理二选一): 宿主后处理最省
  (DxHost 拿帧后 np.flipud 一次), 注入件侧省一次拷贝。先宿主侧, 性能
  不够再下沉。
- 旋钮面: `DmSoft` 加独立方法(大漠公共参数是字符串拼 display, 我们按
  spec 决策走独立方法, 如 `SetGraphicRevert(enable)`), 不做词汇解析器。
- GL 钩捕获路(dmcore hook_gl)已有帧结构, 反转只动行序不动列序。

## 验收

1. 反向靶(如遇到再补真实靶; 临时靶可自写 GL 窗口人为反向, 参考 d3d9test.c
   加 GL 版): 截图与前台对照上下一致。
2. 旋钮开关往返, 帧内容可逆恢复(互为 180° 翻转)。

## Comments

### 2026-11-25 · 落地(agent)

- 宿主后处理实现(帧捕获侧不动 C, 按工单草案首选路): SHM 帧两路(GL/d3d9)统一自底向上,
  `DxHost.capture` 统一翻正 —— `_revert` 开关跳过翻正即为反转(两路通用, GL_RGB 通道序
  处理不受影响)。
- 旋钮面: `DxHost.set_revert(enable)` + `DmSoft.SetGraphicRevert(enable)`(1/0, 需 dx
  通道, `_need_dx` 守卫), 不做 BindWindowEx 词汇解析(spec 决策)。
- 翻转语义 = 180° 行序翻转, 开关往返可逆(selfcheck 无法离线构 SHM, 属一行 if 分支;
  真机验收见下)。

Status: ready-for-human(代码+wiring 落地; 反向 GL 靶待遇 —— 出现反向目标时用
SetGraphicRevert 往返对照截图即真机验收)
