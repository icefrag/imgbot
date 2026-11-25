# spec · 大漠后台图色能力缺口清单(2026-11-25 清点)

对照 `docs/ref/damo-BindWindow.txt` / `damo-BindWindowEx.txt`(官方词汇)与
`imgbot/dmsoft.py` + `imgbot/dx/native/dmcore.c` 现状逐项清点。目标: 补齐长尾,
每张工单场景驱动开工, 不无目标预铺。

## 已搬齐(实机验证过, 不动)

| 大漠 | 潜影对应 |
|---|---|
| gdi(PrintWindow 后台) | `win32/capture.capture_client` |
| normal 前台 | `capture.capture_screen` |
| dx.graphic.opengl | 注入件 GL 钩(wglSwapBuffers/gdi32!SwapBuffers hotpatch, dmcore.c hook_gl) |
| dx.graphic.3d(d3d9) | 注入件 Present 钩(真尾跳, dmcore.c our_present) |
| FindPic/FindPicE、FindColor/E/Ex、FindMultiColor、CmpColor、GetColor(BGR)、GetColorNum、GetAveRGB、IsDisplayDead、Capture/Png/Jpg | dmsoft.py + vision.py 全套 |
| FindStr/字库 | 红线不搬, RapidOCR 替代(决策已定, 不翻案) |

## 缺口(按价值排序 → 工单)

| # | 缺口 | 工单 | 优先级 |
|---|---|---|---|
| 1 | dx.graphic.3d.10plus(dx10/11/12/DXGI 截帧) | 01 | 高(现代游戏主力交换链) |
| 2 | dx2/dx3 原语义(无注入挪屏外截屏路) | 02 | 高(杀软敏感场景唯一备胎) |
| 3 | dx.public.graphic.revert(GL 上下反转救路) | 03 | 中 |
| 4 | gdi2 原语义(PrintWindow 不刷新时的救路) | 04 | 中 |
| 5 | 长尾通道: opengl.esv2 + d3d8 | 05 | 低(出现靶再开工) |
| 6 | SetExcludeRegion(找图找色全局排除区) | 06 | 中 |
| 7 | 低频图色函数: FindColorBlock / GetAveHSV+RGB2HSV / CaptureGif / CapturePre / LockDisplay | 07 | 低 |
| 8 | BindWindowEx 细粒度词汇(逐属性解析器) | 08 | 决策: 不全抄, 见 spec 决策记录 |

## 决策记录 · BindWindowEx 不全抄(2026-11-25)

- 大漠细粒度属性词汇的存在意义是**手动试错面板**: 用户对几十种窗口逐个试
  display/mouse 组合, 错了黑屏/不刷新/崩溃。我们 `hook_auto`(GL 先试, 无
  opengl32 退 d3d9, host.py:277)把试错自动化了 —— 细粒度的主要痛点已消解。
- 鼠标面已等价: `mouse="dx"` 的 mouse_pack 全家桶 7 属性 = 大漠 "dx" 缩写
  预设逐钩对齐(2026-11-01); windows2 缩写语义已单独复刻。真要微调时走
  单独方法旋钮(EnableFakeActive / mouse_lock), 不要求用户拼属性串。
- 全抄成本: 注入件协议逐属性拆开关(dmcore 现为打包命令), 无目标驱动即死代码。
- 例外按需抄: 图色救路类公共参数(graphic.revert, 工单 03)值得抄; 遇到
  "全家桶副作用"类游戏再拆鼠标单属性开关。词汇字符串兼容已做到
  (BindWindow startswith("dx") 不炸), 不额外做解析器。
