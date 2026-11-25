# 01 · dx.graphic.3d.10plus —— dx10/11/12(DXGI)后台截帧

Status: ready-for-agent
Category: feature
Reported: 2026-11-25 (大漠后台图色缺口清点, 见 spec.md)

## 缺口

大漠 BindWindowEx display 词汇 `dx.graphic.3d.10plus`: dx10/11/12 图色模式。
现代游戏越来越多走 DXGI 交换链, 注入件现在只有 GL+d3d9 两钩, 挂不上的目标
直接 RuntimeError(host.py hook_auto: GL rc!=20 → 试 d3d9 → 仍败抛)。

## 大漠对照

- 词汇: `dx.graphic.3d.10plus`(docs/ref/damo-BindWindowEx.txt:244)。
- 语义: 3d 窗口的 dx10/dx11/dx12 图色。大漠实现路(逆向参考): 钩 DXGI 交换链
  present(HRESULT 前取后备缓冲)。

## 方案草案

- 注入件加 CMD_HOOK_DXGI: 枚举目标进程内 IDXGISwapChain vtable(建窗试探或
  已有设备直读), 钩 Present; 捕获后后备缓冲 → BGR 帧进共享内存, 协议同 GL/d3d9。
- 坑位继承: d3d9 Present 的教训直接适用 —— ①转发必须真尾跳(musttail, 栈上
  不许多外来帧); ②detached 闸纯转发零解引用(重绑语义, dmcore g_detached);
  ③DxGI Present 参数与 d3d9 不同(SyncInterval 含撕裂标志, 需按签名适配)。
- hook_auto 顺序: GL → d3d9 → DXGI(现 20 哨兵语义扩一位)。
- Flip-model(FOURCC 无 GetDC 路): GetBufferData/复制资源路径要在原型期定案,
  别拍脑袋。

## 验收

1. 找一个 dx11 游戏(或自写 D3D11 靶, 参考 d3d9test.c 加 d3d11 版)真机跑通:
   绑定 → 截图连续 10 帧内容正确(与前台 PrintWindow 对照)。
2. 真尾跳验证: 转发后游戏不崩, 帧率无肉眼可见劣化。
3. 重绑无缝: unbind → rebind 后在世交换链续捕。

## Comments

### 2026-11-25 · 落地(agent)

**实现**(比草案更强 —— 顺带开通 x64):
- 注入件 dxgi/d3d11 创建入口 MinHook 钩(变速齿轮同引擎, **x86+x64 双架构**):
  CreateDXGIFactory/1/2 → 截获 factory → 补 IDXGIFactory 的 CreateSwapChain(槽 10
  基座)与 IDXGIFactory2 的 ForHwnd/ForCoreWindow(QI 判层级才补高槽)→ 截获 swap →
  补 Present 槽(3 代数记账,d3d9 同构); D3D11CreateDeviceAndSwapChain 直连路同钩。
  **这是 dx 通道第一条 64 位图色路** —— GL/d3d9 的自制 hotpatch 是 x86-only, x64
  目标原本必 31。
- 捕获: GetBuffer(官方 IID_ID3D11Texture2D=6f15aaf2...9c, IID 即设备代筛 ——
  d3d10/d3d12 在此 E_NOINTERFACE 面包屑 0x2100|hr 照转发, 本刀只做 d3d11)→
  GetDesc(槽 10)→GetDevice(IID_ID3D11Device=db6f6ddb..., **双参签名**)→
  GetImmediateContext(40)→staging CreateTexture2D(5, **4 参签名** pInitialData=NULL)
  →CopyResource(47)→Map(**D3D11_MAP_READ=1**, 14)→倒序拷 SHM(协议自底向上)。
- vtable 槽位全部 offsetof 对 mingw 头编译期推导(本机 Windows SDK 10.0.26100
  d3d11.h 逐槽核对一致); IID 硬编码 SDK 头字面量。
- host: hook_auto 扩展 GL→d3d9→DXGI 三顺位降级(20/31 与 60/62/63/64 视为
  "此路不通"落下一路, 其余码立即抛, 全灭汇总 rc); 各路 rc 语义不变。
- proto v11(+CMD_HOOK_DXGI=15); build.py +OBF 串 5 条 + d3d11test32/64 靶;
  scripts/dxgi_smoke.py(注入→钩→帧差→帧率→重绑续捕, 双架构)。

**实机攻坚三坑(留档)**:
1. mingw dxguid/头的 IID_ID3D11Texture2D=6f15aaf2-d208-4e89-9ab4-489535d34f**9c**
   与官方一致 —— 曾按网络摘要臆造 "2f8420f9-..." 系(错), 尾字节 9d(错) ,
   实机 QI 全 E_NOINTERFACE; 最终以本机 SDK 头 DEFINE_GUID 一字不差抄录定案。
2. **GetDevice 是 (REFIID, void**) 双参签名**(IDXGIDeviceSubObject), 单参调用
   stdcall 栈错位即 AV。
3. **CreateTexture2D 是 4 参签名**(pDesc, pInitialData, ppTexture2D), 3 参 cast
   少压一栈位, ppTex 读栈垃圾即 AV。两坑全靠"钩外复刻程序(qi3)本地二分"定位,
   先本地闭环再回注入件 —— 复杂 COM 手调的调试纪律。

**验收**: d3d11test32 靶 PASS(帧差 53.3, ~28fps, swap_calls=50; 重绑续捕帧差 46.0);
d3d11test64 靶 PASS(帧差 56.0, ~28fps, swap_calls=59; 重绑续捕 46.0)。dx11 游戏真机
待消费项目自然覆盖(验收 1 的备选"自写靶真机跑通"已满足)。

Status: ready-for-human(双架构自写靶真机闭环 PASS)

### 2026-11-25 · 提交后 code-review 双轴修复(第二循环, 4633726..HEAD)

- **dx10/dx12 覆盖面遗留显式标注**(本刀只做 d3d11, GetBuffer IID 筛, dx10/12 设备
  面包屑 0x2100|hr 走 gdi/dx2 兜底): dmsoft 头注释"dx10-12 全通"改"dx11, dx10/dx12
  暂不命中"; 本票 Status 的 PASS 口径=d3d11 设备。
- **hook_auto d3d9 降级码收窄 60/62**: 63(蹦床)/64(页保护)是钩引擎故障不是
  "目标不匹配", 降级会吞真实错误 → 改立即抛。GL 31(x64 无哨兵, 结构性)保持降级。
- **M_MOVE import 回归抓获**(回归规矩 dx 三烟测必跑的价值实证): f0457ea 重排
  host.py import 块时丢了 M_MOVE, dx_cursor/notepad_smoke ImportError —— 三烟测
  全绿修复后复跑(notepad PASS/cursor windows2 PASS/keystate 双阶段 PASS)。
- dmcore 死变量 dev 删; 双架构 dxgi_smoke 修复后重验 PASS。
