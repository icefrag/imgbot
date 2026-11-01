/* dmcore.c — imgbot dx 注入件(阶段1图色 + 阶段2键鼠)
 *
 * 机制: 宿主按 "Local\imgbotdx_{pid}" 建好共享内存后注入本 DLL; DxInit 起 worker
 * 线程挂共享内存, 之后纯轮询收发命令(心跳/cmd_seq/ack_seq), 不用命名事件。
 * 图色: CMD_HOOK_GL 对 SwapBuffers 做 hotpatch(哨兵校验+蹦床), 换帧前临时
 * wgl 上下文劫持回读后台缓冲到共享内存。
 * 键鼠: CMD_KEY/CMD_MOUSE 按大漠 0x837/0x838 消息配方合成完整消息链
 * (NCHITTEST 前戏全操作+SETCURSOR 仅移动, 扫描码/扩展键/SYSKEY/滚轮屏幕坐标,
 * 焦点路由 GetGUIThreadInfo; 键=RIT 同步+SendMessageTimeout+手动 TranslateMessage
 * 补 WM_CHAR; 鼠=PostMessage 进真队列 —— 2026-11-01 二次反编译定案, 队列交付
 * 才能让游戏泵/GetMessagePos 看到事件, 直呼 wndproc 是大漠的变体不是默认)。
 * 键态: CMD_KEYSTATE 钩 user32!GetKeyState/GetAsyncKeyState(虚拟状态喂给
 * 轮询型输入游戏 —— 大漠键盘死案解法, 2026-10-29 反编译 7.2319 组件确认)。
 * 光标: CMD_MOUSE_LOCK 钩 user32!GetCursorPos 返回虚拟光标(大漠
 * dx.mouse.position.lock.api —— "能移动不能点击"的游戏在点击里读真光标)。
 * 全家桶: CMD_MOUSE_PACK 一键武装 dx 鼠标辅助钩组团(键态+光标锁之上补
 * GetCapture 虚拟化/GetCursorInfo 改写/ClipCursor 吞噬/取消息层位置钉死 ——
 * 大漠"常用 dx 鼠标属性"7 属性预设的潜影对应物, 反编译 7.2319 组件对齐,
 * 2026-11-01 华夏后台点击攻坚落地)。
 * 协议/布局见 host.py 头注释, 两端必须同步改。
 */
#include <windows.h>
#include "obfstrings.gen.h"   /* 构建期生成(build.py): 遮蔽串密文+obf_init 解码 */

#define SHM_MAGIC   0x49425844u   /* "DXBI" */
#define FRAME_OFF   4096
#define FRAME_CAP   (32 * 1024 * 1024 - FRAME_OFF)

#define CMD_PING     1
#define CMD_BYE      2
#define CMD_HOOK_GL  3
#define CMD_BIND     4   /* arg0=hwnd: 键鼠命令的目标窗口 */
#define CMD_KEY      5   /* arg0=vk, arg1=mode(0=down 1=up 2=char, wParam=码元) */
#define CMD_MOUSE    6   /* arg0=(y<<16)|x 客户区, arg1=op(见 do_mouse) */
#define CMD_RAWINFO  7   /* 诊断: 目标进程 raw input 注册情况, 结果写 arg0/arg1 */
#define CMD_FAKE_ACTIVE 8  /* arg0=1/0: 子类化目标窗口, 吞失焦/失活消息(大漠 EnableFakeActive) */
#define CMD_HIDE     9   /* 从 PEB 模块链摘除本 DLL(大漠 dx.public.hide.dll 简版) */
#define CMD_HOOK_D3D9 10 /* d3d9 vtable 钩子(时机: 目标须在挂好后再调 Direct3DCreate9) */
#define CMD_KEYSTATE 11  /* 钩 user32!GetKeyState/GetAsyncKeyState(x86 热补丁) */
#define CMD_MOUSE_LOCK 12 /* 钩 user32!GetCursorPos 返回虚拟光标(大漠 dx.mouse.position.lock.api) */
#define CMD_MOUSE_PACK 13 /* dx 鼠标全家桶: 大漠"常用 dx 鼠标属性"7 属性预设一键武装 */

#define KEY_DOWN 0
#define KEY_UP   1
#define KEY_CHAR 2

#define M_MOVE     0
#define M_LDOWN    1
#define M_LUP      2
#define M_RDOWN    3
#define M_RUP      4
#define M_MDOWN    5
#define M_MUP      6
#define M_DBLCLK    7    /* WM_LBUTTONDBLCLK(第二击双击语义) */
#define M_WHEEL_UP   9
#define M_WHEEL_DOWN 10

#define SMTO_FLAGS (SMTO_ABORTIFHUNG | SMTO_BLOCK)

#define GL_VIEWPORT     0x0BA2
#define GL_RGB          0x1907
#define GL_BGR          0x80E0
#define GL_BGRA         0x80E1
#define GL_UNSIGNED_BYTE 0x1401

/* 帧格式: 1=BGRA8 行序自底向上(Python 侧翻), 2=RGB8 自底向上 */
typedef struct {
    DWORD magic, version, state, heartbeat;
    DWORD cmd, cmd_seq, ack_seq, ack_result;
    DWORD frame_seq, frame_w, frame_h, frame_fmt;
    DWORD swap_calls, gl_err, vp_w, vp_h;      /* 诊断: 钩子被调数/最后GL错误/最后视口 */
    DWORD arg0, arg1;                          /* 命令参数(v2) */
    DWORD msg_filtered;                        /* 假激活吞掉的消息数(v3) */
    DWORD crash_code, crash_addr, self_base;   /* VEH 首例(参考) */
    DWORD ring[8];                             /* VEH 环形: 最近4例(code,addr) */
} Header;

static HMODULE g_self;
static HANDLE  g_map;
static HANDLE  g_mutex;
static Header *g_hdr;
static volatile LONG g_stopping = 0;
static volatile LONG g_detached = 1;   /* BYE 后 our_present 只转发不捕获; 重绑清零续捕 */
static HWND    g_hwnd;                 /* 键鼠目标窗口(CMD_BIND) */
static DWORD   g_selfBase;              /* 本 DLL 基址(崩溃定位用) */
static DWORD   g_buttons;              /* 当前按住状态(MK_ 位) */

static BOOL (WINAPI *g_realSwap)(HDC);
static void (APIENTRY *p_glReadPixels)(int,int,int,int,unsigned,unsigned,void*);
static void (APIENTRY *p_glGetIntegerv)(unsigned,int*);
static unsigned (APIENTRY *p_glGetError)(void);
static HGLRC (WINAPI *p_wglCreateContext)(HDC);
static BOOL (WINAPI *p_wglMakeCurrent)(HDC, HGLRC);
static HGLRC (WINAPI *p_wglGetCurrentContext)(void);
static HDC (WINAPI *p_wglGetCurrentDC)(void);
static HGLRC g_ourCtx;                   /* 劫持用临时上下文(每 DC 一个, 这里缓存一个) */
static HDC   g_ourCtxHdc;

/* ---- 换帧钩子: 调真 SwapBuffers 之前回读后台缓冲 ----
 * 提交线程未必持有 GL 上下文(雷电渲染/提交分线程), 临时把自建上下文挂到该 DC,
 * 读回后台缓冲, 再还原原上下文(通常为 NULL)。 */
static BOOL WINAPI our_swap(HDC hdc)
{
    if (g_hdr && g_hdr->state == 1 && !g_stopping && p_glReadPixels
        && p_wglCreateContext && p_wglMakeCurrent) {
        InterlockedIncrement((volatile LONG *)&g_hdr->swap_calls);
        if (!g_ourCtx || g_ourCtxHdc != hdc) {
            if (g_ourCtx && p_wglGetCurrentContext && p_wglGetCurrentContext() == g_ourCtx)
                p_wglMakeCurrent(NULL, NULL);
            /* 旧 DC 的上下文不销毁: 泄漏一个可接受 */
            g_ourCtx = p_wglCreateContext(hdc);
            g_ourCtxHdc = hdc;
            if (!g_ourCtx) g_hdr->gl_err = 2;   /* DC 无像素格式/驱动拒绝 */
        }
        if (g_ourCtx) {
            HGLRC prevCtx = p_wglGetCurrentContext ? p_wglGetCurrentContext() : NULL;
            HDC prevDc = p_wglGetCurrentDC ? p_wglGetCurrentDC() : NULL;
            if (p_wglMakeCurrent(hdc, g_ourCtx)) {
                int vp[4] = {0, 0, 0, 0};
                p_glGetIntegerv(GL_VIEWPORT, vp);
                int w = vp[2], h = vp[3];
                g_hdr->vp_w = (DWORD)w;
                g_hdr->vp_h = (DWORD)h;
                if (w > 0 && h > 0 && (DWORD)(w * h * 4) <= FRAME_CAP) {
                    BYTE *dst = (BYTE *)g_hdr + FRAME_OFF;
                    p_glReadPixels(vp[0], vp[1], w, h, GL_BGRA, GL_UNSIGNED_BYTE, dst);
                    DWORD fmt = 1, err = 0;
                    if (p_glGetError) {
                        err = p_glGetError();
                        if (err != 0) {              /* BGRA 不支持退 RGB */
                            p_glReadPixels(vp[0], vp[1], w, h, GL_RGB, GL_UNSIGNED_BYTE, dst);
                            fmt = 2;
                        }
                    }
                    g_hdr->gl_err = err;
                    g_hdr->frame_w = (DWORD)w;
                    g_hdr->frame_h = (DWORD)h;
                    g_hdr->frame_fmt = fmt;
                    InterlockedIncrement((volatile LONG *)&g_hdr->frame_seq);
                }
                p_wglMakeCurrent(prevDc, prevCtx);  /* 还原(可能都是 NULL) */
            }
        }
    }
    return g_realSwap(hdc);
}

/* ---- hotpatch(x86): SwapBuffers 前导 mov edi,edi/push ebp/mov ebp,esp 无相对寻址,
 * ---- 抄前5字节进蹦床即可重定向, 不需要指令引擎。所有调用路径(静态/动态)都汇到
 * ---- gdi32!SwapBuffers / opengl32!wglSwapBuffers 导出函数本体, 挂本体即全覆盖。 */
static BYTE *g_swapFunc;                 /* 被 hook 的函数地址 */
static BYTE *g_tramp;                    /* 蹦床: [原前5字节][jmp func+5] */
static BYTE  g_orig5[5];

/* 公共热补丁: 哨兵校验 + 蹦床 + 首条改 jmp detour; 0=成功 31=无哨兵 32=蹦床分配 33=页保护 */
static int hotpatch(BYTE *func, void *detour, BYTE **trampOut, BYTE *orig5)
{
    static const BYTE expect[5] = {0x8B, 0xFF, 0x55, 0x8B, 0xEC};
    /* 两种可搬前导(x86 user32 实测): ① 标准哨兵 ② a1 [abs32](mov eax,moffs)
       —— GetCapture 前导, 整 5 字节无相对寻址, 同样可整搬蹦床 */
    if (memcmp(func, expect, 5) != 0 && func[0] != 0xA1) return 31;
    BYTE *tramp = (BYTE *)VirtualAlloc(NULL, 16, MEM_COMMIT | MEM_RESERVE,
                                       PAGE_EXECUTE_READWRITE);
    if (!tramp) return 32;
    memcpy(tramp, func, 5);
    tramp[5] = 0xE9;
    INT32 rel = (INT32)((func + 5) - (tramp + 10));
    memcpy(tramp + 6, &rel, 4);
    FlushInstructionCache(GetCurrentProcess(), tramp, 10);
    DWORD oldProt;
    if (!VirtualProtect(func, 5, PAGE_EXECUTE_READWRITE, &oldProt)) {
        VirtualFree(tramp, 0, MEM_RELEASE);
        return 33;
    }
    memcpy(orig5, func, 5);
    INT32 jrel = (INT32)((BYTE *)detour - (func + 5));
    func[0] = 0xE9;
    memcpy(func + 1, &jrel, 4);
    VirtualProtect(func, 5, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), func, 5);
    *trampOut = tramp;
    return 0;
}

static int hook_gl(void)
{
    if (g_tramp) return 0;                           /* 已挂 */
    HMODULE gl = GetModuleHandleA(OBF_OPENGL32);
    if (!gl) return 20;                              /* 目标未用 GL */
    p_glReadPixels   = (void *)GetProcAddress(gl, OBF_GLREADPIXELS);
    p_glGetIntegerv  = (void *)GetProcAddress(gl, OBF_GLGETINTEGERV);
    p_glGetError     = (void *)GetProcAddress(gl, OBF_GLGETERROR);
    p_wglCreateContext = (void *)GetProcAddress(gl, OBF_WGLCREATECONTEXT);
    p_wglMakeCurrent = (void *)GetProcAddress(gl, OBF_WGLMAKECURRENT);
    p_wglGetCurrentContext = (void *)GetProcAddress(gl, OBF_WGLGETCURRENTCONTEXT);
    p_wglGetCurrentDC = (void *)GetProcAddress(gl, OBF_WGLGETCURRENTDC);
    if (!p_glReadPixels || !p_glGetIntegerv) return 21;

    BYTE *func = NULL;
    HMODULE gdi = GetModuleHandleA(OBF_GDI32);
    if (gdi) {
        func = (BYTE *)GetProcAddress(gdi, OBF_SWAPBUFFERS);
        /* Win10 上 gdi32!SwapBuffers 是 ff 25 (jmp [imm32]) 转发跳, 解引用拿真身 */
        if (func && func[0] == 0xFF && func[1] == 0x25) {
            BYTE **slot = *(BYTE ***)(func + 2);      /* x86 操作数 = 指针的绝对地址 */
            if (slot && *slot) func = *slot;          /* -> gdi32full!SwapBuffers */
        }
    }
    if (!func) func = (BYTE *)GetProcAddress(gl, OBF_WGLSWAPBUFFERS);
    if (!func) return 22;
    int rc = hotpatch(func, (void *)our_swap, &g_tramp, g_orig5);
    if (rc) { g_tramp = NULL; return rc; }

    g_swapFunc = func;
    g_realSwap = (BOOL (WINAPI *)(HDC))g_tramp;
    return 0;
}

static void unhook_gl(void)
{
    if (!g_tramp) return;
    InterlockedExchange(&g_stopping, 1);
    Sleep(60);                                        /* 让渲染线程退出 our_swap */
    DWORD oldProt;
    VirtualProtect(g_swapFunc, 5, PAGE_EXECUTE_READWRITE, &oldProt);
    memcpy(g_swapFunc, g_orig5, 5);
    VirtualProtect(g_swapFunc, 5, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), g_swapFunc, 5);
    VirtualFree(g_tramp, 0, MEM_RELEASE);
    g_tramp = NULL; g_swapFunc = NULL; g_realSwap = NULL;
    InterlockedExchange(&g_stopping, 0);
}

/* ---- 键态钩子(CMD_KEYSTATE, 大漠键盘死案解法): 消息注入对轮询型输入
 * ---- (GetAsyncKeyState/RawInput/DirectInput) 天然无效 —— SendMessageTimeout
 * ---- 直呼 wndproc 不进队列, 线程键状态表/异步键状态都不更新。大漠 7.2319
 * ---- 组件的答案(反编译确认): 内联钩住 user32!GetKeyState/GetAsyncKeyState,
 * ---- 命中"虚拟按住"的键返回 0x8000|按下未读(读后清, 精确异步语义),
 * ---- 否则透传真值。DirectInput 游戏还需 dinput8!GetDeviceState 钩子(未实现)。 ---- */
/* 虚拟键鼠状态: 键鼠命令落命令时同步维护, 键态钩子读它喂给轮询型输入游戏。
 * 语义同大漠: 按住期返回 0x8000|按下未读(读后清), 松开后放弃虚拟值透真值。 */
static volatile BYTE g_vkDown[256], g_vkLatch[256];   /* 键盘: 按/按下未读 */
static volatile BYTE g_mbDown[3], g_mbLatch[3];       /* 鼠标 L/R/M */
static volatile LONG g_virtAlt;                       /* 虚拟 Alt 按住(SYSKEY 判据) */

static SHORT (WINAPI *g_realGetKeyState)(int);
static SHORT (WINAPI *g_realGetAsyncKeyState)(int);

static SHORT WINAPI our_getasynckeystate(int vk)
{
    int mbi = -1;
    if (vk == VK_LBUTTON) mbi = 0;
    else if (vk == VK_RBUTTON) mbi = 1;
    else if (vk == VK_MBUTTON) mbi = 2;
    if (mbi >= 0) {
        if (g_mbDown[mbi]) {
            BYTE l = g_mbLatch[mbi];
            g_mbLatch[mbi] = 0;
            return (SHORT)(0x8000u | l);
        }
        return g_realGetAsyncKeyState(vk);
    }
    if (vk > 0 && vk < 256 && vk != VK_CANCEL && g_vkDown[vk]) {
        BYTE l = g_vkLatch[vk];
        g_vkLatch[vk] = 0;
        return (SHORT)(0x8000u | l);
    }
    return g_realGetAsyncKeyState(vk);
}

static SHORT WINAPI our_getkeystate(int vk)
{
    int mbi = -1;
    if (vk == VK_LBUTTON) mbi = 0;
    else if (vk == VK_RBUTTON) mbi = 1;
    else if (vk == VK_MBUTTON) mbi = 2;
    if (mbi >= 0 && g_mbDown[mbi]) return (SHORT)0x8000u;
    if (vk > 0 && vk < 256 && g_vkDown[vk]) return (SHORT)0x8000u;
    return g_realGetKeyState(vk);
}

static BYTE *g_ksFunc[2];                  /* 被钩函数地址(还原用) */
static BYTE  g_ksOrig[2][5];
static BYTE *g_ksTramp[2];

static void restore_hotpatch(BYTE *func, BYTE *orig5, BYTE *tramp)
{
    Sleep(30);                               /* 等在途调用走出蹦床 */
    DWORD oldProt;
    VirtualProtect(func, 5, PAGE_EXECUTE_READWRITE, &oldProt);
    memcpy(func, orig5, 5);
    VirtualProtect(func, 5, oldProt, &oldProt);
    FlushInstructionCache(GetCurrentProcess(), func, 5);
    VirtualFree(tramp, 0, MEM_RELEASE);
}

/* ---- 虚拟光标钩(CMD_MOUSE_LOCK, 大漠 dx.mouse.position.lock.api): 游戏在
 * ---- 点击处理里调 GetCursorPos 取"真实光标"位置(在别处) → 点击落空, 表现为
 * ---- "能移动不能点击"。钩住后返回最近一次鼠标命令的虚拟位置(换算屏幕坐标)。
 * ---- 虚拟位由 do_mouse 每次落命令时刷新; BIND 时初始化为客户区中心。 ---- */
static volatile LONG g_mlock;                 /* 锁开关 */
static volatile LONG g_vx, g_vy;              /* 虚拟光标(客户区坐标) */
static BOOL (WINAPI *g_realGetCursorPos)(LPPOINT);
static BYTE *g_gpFunc, *g_gpTramp;
static BYTE  g_gpOrig[5];

static BOOL WINAPI our_getcursorpos(LPPOINT pt)
{
    if (g_mlock && g_hwnd && IsWindow(g_hwnd)) {
        POINT p = { (LONG)g_vx, (LONG)g_vy };
        ClientToScreen(g_hwnd, &p);
        if (pt) *pt = p;
        return TRUE;
    }
    return g_realGetCursorPos(pt);
}

static int hook_mouse_lock(void)
{
    if (g_gpTramp) { g_mlock = 1; return 0; }        /* 已挂, 只开闸 */
    HMODULE u = GetModuleHandleA(OBF_USER32);
    if (!u) return 80;
    BYTE *f = (BYTE *)GetProcAddress(u, OBF_GETCURSORPOS);
    if (!f) return 81;
    int rc = hotpatch(f, (void *)our_getcursorpos, &g_gpTramp, g_gpOrig);
    if (rc) { g_gpTramp = NULL; return 82 + (rc - 31); }
    g_gpFunc = f;
    g_realGetCursorPos = (BOOL (WINAPI *)(LPPOINT))g_gpTramp;
    g_mlock = 1;
    return 0;
}

static void unhook_mouse_lock(void)
{
    g_mlock = 0;
    if (!g_gpTramp) return;
    restore_hotpatch(g_gpFunc, g_gpOrig, g_gpTramp);
    g_gpFunc = NULL; g_gpTramp = NULL;
    g_realGetCursorPos = NULL;
}

static int hook_keystate(void)
{
    if (g_ksTramp[0]) return 0;                       /* 已挂 */
    HMODULE u = GetModuleHandleA(OBF_USER32);
    if (!u) return 70;
    BYTE *fk = (BYTE *)GetProcAddress(u, OBF_GETKEYSTATE);
    BYTE *fa = (BYTE *)GetProcAddress(u, OBF_GETASYNCKEYSTATE);
    if (!fk || !fa) return 71;
    int rc = hotpatch(fk, (void *)our_getkeystate, &g_ksTramp[0], g_ksOrig[0]);
    if (rc) return 72 + (rc - 31);                    /* 72 无哨兵(x64) 73 蹦床 74 保护 */
    g_ksFunc[0] = fk;
    rc = hotpatch(fa, (void *)our_getasynckeystate, &g_ksTramp[1], g_ksOrig[1]);
    if (rc) {                                         /* 第二个失败回滚第一个 */
        restore_hotpatch(g_ksFunc[0], g_ksOrig[0], g_ksTramp[0]);
        g_ksFunc[0] = NULL; g_ksTramp[0] = NULL;
        return 75 + (rc - 31);
    }
    g_ksFunc[1] = fa;
    g_realGetKeyState = (SHORT (WINAPI *)(int))g_ksTramp[0];
    g_realGetAsyncKeyState = (SHORT (WINAPI *)(int))g_ksTramp[1];
    return 0;
}

static void unhook_keystate(void)
{
    if (!g_ksTramp[0]) return;
    for (int i = 0; i < 2; i++) {
        if (!g_ksTramp[i]) continue;
        restore_hotpatch(g_ksFunc[i], g_ksOrig[i], g_ksTramp[i]);
        g_ksFunc[i] = NULL; g_ksTramp[i] = NULL;
    }
    g_realGetKeyState = NULL;
    g_realGetAsyncKeyState = NULL;
    ZeroMemory((void *)g_vkDown, sizeof(g_vkDown));
    ZeroMemory((void *)g_vkLatch, sizeof(g_vkLatch));
    ZeroMemory((void *)g_mbDown, sizeof(g_mbDown));
    ZeroMemory((void *)g_mbLatch, sizeof(g_mbLatch));
}

/* ---- 键鼠消息配方(大漠 0x837/0x838 移植): 在目标进程内、从 worker 线程
 * ---- SendMessageTimeout 到目标窗口 —— 消息过目标自己的消息循环, TranslateMessage
 * ---- 凭扫描码生成 WM_CHAR, 与真人键入同路。 ---- */

/* 焦点路由: 大漠命令在 UI 线程执行, GetFocus() 拿真实焦点子窗口(编辑框/渲染子窗口),
 * 无焦点时 SetFocus(绑定窗口) 补。我们命令在 worker 线程, GetFocus 恒 NULL,
 * 等价只读替代 = GetGUIThreadInfo(绑定窗口线程).hwndFocus(窗口必须仍属于该线程);
 * SetFocus 补焦跨线程无效, 假激活通道(CMD_FAKE_ACTIVE)补这块。 */
/* 直接调用会进 PE 导入表(名字裸露 —— 这俩恰是"键态/焦点探测"指纹 API),
   与遮蔽策略冲突: 改动态解析缓存(DxInit 时取一次) */
static SHORT (WINAPI *g_pGetAsyncKeyState)(int);
static BOOL  (WINAPI *g_pGetGUIThreadInfo)(DWORD, void *);

static HWND focus_or_bound(void)
{
    DWORD tid = GetWindowThreadProcessId(g_hwnd, NULL);
    GUITHREADINFO gi;
    ZeroMemory(&gi, sizeof(gi));
    gi.cbSize = sizeof(gi);
    if (g_pGetGUIThreadInfo && g_pGetGUIThreadInfo(tid, &gi) && gi.hwndFocus
        && GetWindowThreadProcessId(gi.hwndFocus, NULL) == tid
        && IsWindow(gi.hwndFocus))
        return gi.hwndFocus;
    return g_hwnd;
}

/* RIT 同步(大漠 7.2319 反编译确认): 目标注册过 raw input 时, 键鼠命令送达前向
 * raw input 目标窗口送 0xFF/魔数消息 —— SendMessageTimeout 等该线程排空在途
 * WM_INPUT, 保证真实输入事件先于合成输入落地(键 0x...78 / 鼠 0x...79 尾号不同)。
 * BIND 时解析一次缓存, 窗口没了就跳过(同步是保序优化, 不是硬前置)。 */
static HWND g_ritHwnd;

static HWND find_rit_hwnd(void)
{
    UINT n = 0;
    GetRegisteredRawInputDevices(NULL, &n, sizeof(RAWINPUTDEVICE));
    if (!n) return NULL;
    RAWINPUTDEVICE dev[8];
    UINT m = n < 8 ? n : 8;
    if (!GetRegisteredRawInputDevices(dev, &m, sizeof(RAWINPUTDEVICE))) return NULL;
    for (UINT i = 0; i < m; i++)
        if (dev[i].hwndTarget) return dev[i].hwndTarget;
    return NULL;
}

static void rit_sync(DWORD magic)
{
    if (g_ritHwnd && IsWindow(g_ritHwnd)) {
        DWORD_PTR res;
        SendMessageTimeoutW(g_ritHwnd, 0xFF, 0, magic, SMTO_FLAGS, 100, &res);
    }
}

static BOOL is_extended_vk(DWORD vk)
{
    switch (vk) {
    case VK_INSERT: case VK_DELETE: case VK_HOME: case VK_END:
    case VK_PRIOR: case VK_NEXT: case VK_LEFT: case VK_RIGHT:
    case VK_UP: case VK_DOWN: case VK_NUMLOCK: case VK_RCONTROL:
    case VK_RMENU: case VK_DIVIDE: case VK_SNAPSHOT:
        return TRUE;
    default:
        return FALSE;
    }
}

static int do_key(DWORD vk, DWORD mode)
{
    if (!g_hwnd || !IsWindow(g_hwnd)) return 40;
    UINT msg;
    LPARAM lp;
    DWORD_PTR res;
    if (mode == KEY_CHAR) {
        /* 中文/文本: 直接 WM_CHAR, wParam=UTF-16 码元(vk 参数位复用);
           修饰键归一化不适用于此路径(码元可能是任意字符) */
        DWORD scanC = MapVirtualKeyA(vk, MAPVK_VK_TO_VSC);
        SendMessageTimeoutW(focus_or_bound(), WM_CHAR, vk, (scanC << 16) | 1,
                            SMTO_FLAGS, 2000, &res);
        return 0;
    }
    /* 修饰键 VK 归一化(大漠宿主 KeyPress 序列同款): L/R → 通用码,
       扫描码/扩展位随通用码走, 别让 0xA3 这类裸码拿不到扫描码 */
    if (vk == VK_LSHIFT || vk == VK_RSHIFT) vk = VK_SHIFT;
    else if (vk == VK_LCONTROL || vk == VK_RCONTROL) vk = VK_CONTROL;
    else if (vk == VK_LMENU || vk == VK_RMENU) vk = VK_MENU;
    DWORD scan = MapVirtualKeyA(vk, MAPVK_VK_TO_VSC);
    /* 虚拟 Alt 追踪(大漠宿主 ctx+100 同款): 脚本按住 Alt 期间后续键转
       WM_SYSKEYDOWN/UP + 上下文位 —— 后台合成时真实 Alt 并没按 */
    BOOL sysKey = (vk == VK_F10) || g_virtAlt
                  || (g_pGetAsyncKeyState
                      && (g_pGetAsyncKeyState(VK_MENU) & 0x8000) != 0);
    if (mode == KEY_DOWN) {
        msg = sysKey ? WM_SYSKEYDOWN : WM_KEYDOWN;
        lp = (LPARAM)(scan << 16) | 1;
    } else {
        msg = sysKey ? WM_SYSKEYUP : WM_KEYUP;
        lp = (LPARAM)(scan << 16) | 0xC0000001;
    }
    if (is_extended_vk(vk)) lp |= 0x1000000;       /* 扩展键标志 */
    if (sysKey) lp |= 0x20000000;                  /* 上下文码: Alt 按住 */
    HWND target = focus_or_bound();
    if (vk < 256) {                                /* 虚拟状态先落, 钩子立即可见 */
        g_vkDown[vk] = (mode == KEY_DOWN);
        if (mode == KEY_DOWN) g_vkLatch[vk] = 1;
        if (vk == VK_MENU) g_virtAlt = (mode == KEY_DOWN);
    }
    rit_sync(0x12345678);                          /* 大漠: 先等 raw input 线程排空 */
    SendMessageTimeoutW(target, msg, vk, lp, SMTO_FLAGS, 2000, &res);
    /* 大漠直呼模式同款收尾: SendMessageTimeout 直呼 wndproc 不进队列, 应用循环的
       TranslateMessage 永远看不到这条 KEYDOWN —— 自己补一次, WM_CHAR/WM_SYSCHAR
       才会入队(聊天框/编辑控件可被打字)。真值无重复风险(队列里没有 KEYDOWN)。 */
    MSG m;
    ZeroMemory(&m, sizeof(m));
    m.hwnd = target; m.message = msg; m.wParam = vk; m.lParam = lp;
    TranslateMessage(&m);
    return 0;
}

static int do_mouse(DWORD arg0, DWORD op)
{
    if (!g_hwnd || !IsWindow(g_hwnd)) return 40;
    short x = (short)(arg0 & 0xFFFF), y = (short)(arg0 >> 16);
    g_vx = x; g_vy = y;                        /* 虚拟光标随命令刷新(锁开着时喂钩子) */
    LPARAM xy = MAKELPARAM(x, y);
    POINT pt = { x, y };
    ClientToScreen(g_hwnd, &pt);
    DWORD_PTR res;
    UINT msg = 0;
    WPARAM wp = 0;
    LPARAM lp = xy;

    switch (op) {
    case M_MOVE:
        msg = WM_MOUSEMOVE; wp = g_buttons;
        break;
    case M_LDOWN: g_buttons |= MK_LBUTTON; msg = WM_LBUTTONDOWN; wp = g_buttons;
                  g_mbDown[0] = 1; g_mbLatch[0] = 1; break;
    case M_LUP:   g_buttons &= ~MK_LBUTTON; msg = WM_LBUTTONUP; wp = g_buttons;
                  g_mbDown[0] = 0; break;
    case M_RDOWN: g_buttons |= MK_RBUTTON; msg = WM_RBUTTONDOWN; wp = g_buttons;
                  g_mbDown[1] = 1; g_mbLatch[1] = 1; break;
    case M_RUP:   g_buttons &= ~MK_RBUTTON; msg = WM_RBUTTONUP; wp = g_buttons;
                  g_mbDown[1] = 0; break;
    case M_MDOWN: g_buttons |= MK_MBUTTON; msg = WM_MBUTTONDOWN; wp = g_buttons;
                  g_mbDown[2] = 1; g_mbLatch[2] = 1; break;
    case M_MUP:   g_buttons &= ~MK_MBUTTON; msg = WM_MBUTTONUP; wp = g_buttons;
                  g_mbDown[2] = 0; break;
    case M_DBLCLK: msg = WM_LBUTTONDBLCLK; wp = g_buttons | MK_LBUTTON; break;
    case M_WHEEL_UP:
    case M_WHEEL_DOWN:
        msg = WM_MOUSEWHEEL;
        wp = MAKEWPARAM(g_buttons, op == M_WHEEL_UP ? 120 : (WORD)-120);
        lp = MAKELPARAM(pt.x, pt.y);               /* 滚轮 lParam = 屏幕坐标(大漠同款) */
        break;
    default:
        return 41;
    }
    /* 前戏(大漠 0x838 对齐): 每个鼠标操作都先 NCHITTEST(点击也做, 位置感知型
       游戏靠它更新悬停态), SETCURSOR 仅移动时做。发给绑定窗口。 */
    SendMessageTimeoutW(g_hwnd, WM_NCHITTEST, 0,
                        MAKELPARAM(pt.x, pt.y), SMTO_FLAGS, 2000, &res);
    if (op == M_MOVE)
        SendMessageTimeoutW(g_hwnd, WM_SETCURSOR, (WPARAM)g_hwnd,
                            MAKELPARAM(1 /*HTCLIENT*/, WM_MOUSEMOVE),
                            SMTO_FLAGS, 2000, &res);
    /* 主交付 = PostMessage 进真队列(2026-11-01 二次反编译 0x838 定案: 大漠默认
       路径是 PostMessageA, SendMessageTimeout 只是配置变体)。队列交付让游戏
       自己的 GetMessage 泵取消息 —— MSG.pt/GetMessagePos/队列时序全由 win32k
       自然更新, 直呼 wndproc 永远做不到, 华夏"偶发"的根因面。 */
    if (!PostMessageW(focus_or_bound(), msg, wp, lp))
        return 42;                                     /* 投递失败(无效窗口) */
    return 0;
}

/* ---- d3d9(Dummy 设备路线, OBS/kiero 同款): 自建一次性 D3D9 设备拿到设备类
 * ---- vtable(d3d9.dll .data, 全类共享), 只补 Present(17) 一个槽。
 * ---- 不代理对象/不碰 IDirect3D9 vtable —— 前两条路已被证死:
 * ----   类 vtable 的 CreateDevice 槽: 系统内部会调(崩在本 DLL+0xFBA4);
 * ----   COM 代理(this 替换蹦床): d3d9 内部身份校验打架(崩在未进蹦床前)。
 * ---- 索引(mingw d3d9.h 实数): Present=17 GetBackBuffer=18
 * ---- GetRenderTargetData=32 CreateOffscreenPlainSurface=36;
 * ---- Surface: GetDesc=12 LockRect=13 UnlockRect=14 Release=2。
 * ---- PRESENT_PARAMETERS x86 偏移: W@0 H@4 fmt@8 cnt@12 ms@16 msq@20 swap@24
 * ---- hwnd@28 windowed@32 ads@36 adsfmt@40 flags@44 rate@48 interval@52。 ---- */
#define D3D9_PRESENT        17
#define D3D9_GETBACKBUFFER  18
#define D3D9_GETRENDERTARGETDATA 32
#define D3D9_CREATEOFFSCREEN 36
#define SURF_GETDESC        12
#define SURF_LOCKRECT       13
#define SURF_UNLOCKRECT     14
#define SURF_RELEASE        2

typedef HRESULT (WINAPI *FnPresent)(void *, const RECT *, const RECT *, HWND,
                                    const void *, DWORD);
typedef struct { long Pitch; void *pBits; } LockedRect;          /* x86: INT+ptr */
typedef struct { DWORD fmt, type, usage, pool, msType, msQuality, w, h; } SurfDesc;

static FnPresent g_realPresent[3];               /* 原始 Present(按行为标志最多 3 个 vtable) */
static void   *g_devVt[3];                       /* 设备类 vtable 地址(还原用) */
static int     g_nVt;
static FnPresent g_realPresentShadow;            /* 探针: g_realPresent[0] 原值影子(被踩检测) */
static volatile LONG g_enterCount;               /* 探针: our_present 总进入次数(递归检测) */

/* 诊断区(shm 偏移 2048, DWORD 序号): [0..15]=VEH Eip+栈转储  [16]=崩TID [17]=Esp
 * [18]=Eip所在模块 [23]=enterCount [24..31]=Eip/Esp 区块指纹 [32]=蹦床
 * [48]=vt17 [49]=realPresent现值 [50]=影子原值 [51]=vt [52]=dev [54]=shm基址
 * [55]=present线程 [56]=sysmem像素 [57]=backbuffer [58]=sysSurf对象
 * [60..61]=真实栈界 [62]=回溯条数 [64+]=栈顶模块地址回溯 */
#define DIAG(n) (((DWORD *)((BYTE *)g_hdr + 2048))[n])
static void   *g_sysSurf;                        /* 系统内存回读面(按尺寸缓存) */
static DWORD   g_sysW, g_sysH;

static HRESULT WINAPI our_present(void *dev, const RECT *src, const RECT *dst,
                                  HWND wnd, const void *rgn, DWORD flags)
{
    /* 二分掩码(DIAG(100), 宿主写): bit0=GetBackBuffer bit1=GetDesc
       bit2=CreateOffscreen bit3=GRTD bit4=Lock+copy; 0=全开, -1=纯尾跳零副作用 */
    LONG entered = InterlockedIncrement(&g_enterCount);
    if (g_detached) {                   /* BYE 后闸住: g_hdr 视图已拆, 不碰, 只转发 */
        __attribute__((musttail))
        return g_realPresent[0](dev, src, dst, wnd, rgn, flags);
    }
    if (g_hdr) DIAG(59) = (DWORD)entered;
    DWORD mask = g_hdr ? DIAG(100) : 0;
    if (!mask) mask = 0x1F;
    if (mask != 0xFFFFFFFFu && g_hdr && g_hdr->state == 1 && !g_stopping) {
        InterlockedIncrement((volatile LONG *)&g_hdr->swap_calls);
        g_hdr->gl_err = 0x11;                           /* 进入捕获体 */
        void **vt = *(void ***)dev;                     /* 应用自己的设备(未动) */
        /* 帧入口快照: vt17 仍指 our_present? / g_realPresent[0] 对影子是否被踩 */
        DIAG(48) = (DWORD)(DWORD_PTR)vt[D3D9_PRESENT];
        DIAG(49) = (DWORD)(DWORD_PTR)g_realPresent[0];
        DIAG(50) = (DWORD)(DWORD_PTR)g_realPresentShadow;
        DIAG(51) = (DWORD)(DWORD_PTR)vt;
        DIAG(52) = (DWORD)(DWORD_PTR)dev;
        DIAG(54) = (DWORD)(DWORD_PTR)g_hdr;             /* shm 在本进程的基址 */
        DIAG(55) = GetCurrentThreadId();
        void *back = NULL;
        if (mask & 1)
            ((HRESULT (WINAPI *)(void *, UINT, UINT, DWORD, void **))
                vt[D3D9_GETBACKBUFFER])(dev, 0, 0, 0, &back);
        SurfDesc d;
        int ok = 0;
        if (back && (mask & 2)) {
            g_hdr->gl_err = 0x12;                       /* GetBackBuffer OK */
            ((HRESULT (WINAPI *)(void *, void *))
                (*(void ***)back)[SURF_GETDESC])(back, &d);
            ok = (d.fmt == 21 || d.fmt == 22) && d.w && d.h
                 && (DWORD)(d.w * d.h * 4) <= FRAME_CAP;
            if (!ok && d.w) g_hdr->gl_err = 7;          /* 非常见像素格式 */
        }
        if (ok && (mask & 4)
            && (!g_sysSurf || g_sysW != d.w || g_sysH != d.h)) {
            if (g_sysSurf)
                ((ULONG (WINAPI *)(void *))
                    (*(void ***)g_sysSurf)[SURF_RELEASE])(g_sysSurf);
            g_sysSurf = NULL;
            ((HRESULT (WINAPI *)(void *, UINT, UINT, DWORD, DWORD, void *, void *))
                vt[D3D9_CREATEOFFSCREEN])(dev, d.w, d.h, d.fmt,
                                          2 /*SYSTEMMEM*/, &g_sysSurf, NULL);
            g_sysW = d.w;
            g_sysH = d.h;
        }
        if (ok && g_sysSurf && (mask & 8)) {
            g_hdr->gl_err = g_sysSurf ? 0x13 : 4;       /* 系统内存面就绪/创建失败 */
            HRESULT hr = ((HRESULT (WINAPI *)(void *, void *, void *))
                vt[D3D9_GETRENDERTARGETDATA])(dev, back, g_sysSurf);
            if (hr >= 0) {
                g_hdr->gl_err = 0x14;                   /* GetRenderTargetData OK */
                if (mask & 16) {
                    LockedRect lr;
                    if (((HRESULT (WINAPI *)(void *, void *, const RECT *, DWORD))
                            (*(void ***)g_sysSurf)[SURF_LOCKRECT])(
                                g_sysSurf, &lr, NULL, 0) >= 0) {
                        BYTE *out = (BYTE *)g_hdr + FRAME_OFF;
                        BYTE *row = (BYTE *)lr.pBits + (d.h - 1) * lr.Pitch;
                        DIAG(56) = (DWORD)(DWORD_PTR)lr.pBits;   /* sysmem 面像素区 */
                        DIAG(57) = (DWORD)(DWORD_PTR)back;       /* 显存后台缓冲 */
                        DIAG(58) = (DWORD)(DWORD_PTR)g_sysSurf;  /* sysmem 面对象 */
                        for (DWORD y = 0; y < d.h; y++, row -= lr.Pitch)
                            memcpy(out + y * d.w * 4, row, d.w * 4);
                        ((HRESULT (WINAPI *)(void *))
                            (*(void ***)g_sysSurf)[SURF_UNLOCKRECT])(g_sysSurf);
                        g_hdr->frame_w = d.w;
                        g_hdr->frame_h = d.h;
                        g_hdr->frame_fmt = 1;
                        InterlockedIncrement((volatile LONG *)&g_hdr->frame_seq);
                    } else g_hdr->gl_err = 0x5000 | (DWORD)hr & 0xFFF;
                }
            } else g_hdr->gl_err = 0x6000 | ((DWORD)hr & 0xFFF);
        }
        if (back)
            ((ULONG (WINAPI *)(void *))(*(void ***)back)[SURF_RELEASE])(back);
    }
    /* 转发必须是真尾跳(musttail): Win10 d3d9!Present 检查调用来源, 栈上多一层
       本 DLL 的帧(call+返址)即崩(Eip 落栈页 DEP, 2026-10 会诊); 尾跳后栈布局与
       应用直调一致, Present 直接返回应用。捕获照常在跳之前完成。 */
    __attribute__((musttail))
    return g_realPresent[0](dev, src, dst, wnd, rgn, flags);
}

static HRESULT (WINAPI *g_realCreateDevice)(void *, UINT, DWORD, HWND, DWORD,
                                             void *, void **);
static void   *g_d3dVt;                          /* 应用 d3d9 对象的 vtable(一次性) */

static HRESULT WINAPI our_create_device(void *d3d, UINT adapter, DWORD type,
                                        HWND hwnd, DWORD behavior, void *pp,
                                        void **out)
{
    /* 一次性自拆: 应用首次 CreateDevice 走到这里, 立即还原槽位 ——
       d3d9 内部后续对 slot16 的调用只见原函数(模式二崩溃的根治) */
    if (g_d3dVt) {
        DWORD oldProt;
        VirtualProtect(&((void **)g_d3dVt)[16], sizeof(void *),
                       PAGE_READWRITE, &oldProt);
        ((void **)g_d3dVt)[16] = (void *)g_realCreateDevice;
        VirtualProtect(&((void **)g_d3dVt)[16], sizeof(void *), oldProt, &oldProt);
        FlushInstructionCache(GetCurrentProcess(), g_d3dVt, 0);
        g_d3dVt = NULL;
    }
    HRESULT hr = g_realCreateDevice(d3d, adapter, type, hwnd, behavior, pp, out);
    if (hr >= 0 && out && *out && g_nVt < 3) {
        void **vt = *(void ***)*out;                    /* 应用设备(堆私有 vtable) */
        DWORD oldProt;
        if (VirtualProtect(&vt[D3D9_PRESENT], sizeof(void *),
                           PAGE_READWRITE, &oldProt)) {
            g_realPresent[g_nVt] = (FnPresent)vt[D3D9_PRESENT];
            vt[D3D9_PRESENT] = (void *)our_present;
            g_devVt[g_nVt] = vt;
            if (g_nVt == 0) g_realPresentShadow = g_realPresent[0];
            g_nVt++;
            VirtualProtect(&vt[D3D9_PRESENT], sizeof(void *), oldProt, &oldProt);
            FlushInstructionCache(GetCurrentProcess(), vt, 0);
        }
    }
    return hr;
}

static BYTE *g_d3d9Func;
static BYTE *g_d3d9Tramp;
static BYTE  g_d3d9Orig5[5];

static void *WINAPI our_d3dcreate9(UINT sdk)
{
    void *d3d = ((void *(WINAPI *)(UINT))g_d3d9Tramp)(sdk);
    /* 应用刚拿到自己的 d3d9 对象(堆私有 vtable): 补它的 CreateDevice 槽,
       一次性自拆(our_create_device 首调即还原) —— 内部调用者永不见包装 */
    if (d3d && g_d3dVt == NULL) {
        /* 每个新 d3d9 对象都补 slot16(g_d3dVt 在 BYE 时复位): 重绑后游戏重建
           的对象也能被钩到; g_realCreateDevice 已有值就复用(同一直函数) */
        void **vt = *(void ***)d3d;
        DWORD oldProt;
        if (VirtualProtect(&vt[16], sizeof(void *), PAGE_READWRITE, &oldProt)) {
            g_realCreateDevice = (HRESULT (WINAPI *)(void *, UINT, DWORD, HWND,
                                                     DWORD, void *, void **))vt[16];
            vt[16] = (void *)our_create_device;
            g_d3dVt = vt;
            VirtualProtect(&vt[16], sizeof(void *), oldProt, &oldProt);
            FlushInstructionCache(GetCurrentProcess(), vt, 0);
        }
    }
    return d3d;
}

static int hook_d3d9(void)
{
    if (g_d3d9Tramp) {                                  /* 本会话已挂(重复调用): 清闸即恢复 */
        InterlockedExchange(&g_detached, 0);
        return 0;
    }
    HMODULE d3d = GetModuleHandleA(OBF_D3D9);
    if (!d3d) d3d = LoadLibraryA(OBF_D3D9);           /* 目标还没加载就先代载 */
    if (!d3d) return 60;
    BYTE *func = (BYTE *)GetProcAddress(d3d, OBF_DIRECT3DCREATE9);
    if (!func) return 61;
    int rc = hotpatch(func, (void *)our_d3dcreate9, &g_d3d9Tramp, g_d3d9Orig5);
    if (rc) { g_d3d9Tramp = NULL; return 62 + (rc - 31); }
    g_d3d9Func = func;
    DWORD *diag = (DWORD *)((BYTE *)g_hdr + 2048 + 128);
    diag[0] = (DWORD)(DWORD_PTR)g_d3d9Tramp;            /* 蹦床地址留档 */
    InterlockedExchange(&g_detached, 0);                /* 首装清闸, our_present 可捕获 */
    return 0;
}

static void unhook_d3d9(void)
{
    if (!g_d3d9Tramp && !g_nVt) return;
    InterlockedExchange(&g_detached, 1);               /* 先闸住 our_present */
    Sleep(60);                                         /* 等在途捕获出完(还要放 sysSurf) */
    if (g_sysSurf) {
        ((ULONG (WINAPI *)(void *))(*(void ***)g_sysSurf)[SURF_RELEASE])(g_sysSurf);
        g_sysSurf = NULL;
    }
    g_d3dVt = NULL;      /* 允许 our_d3dcreate9 给重绑后新建的 d3d9 对象补 slot16 */
    /* Direct3DCreate9 热补丁还原+复位(重绑时注入器复用驻留模块, hook_d3d9 重走全新
     * 挂载路径并清 detached); 设备 vtable 钩故意不还原 —— detached 闸着纯转发,
     * 在世设备靠它无缝续捕。g_nVt/g_devVt[] 保留作代数记账(g_nVt<3 上限)。 */
    if (g_d3d9Tramp) {
        DWORD oldProt;
        VirtualProtect(g_d3d9Func, 5, PAGE_EXECUTE_READWRITE, &oldProt);
        memcpy(g_d3d9Func, g_d3d9Orig5, 5);
        VirtualProtect(g_d3d9Func, 5, oldProt, &oldProt);
        FlushInstructionCache(GetCurrentProcess(), g_d3d9Func, 5);
        VirtualFree(g_d3d9Tramp, 0, MEM_RELEASE);
        g_d3d9Tramp = NULL; g_d3d9Func = NULL;
    }
}

/* ---- 假激活(大漠 EnableFakeActive / dx.public.active.*): 子类化目标窗口,
 * ---- 吞"失焦/失活"族消息并按需合成一次激活, 让后台窗口自认为有焦点。 ---- */
static WNDPROC g_origProc;
static HWND    g_subHwnd;

static LRESULT CALLBACK sub_proc(HWND h, UINT msg, WPARAM w, LPARAM l)
{
    /* 过滤计数器在共享内存里; BYE 还原子类后仍可能有在途消息, 空判防崩 */
    #define FILTER_MSG() do { if (g_hdr) \
        InterlockedIncrement((volatile LONG *)&g_hdr->msg_filtered); } while (0)
    switch (msg) {
    case WM_ACTIVATE:        /* 0x06: 低字 WA_INACTIVE=0 → 改写成 WA_ACTIVE */
        if (LOWORD(w) == 0) {
            FILTER_MSG();
            return 0;
        }
        break;
    case WM_KILLFOCUS:       /* 0x08: 直接吞 */
        FILTER_MSG();
        return 0;
    case WM_ACTIVATEAPP:     /* 0x1C: w=FALSE(失活) → 吞 */
        if (!w) {
            FILTER_MSG();
            return 0;
        }
        break;
    case WM_NCACTIVATE:      /* 0x86: 非客户区失活 → 谎称仍激活 */
        if (!w) {
            FILTER_MSG();
            w = TRUE;
        }
        break;
    default:
        break;
    }
    #undef FILTER_MSG
    return CallWindowProcW(g_origProc, h, msg, w, l);
}

/* ---- API 级激活锁(大漠 dx.public.active.api, 2026-11-01 华夏坐实其必要性):
 * ---- 消息级假激活只吞失焦消息, 救不了"点击时轮询 GetForegroundWindow/
 * ---- GetActiveWindow/GetFocus 系统态"的游戏(华夏世界画面实测: 真后台时
 * ---- 消息级假激活+光标锁+键态钩全家桶仍 0/5, 真前台秒通)。钩住三查询
 * ---- 返回绑定窗口 —— 与消息级假激活同开关(fake_active)双管齐下。 ---- */
static volatile LONG g_afApi;                 /* API 锁闸 */
static DWORD *g_fwSlot, *g_gaSlot, *g_gtiSlot;  /* 槽地址(FF25/FF15 thunk 指针) */
static DWORD   g_fwOrig, g_gaOrig, g_gtiOrig;   /* 原槽值(还原用) */

/* 这三个查询在 Win10 x86 user32 里是导入 thunk, 无热补丁哨兵:
 *   GetForegroundWindow: FF 25 [ptr]              jmp 槽
 *   GetActiveWindow:     6A 01 FF 15 [ptr] C3     push 1; call 槽
 *   GetFocus:            6A 00 FF 15 [ptr] C3     push 0; call 槽  ← 与上者共槽!
 * 挂法 = 改槽指针(改一个 DWORD, 不动代码); GA/GF 共槽, 用 push 实参区分语义。 */
static HWND WINAPI our_gfw(void)
{
    if (g_afApi && g_hwnd && IsWindow(g_hwnd)) return g_hwnd;
    return (HWND)(DWORD_PTR)((HWND (WINAPI *)(void))g_fwOrig)();
}
static DWORD WINAPI our_ga_gate(DWORD which)   /* which: 1=ActiveWindow 0=Focus */
{
    if (g_afApi && g_hwnd && IsWindow(g_hwnd) && which <= 1)
        return (DWORD)(DWORD_PTR)g_hwnd;
    return ((DWORD (WINAPI *)(DWORD))g_gaOrig)(which);
}

static BOOL WINAPI our_gti(DWORD tid, void *info)   /* GetGUIThreadInfo 输出改写 */
{
    BOOL r = ((BOOL (WINAPI *)(DWORD, void *))g_gtiOrig)(tid, info);
    if (g_afApi && r && info && g_hwnd && IsWindow(g_hwnd)) {
        DWORD *f = (DWORD *)info;   /* x86 GUITHREADINFO: cb flg Active Focus Capture */
        f[2] = (DWORD)(DWORD_PTR)g_hwnd;   /* hwndActive */
        f[3] = (DWORD)(DWORD_PTR)g_hwnd;   /* hwndFocus  */
        f[4] = (DWORD)(DWORD_PTR)g_hwnd;   /* hwndCapture */
    }
    return r;
}

static int patch_slot(DWORD *slot, DWORD value, DWORD *saved)
{
    DWORD oldProt;
    if (!slot) return 95;
    if (!VirtualProtect(slot, 4, PAGE_READWRITE, &oldProt)) return 96;
    *saved = *slot;
    *slot = value;
    VirtualProtect(slot, 4, oldProt, &oldProt);
    return 0;
}

static int hook_active_api(void)
{
    if (g_fwSlot) { g_afApi = 1; return 0; }          /* 已挂, 开闸 */
    HMODULE u = GetModuleHandleA(OBF_USER32);
    if (!u) return 90;
    BYTE *gfw = (BYTE *)GetProcAddress(u, OBF_GETFOREGROUNDWINDOW);
    BYTE *gaw = (BYTE *)GetProcAddress(u, OBF_GETACTIVEWINDOW);
    BYTE *gf  = (BYTE *)GetProcAddress(u, OBF_GETFOCUS);
    if (!gfw || !gaw || !gf) return 91;
    /* 形状校验: 看准了才下槽, 形状变了(系统升级)报码不硬来 */
    if (gfw[0] != 0xFF || gfw[1] != 0x25) return 93;
    if (gaw[0] != 0x6A || gaw[2] != 0xFF || gaw[3] != 0x15) return 93;
    if (gf[0]  != 0x6A || gf[2]  != 0xFF || gf[3]  != 0x15) return 93;
    DWORD *fwSlot = (DWORD *)(DWORD_PTR)*(DWORD *)(gfw + 2);  /* FF 25 [slot] */
    DWORD *gaSlot = (DWORD *)(DWORD_PTR)*(DWORD *)(gaw + 4);  /* FF 15 [slot] */
    if (!fwSlot || !gaSlot) return 94;
    int rc = patch_slot(fwSlot, (DWORD)(DWORD_PTR)our_gfw, &g_fwOrig);
    if (rc) return 100 + rc;
    g_fwSlot = fwSlot;
    rc = patch_slot(gaSlot, (DWORD)(DWORD_PTR)our_ga_gate, &g_gaOrig);
    if (rc) {                                         /* 第二槽失败回滚第一槽 */
        patch_slot(g_fwSlot, g_fwOrig, &g_fwOrig);
        g_fwSlot = NULL;
        return 101 + (rc - 100);
    }
    g_gaSlot = gaSlot;
    /* GetGUIThreadInfo(游戏常查它拿前台线程的 Active/Focus/Capture, 前三钩盖不住):
       同为 FF 25 槽 thunk, 输出改写 hwndActive/hwndFocus/hwndCapture */
    BYTE *gti = (BYTE *)GetProcAddress(u, OBF_GETGUIThreadINFO);
    if (gti && gti[0] == 0xFF && gti[1] == 0x25) {
        DWORD *gtiSlot = (DWORD *)(DWORD_PTR)*(DWORD *)(gti + 2);
        if (gtiSlot && patch_slot(gtiSlot, (DWORD)(DWORD_PTR)our_gti, &g_gtiOrig) == 0)
            g_gtiSlot = gtiSlot;      /* 形状不符/失败: 容忍, 前三钩仍在 */
    }
    g_afApi = 1;
    return 0;
}

static void unhook_active_api(void)
{
    g_afApi = 0;
    if (g_fwSlot) { patch_slot(g_fwSlot, g_fwOrig, &g_fwOrig); g_fwSlot = NULL; }
    if (g_gaSlot) { patch_slot(g_gaSlot, g_gaOrig, &g_gaOrig); g_gaSlot = NULL; }
    if (g_gtiSlot) { patch_slot(g_gtiSlot, g_gtiOrig, &g_gtiOrig); g_gtiSlot = NULL; }
}

static int fake_active(int enable)
{
    if (enable) {
        if (!g_hwnd || !IsWindow(g_hwnd)) return 40;
        if (g_subHwnd) return 0;                       /* 已开 */
        /* 保存原子类指针必须在窗口所属线程之外也可行(同进程内), 大漠同款做法 */
        g_origProc = (WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC,
                                               (LONG_PTR)sub_proc);
        if (!g_origProc) return (int)GetLastError();
        g_subHwnd = g_hwnd;
        /* API 级激活锁(GetForegroundWindow/GetActiveWindow/GetFocus 轮询免疫) */
        int rc2 = hook_active_api();
        if (rc2) { /* 失败不致命: 消息级仍生效。93=前导形状不符 —— x64 user32
                      没有 x86 槽 thunk 形状, 属平台不支持而非错误, 视同全开 */ }
        /* 合成一次"被激活+得焦", 把窗口先带进激活态 */
        SendMessageTimeoutW(g_hwnd, WM_ACTIVATE, MAKELPARAM(1, 0), 0,
                            SMTO_FLAGS, 2000, NULL);
        SendMessageTimeoutW(g_hwnd, WM_SETFOCUS, 0, 0, SMTO_FLAGS, 2000, NULL);
        return (rc2 == 0 || rc2 == 93) ? 1 : 100 + rc2;  /* 1=全开; 1xx=API锁挂(码-100) */
    }
    unhook_active_api();
    if (!g_subHwnd) return 0;
    /* 只在当前 wndproc 仍是 sub_proc 时还原(被人叠过类就不硬扒) */
    if ((WNDPROC)GetWindowLongPtrW(g_subHwnd, GWLP_WNDPROC) == sub_proc)
        SetWindowLongPtrW(g_subHwnd, GWLP_WNDPROC, (LONG_PTR)g_origProc);
    g_subHwnd = NULL;
    g_origProc = NULL;
    return 0;
}

/* ---- dx 鼠标全家桶(CMD_MOUSE_PACK): 大漠"常用 dx 鼠标属性"预设的潜影对应物。
 * ---- 背景(2026-11-01 华夏攻坚): 大漠 BindWindowEx 鼠标串
 * ---- position.lock.api|position.lock.message|clip.lock.api|input.lock.api|
 * ---- state.api|mouse.api|cursor 勾选即后台可点(用户实测, 无假激活); 我们此前的
 * ---- 消息配方(0x838)只覆盖 mouse.api 一件, 其余六件钩子缺位 → 后台点击偶发。
 * ---- 反编译 7.2319 组件确认的钩子面: 键态(state.api)+光标锁(position.lock.api)
 * ---- 复用现有钩, 本段补四件新钩:
 * ----   input.lock: GetCapture 绑定期恒回绑窗(后台 GetCapture=NULL/别窗, 点门槛
 * ----            型游戏按它丢弃点击 —— 登录UI不受影响、世界点击偶发的判据面)
 * ----   cursor:    GetCursorInfo 光标位改写到虚拟光标
 * ----   clip.lock: ClipCursor 吞噬(游戏锁不了真光标)
 * ----   position.lock.message: 取消息层(Peek/GetMessage W/A)把真实鼠标消息的
 * ----            位置钉死到虚拟光标 —— 真鼠标路过后台窗口不再把游戏悬停态带跑 ---- */
static volatile LONG g_pack;                     /* 全家桶总闸 */

static HWND (WINAPI *g_realGetCapture)(void);    /* GetCapture(热补丁, a1 形状) */
static BYTE *g_gcFunc, *g_gcTramp;
static BYTE  g_gcOrig[5];

static HWND WINAPI our_getcapture(void)
{
    if (g_pack && g_hwnd && IsWindow(g_hwnd)) return g_hwnd;
    return g_realGetCapture();
}

static DWORD *g_gciSlot;                         /* GetCursorInfo(FF25 槽 thunk) */
static DWORD   g_gciOrig;

static BOOL WINAPI our_getcursorinfo(PCURSORINFO pci)
{
    BOOL r = ((BOOL (WINAPI *)(PCURSORINFO))(DWORD_PTR)g_gciOrig)(pci);
    if (g_pack && r && pci && pci->cbSize >= sizeof(CURSORINFO)
        && g_hwnd && IsWindow(g_hwnd)) {
        POINT p = { (LONG)g_vx, (LONG)g_vy };
        ClientToScreen(g_hwnd, &p);
        pci->ptScreenPos = p;
    }
    return r;
}

static DWORD *g_ccSlot;                          /* ClipCursor(FF25 槽 thunk) */
static DWORD   g_ccOrig;

static BOOL WINAPI our_clipcursor(const RECT *rc)
{
    if (g_pack && g_hwnd && IsWindow(g_hwnd)) return TRUE;   /* 吞: 后台不锁真光标 */
    return ((BOOL (WINAPI *)(const RECT *))(DWORD_PTR)g_ccOrig)(rc);
}

static void pin_mouse_msg(MSG *m)
{
    if (!g_pack || !m || !m->hwnd || !g_hwnd || !IsWindow(g_hwnd)) return;
    UINT t = m->message;
    if (t < 0x200 || t > 0x20E) return;           /* WM_MOUSEFIRST..WM_MOUSELAST */
    if (GetWindowThreadProcessId(m->hwnd, NULL)
        != GetWindowThreadProcessId(g_hwnd, NULL)) return;   /* 只管绑定窗口线程 */
    POINT sp = { (LONG)g_vx, (LONG)g_vy };
    ClientToScreen(g_hwnd, &sp);
    if (t == 0x020A || t == 0x020E) {             /* 滚轮: lParam=屏幕坐标 */
        m->lParam = MAKELPARAM(sp.x, sp.y);
    } else {
        POINT cp = sp;
        ScreenToClient(m->hwnd, &cp);             /* 其余: lParam=收件窗客户坐标 */
        m->lParam = MAKELPARAM(cp.x, cp.y);
    }
    m->pt = sp;
}

static BYTE *g_msgFunc[4];                        /* 0=PeekW 1=PeekA 2=GetW 3=GetA */
static BYTE  g_msgOrig[4][5];
static BYTE *g_msgTramp[4];

static BOOL WINAPI our_peek_w(LPMSG m, HWND h, UINT lo, UINT hi, UINT rm)
{
    BOOL r = ((BOOL (WINAPI *)(LPMSG, HWND, UINT, UINT, UINT))g_msgTramp[0])(
        m, h, lo, hi, rm);
    if (r) pin_mouse_msg(m);
    return r;
}
static BOOL WINAPI our_peek_a(LPMSG m, HWND h, UINT lo, UINT hi, UINT rm)
{
    BOOL r = ((BOOL (WINAPI *)(LPMSG, HWND, UINT, UINT, UINT))g_msgTramp[1])(
        m, h, lo, hi, rm);
    if (r) pin_mouse_msg(m);
    return r;
}
static BOOL WINAPI our_get_w(LPMSG m, HWND h, UINT lo, UINT hi)
{
    BOOL r = ((BOOL (WINAPI *)(LPMSG, HWND, UINT, UINT))g_msgTramp[2])(m, h, lo, hi);
    if ((int)r > 0) pin_mouse_msg(m);
    return r;
}
static BOOL WINAPI our_get_a(LPMSG m, HWND h, UINT lo, UINT hi)
{
    BOOL r = ((BOOL (WINAPI *)(LPMSG, HWND, UINT, UINT))g_msgTramp[3])(m, h, lo, hi);
    if ((int)r > 0) pin_mouse_msg(m);
    return r;
}

/* 一键武装: rc=0 全装; 非 0 = 第一个失败钩的码(部分武装照开闸, 容忍哲学同
   hook_active_api)。arg1 = 武装位图: bit0 键态 bit1 光标锁 bit2 GetCapture
   bit3 GetCursorInfo bit4 ClipCursor bit5 PeekW bit6 PeekA bit7 GetW bit8 GetA */
static int hook_mouse_pack(void)
{
    if (g_pack) return 0;
    if (!g_hdr) return 43;
    if (!g_hwnd || !IsWindow(g_hwnd)) return 40;
    HMODULE u = GetModuleHandleA(OBF_USER32);
    if (!u) return 100;
    DWORD bits = 0;
    int fail = 0, rc;

    rc = hook_keystate();
    if (!rc) bits |= 1; else if (!fail) fail = rc;
    rc = hook_mouse_lock();
    if (!rc) bits |= 2; else if (!fail) fail = rc;

    BYTE *f = (BYTE *)GetProcAddress(u, OBF_GETCAPTURE);
    if (!f) { if (!fail) fail = 110; }
    else {
        rc = hotpatch(f, (void *)our_getcapture, &g_gcTramp, g_gcOrig);
        if (rc) { g_gcTramp = NULL; if (!fail) fail = 111 + (rc - 31); }
        else {
            g_gcFunc = f;
            g_realGetCapture = (HWND (WINAPI *)(void))g_gcTramp;
            bits |= 4;
        }
    }

    BYTE *gci = (BYTE *)GetProcAddress(u, OBF_GETCURSORINFO);
    if (gci && gci[0] == 0xFF && gci[1] == 0x25) {
        DWORD *slot = (DWORD *)(DWORD_PTR)*(DWORD *)(gci + 2);
        if (slot && patch_slot(slot, (DWORD)(DWORD_PTR)our_getcursorinfo,
                               &g_gciOrig) == 0) {
            g_gciSlot = slot;
            bits |= 8;
        } else if (!fail) fail = 121;
    } else if (!fail) fail = 122;

    BYTE *cc = (BYTE *)GetProcAddress(u, OBF_CLIPCURSOR);
    if (cc && cc[0] == 0xFF && cc[1] == 0x25) {
        DWORD *slot = (DWORD *)(DWORD_PTR)*(DWORD *)(cc + 2);
        if (slot && patch_slot(slot, (DWORD)(DWORD_PTR)our_clipcursor,
                               &g_ccOrig) == 0) {
            g_ccSlot = slot;
            bits |= 0x10;
        } else if (!fail) fail = 125;
    } else if (!fail) fail = 126;

    const char *mnames[4] = { OBF_PEEKMESSAGEW, OBF_PEEKMESSAGEA,
                              OBF_GETMESSAGEW, OBF_GETMESSAGEA };
    void *mdetours[4] = { (void *)our_peek_w, (void *)our_peek_a,
                          (void *)our_get_w, (void *)our_get_a };
    for (int i = 0; i < 4; i++) {
        BYTE *fm = (BYTE *)GetProcAddress(u, mnames[i]);
        if (!fm) { if (!fail) fail = 130 + i * 4; continue; }
        rc = hotpatch(fm, mdetours[i], &g_msgTramp[i], g_msgOrig[i]);
        if (rc) { g_msgTramp[i] = NULL; if (!fail) fail = 131 + i * 4 + (rc - 31); }
        else { g_msgFunc[i] = fm; bits |= 0x20 << i; }
    }

    g_hdr->arg1 = bits;
    if (fail) return fail;               /* 部分武装也开闸, 码透传宿主定夺 */
    g_pack = 1;
    return 0;
}

static void unhook_mouse_pack(void)
{
    g_pack = 0;
    if (g_gcTramp) {
        restore_hotpatch(g_gcFunc, g_gcOrig, g_gcTramp);
        g_gcFunc = NULL; g_gcTramp = NULL; g_realGetCapture = NULL;
    }
    if (g_gciSlot) { patch_slot(g_gciSlot, g_gciOrig, &g_gciOrig); g_gciSlot = NULL; }
    if (g_ccSlot)  { patch_slot(g_ccSlot, g_ccOrig, &g_ccOrig); g_ccSlot = NULL; }
    for (int i = 0; i < 4; i++) {
        if (!g_msgTramp[i]) continue;
        restore_hotpatch(g_msgFunc[i], g_msgOrig[i], g_msgTramp[i]);
        g_msgFunc[i] = NULL; g_msgTramp[i] = NULL;
    }
    unhook_mouse_lock();
    unhook_keystate();
}

/* ---- 模块隐藏(大漠 dx.public.hide.dll 简版): 从 PEB->Ldr 三条链摘掉本模块。
 * ---- 摘链后 EnumProcessModules/Toolhelp 快照/进程内 GetModuleHandle 都找不到。 ---- */
static int hide_module(void)
{
#ifdef _WIN64
    BYTE *peb = (BYTE *)__readgsqword(0x60);
#else
    BYTE *peb = (BYTE *)__readfsdword(0x30);
#endif
    if (!peb) return 50;
    BYTE *ldr = *(BYTE **)(peb + 0x0C);                /* PEB->Ldr (x86/x64 同偏移) */
    if (!ldr) return 51;
    /* LDR_DATA_TABLE_ENTRY: InLoadOrder(0x00) InMemoryOrder(0x08) InInitOrder(0x10)
     * 三条 LIST_ENTRY 链头都在 Ldr 里(x86: +0x0C/+0x14/+0x1C; x64: +0x10/+0x20/+0x30),
     * 模块条目 DllBase 在 InLoadOrderLinks 起 x86+0x18 / x64+0x30。 */
    int removed = 0;
    LIST_ENTRY *heads[3];
#ifdef _WIN64
    heads[0] = (LIST_ENTRY *)(ldr + 0x10);
    heads[1] = (LIST_ENTRY *)(ldr + 0x20);
    heads[2] = (LIST_ENTRY *)(ldr + 0x30);
    const size_t baseOff = 0x30 - 0x00;               /* DllBase 距 InLoadOrderLinks */
#else
    heads[0] = (LIST_ENTRY *)(ldr + 0x0C);
    heads[1] = (LIST_ENTRY *)(ldr + 0x14);
    heads[2] = (LIST_ENTRY *)(ldr + 0x1C);
    const size_t baseOff = 0x18;
#endif
    /* 先在 InLoadOrder 链上找到 DllBase==g_self 的条目, 再逐链摘除 */
    BYTE *entry = NULL;
    for (LIST_ENTRY *le = heads[0]->Flink; le != heads[0]; le = le->Flink) {
        if (*(HMODULE *)((BYTE *)le + baseOff) == g_self) { entry = (BYTE *)le; break; }
    }
    if (!entry) return 52;                            /* 不在链上(可能已隐藏) */
    for (int i = 0; i < 3; i++) {
        LIST_ENTRY *le = (LIST_ENTRY *)(entry + (size_t)heads[i] - (size_t)heads[0]);
        le->Blink->Flink = le->Flink;
        le->Flink->Blink = le->Blink;
        removed++;
    }
    if (removed != 3) return 53;
    /* 语义(=大漠 dx.public.hide.dll 覆盖范围): 摘三条链 → 模块枚举/扫链找不到;
     * GetModuleHandle 走 LdrpHashTable 仍可按名解析(不追, 摘哈希表是兔子洞),
     * 结果放 arg1 备查: 1=枚举隐+按名仍可解析, 0=连按名也解析不到。 */
    wchar_t path[MAX_PATH];
    GetModuleFileNameW(g_self, path, MAX_PATH);
    wchar_t *base = wcsrchr(path, L'\\');
    base = base ? base + 1 : path;
    g_hdr->arg1 = GetModuleHandleW(base) ? 1 : 0;
    return 1;
}

/* ---- worker: 挂共享内存, 心跳 + 命令泵 ---- */
static DWORD WINAPI worker(LPVOID unused)
{
    (void)unused;
    char name[64];
    wsprintfA(name, OBF_SHM_FMT, GetCurrentProcessId());
    g_map = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, name);
    if (!g_map) return 1;
    g_hdr = (Header *)MapViewOfFile(g_map, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    if (!g_hdr) { CloseHandle(g_map); g_map = NULL; return 2; }
    if (g_hdr->magic != SHM_MAGIC || g_hdr->version != 8) {
        UnmapViewOfFile(g_hdr); CloseHandle(g_map);
        g_hdr = NULL; g_map = NULL;
        if (g_mutex) { CloseHandle(g_mutex); g_mutex = NULL; }
        return 3;
    }
    g_hdr->state = 1;
    g_hdr->self_base = g_selfBase;
    for (;;) {
        g_hdr->heartbeat++;
        if (g_hdr->cmd_seq != g_hdr->ack_seq) {
            DWORD r = 0xDEADBEEF;
            BOOL quit = FALSE;
            switch (g_hdr->cmd) {
            case CMD_PING:    r = 0x50494731u; break;
            case CMD_BYE:     unhook_gl(); unhook_d3d9(); unhook_mouse_pack();
                              fake_active(0);
                              g_hdr->state = 0; r = 0;
                              quit = TRUE; break;
            case CMD_HOOK_GL: r = (DWORD)hook_gl(); break;
            case CMD_HOOK_D3D9: r = (DWORD)hook_d3d9(); break;
            case CMD_KEYSTATE: r = (DWORD)hook_keystate(); break;
            case CMD_MOUSE_LOCK:
                              if (g_hdr->arg0) r = (DWORD)hook_mouse_lock();
                              else { unhook_mouse_lock(); r = 0; }
                              break;
            case CMD_MOUSE_PACK:
                              if (g_hdr->arg0) r = (DWORD)hook_mouse_pack();
                              else { unhook_mouse_pack(); r = 0; }
                              break;
            case CMD_FAKE_ACTIVE: r = (DWORD)fake_active((int)g_hdr->arg0); break;
            case CMD_HIDE:    r = (DWORD)hide_module(); break;
            case CMD_BIND:    g_hwnd = (HWND)(DWORD_PTR)g_hdr->arg0;
                              g_buttons = 0;
                              g_virtAlt = 0;
                              {   /* 虚拟光标初始化为客户区中心(避免 0,0 起步) */
                                  RECT rc;
                                  if (GetClientRect(g_hwnd, &rc)) {
                                      g_vx = (rc.right - rc.left) / 2;
                                      g_vy = (rc.bottom - rc.top) / 2;
                                  }
                              }
                              g_ritHwnd = find_rit_hwnd();   /* RIT 同步目标缓存 */
                              r = IsWindow(g_hwnd) ? 1 : 0; break;
            case CMD_KEY:     r = (DWORD)do_key(g_hdr->arg0, g_hdr->arg1); break;
            case CMD_MOUSE:   r = (DWORD)do_mouse(g_hdr->arg0, g_hdr->arg1); break;
            case CMD_RAWINFO: {
                UINT n = 0;
                GetRegisteredRawInputDevices(NULL, &n, sizeof(RAWINPUTDEVICE));
                RAWINPUTDEVICE dev[8];
                HWND target = NULL;
                DWORD cnt = 0;
                if (n) {
                    UINT m = n < 8 ? n : 8;
                    if (GetRegisteredRawInputDevices(dev, &m, sizeof(RAWINPUTDEVICE))) {
                        for (UINT i = 0; i < m; i++)
                            if (dev[i].hwndTarget) { target = dev[i].hwndTarget; cnt++; }
                    }
                    n = m;
                }
                g_hdr->arg0 = (DWORD)(DWORD_PTR)target;
                g_hdr->arg1 = (n << 16) | cnt;   /* 高16=注册设备数, 低16=有目标的 */
                r = 0;
                break;
            }
            default:          r = 0xFFFFFFFEu; break;
            }
            g_hdr->ack_result = r;
            g_hdr->ack_seq = g_hdr->cmd_seq;      /* 完成即应答 */
            if (quit) break;                      /* 应答落地再退场 */
        }
        Sleep(5);
    }
    UnmapViewOfFile(g_hdr);
    CloseHandle(g_map);
    g_hdr = NULL; g_map = NULL;
    if (g_mutex) { CloseHandle(g_mutex); g_mutex = NULL; }
    /* 故意不 FreeLibrary: our_swap/蹦床地址仍可能被渲染线程引用, 留驻最安全;
       每次构建的注入件文件名不同(注入器按 size+mtime 拷贝), 旧副本驻留无害。 */
    return 0;
}

/* VEH: 崩溃瞬间把异常码/地址写进共享内存(只记首例), 宿主读走定位崩点模块 */
static volatile LONG g_ringPos;

static LONG WINAPI veh(EXCEPTION_POINTERS *ep)
{
    if (g_hdr) {
        int first = (g_hdr->crash_addr == 0);
        if (first) {
            g_hdr->crash_code = ep->ExceptionRecord->ExceptionCode;
            g_hdr->crash_addr = (DWORD)(DWORD_PTR)ep->ExceptionRecord->ExceptionAddress;
        }
        LONG i = InterlockedIncrement(&g_ringPos) & 3;      /* 环形记最近 4 例 */
        g_hdr->ring[i * 2] = ep->ExceptionRecord->ExceptionCode;
        g_hdr->ring[i * 2 + 1] = (DWORD)(DWORD_PTR)ep->ExceptionRecord->ExceptionAddress;
        /* 栈转储(偏移 2048, 帧区前空档): [0]=Eip, [1..15]=Esp 起栈内容(返址可见) */
        DWORD *dump = (DWORD *)((BYTE *)g_hdr + 2048);
#ifdef _WIN64
        dump[0] = (DWORD)ep->ContextRecord->Rip;
        DWORD *esp = (DWORD *)(DWORD_PTR)ep->ContextRecord->Rsp;
#else
        dump[0] = (DWORD)ep->ContextRecord->Eip;
        DWORD *esp = (DWORD *)(DWORD_PTR)ep->ContextRecord->Esp;
#endif
        dump[16] = GetCurrentThreadId();                 /* 崩在哪个线程 */
        dump[17] = (DWORD)(DWORD_PTR)esp;                /* 崩时 Esp 本体 */
        dump[23] = g_enterCount;                         /* 崩时 our_present 进入数 */
#ifndef _WIN64
        /* 真实栈回溯(Esp 已不可信): fs:[4/8] 拿栈界, 从栈顶向下扫 8KB,
           收集落在模块内的返回地址 → 穷人版调用链 DIAG(64+i*2)=值/模块基 */
        DWORD sb = __readfsdword(0x04), sl = __readfsdword(0x08);
        ((DWORD *)((BYTE *)g_hdr + 2048))[60] = sb;
        ((DWORD *)((BYTE *)g_hdr + 2048))[61] = sl;
        /* 首例 AV 全现场快照(shm+2MB, 帧区之后): 寄存器 + 真实栈 64KB + Eip 字节 256 */
        if (first && ep->ExceptionRecord->ExceptionCode == 0xC0000005) {
            DWORD *snap = (DWORD *)((BYTE *)g_hdr + 0x200000);
            CONTEXT *c = ep->ContextRecord;
            snap[0] = c->Eip; snap[1] = c->Esp; snap[2] = c->Ebp;
            snap[3] = c->Eax; snap[4] = c->Ebx; snap[5] = c->Ecx;
            snap[6] = c->Edx; snap[7] = c->Esi; snap[8] = c->Edi;
            DWORD copyFrom = sb > 0x10000 ? sb - 0x10000 : sl;
            if (copyFrom < sl) copyFrom = sl;
            memcpy(snap + 16, (void *)copyFrom, sb - copyFrom);
            if (c->Eip >= sl && c->Eip < sb)
                memcpy((BYTE *)(snap + 16) + 0x10000,
                       (void *)(c->Eip - 128), 256);
        }
        DWORD *sw = &((DWORD *)((BYTE *)g_hdr + 2048))[64];
        int nsw = 0;
        for (DWORD *p = (DWORD *)(sb - 4); p > (DWORD *)(sb - 8192) && nsw < 40; p--) {
            DWORD v = *p;
            if (v <= 0x10000 || v >= 0x7FFF0000u) continue;
            HMODULE m = NULL;
            if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                   GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                   (LPCWSTR)v, &m) && m) {
                sw[nsw * 2] = v;
                sw[nsw * 2 + 1] = (DWORD)(DWORD_PTR)m;
                nsw++;
            }
        }
        ((DWORD *)((BYTE *)g_hdr + 2048))[62] = (DWORD)nsw;
#endif
        /* 区块指纹: Eip/Esp 所在页的 VirtualQuery(Type/Protect/AllocBase/State)
           + Eip 是否落在某模块内 —— 分辨 堆/线程栈/映射节/DEP数据页 */
        HMODULE mod = NULL;
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)(DWORD_PTR)dump[0], &mod);
        dump[18] = (DWORD)(DWORD_PTR)mod;
        MEMORY_BASIC_INFORMATION mbi;
        DWORD *probes[2] = { (DWORD *)(DWORD_PTR)dump[0], esp };
        for (int p = 0; p < 2; p++) {
            if (VirtualQuery(probes[p], &mbi, sizeof(mbi)) == sizeof(mbi)) {
                DWORD *o = &dump[24 + p * 4];
                o[0] = mbi.Type; o[1] = mbi.Protect;
                o[2] = (DWORD)(DWORD_PTR)mbi.AllocationBase; o[3] = mbi.State;
            }
        }
        for (int k = 0; k < 15; k++)
            dump[1 + k] = esp[k];
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

/* 远程线程入口(injector 第二跳): DLL 已映射完, 此处无 loader lock。
 * 无参 cdecl: CreateRemoteThread 的 lpParameter 恒为 NULL, 无视之;
 * cdecl 不清栈, 线程蹦床调完即 RtlExitUserThread 不返回, 漂移无害;
 * 更关键的是 x86 导出名不会被修饰成 DxInit@4。 */
__declspec(dllexport) DWORD DxInit(void)
{
    obf_init();                                     /* 遮蔽串解码(先于一切使用) */
    DisableThreadLibraryCalls(g_self);
    {
        HMODULE u = GetModuleHandleA(OBF_USER32);
        if (u) {
            g_pGetAsyncKeyState =
                (SHORT (WINAPI *)(int))GetProcAddress(u, OBF_GETASYNCKEYSTATE);
            g_pGetGUIThreadInfo =
                (BOOL (WINAPI *)(DWORD, void *))GetProcAddress(u, OBF_GETGUIThreadINFO);
        }
    }
    /* 进程级互斥体: 同一进程任意副本同时只允许一个 worker(宿主 attach 前会先
       BYE 掉旧实例, 正常轮不到这里撞锁; 撞锁=旧实例没退干净) */
    char lk[64];
    wsprintfA(lk, OBF_MUTEX_FMT, GetCurrentProcessId());
    g_mutex = CreateMutexA(NULL, FALSE, lk);
    if (g_mutex && GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(g_mutex); g_mutex = NULL;
        return 6;                                /* 旧实例仍在, 先 BYE 再注入 */
    }
    if (!g_mutex) return 5;
    static LONG vehAdded = 0;                    /* 重绑=同模块再 DxInit, VEH 只挂一次 */
    if (InterlockedExchange(&vehAdded, 1) == 0)
        AddVectoredExceptionHandler(1, veh);
    g_selfBase = (DWORD)(DWORD_PTR)g_self;
    HANDLE t = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    if (!t) { CloseHandle(g_mutex); g_mutex = NULL; return 5; }
    CloseHandle(t);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE self, DWORD reason, LPVOID unused)
{
    (void)unused;
    if (reason == DLL_PROCESS_ATTACH) g_self = self;
    return TRUE;
}
