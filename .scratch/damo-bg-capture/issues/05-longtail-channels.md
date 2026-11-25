# 05 · 长尾通道 —— opengl.esv2 + d3d8

Status: needs-triage
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口

两条长尾渲染后端, 注入件未实现, 出现真实靶再开工(无目标不预铺):

1. **`dx.graphic.opengl.esv2`**(BindWindowEx:223): OpenGL ES 2.0 路的
   opengl 图色。极少数窗口用 OpenGL ES 引擎刷新(嵌入式/移植引擎),
   钩点与桌面 GL(wglSwapBuffers)不同 —— ES 无 wgl, 常经 ANGLE 转 d3d9
   (此时现有 d3d9 钩意外覆盖)或原生 ES 驱动(需钩 eglSwapBuffers)。
2. **`dx.graphic.3d.8`**(BindWindowEx:189): dx8 图色, 大漠注明"此模式对
   64位进程无效"(我们 x86 注入件形态下可用)。老游戏(x86)用 d3d8,
   钩法与 d3d9 同为 vtable(Present 槽位不同, d3d8 设备接口布局需查证)。

## 方案草案(出现靶后)

- esv2: 先验 ANGLE 路是否已被 d3d9 钩覆盖(大概率是); 原生 ES 加
  CMD_HOOK_EGL 钩 eglSwapBuffers, 帧协议复用 GL 路。
- d3d8: 复制 hook_d3d9 的 vtable 路, 换 IDirect3DDevice8 槽位表;
  d3d9test.c 加 d3d8 靶。
- 两者都不动 hook_auto 顺序(GL → d3d9 优先), 仅作显式 display 词汇时启用。

## 验收(各子项独立)

1. esv2: ANGLE 目标先确认 d3d9 钩已够; 原生 ES 靶截图连续帧内容正确。
2. d3d8: 自写 d3d8 靶(d3d9test.c 同款结构)真机截图正确 + 真尾跳不崩。
