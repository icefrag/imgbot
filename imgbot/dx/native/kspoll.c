/* kspoll.c — 键态钩子+虚拟光标钩验收靶: 游戏型输入消费方(不看消息, 每帧轮询
 * GetAsyncKeyState/GetKeyState/GetCursorPos), 兼收 WM_KEYDOWN/WM_CHAR 计数。
 * 计数器写在窗口标题里(宿主 GetWindowTextW 轮询读), 跑 60 秒退出:
 *   down=N  轮询看到 GetAsyncKeyState(0x8000 位) 次数  ← 只可能来自虚拟状态钩子
 *   latch=N 轮询看到 0x0001 位(按下未读) 次数          ← 读后清语义
 *   gks=N   轮询看到 GetKeyState(0x8000 位) 次数
 *   kd=N / char=N  wndproc 收到 WM_KEYDOWN / WM_CHAR 次数(消息配方+手动 Translate)
 *   gcx=N / gcy=N  轮询 GetCursorPos 读到的光标(虚拟光标钩开着时=注入位)
 * zig cc -target x86-windows -luser32 编译(x86: 键态钩子热补丁仅 x86)。
 */
#include <windows.h>

static int g_down, g_latch, g_gks, g_kd, g_char, g_gcx, g_gcy;

static void publish(HWND h)
{
    wchar_t t[160];
    wsprintfW(t, L"down=%d latch=%d gks=%d kd=%d char=%d gcx=%d gcy=%d",
              g_down, g_latch, g_gks, g_kd, g_char, g_gcx, g_gcy);
    SetWindowTextW(h, t);
}

static LRESULT WINAPI wndproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == WM_KEYDOWN) g_kd++;
    if (m == WM_CHAR) g_char++;
    return DefWindowProcW(h, m, w, l);
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    (void)prev; (void)cmd; (void)show;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = wndproc;
    wc.hInstance = inst;
    wc.lpszClassName = L"imgbot_kspoll";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, L"imgbot_kspoll", L"kspoll-wait",
        0x10CA0000 /*WS_VISIBLE|overlappedwindow*/, 100, 100, 400, 120,
        NULL, NULL, inst, NULL);
    DWORD tick0 = GetTickCount(), lastPub = 0;
    for (;;) {
        POINT cp;
        if (GetCursorPos(&cp)) { g_gcx = cp.x; g_gcy = cp.y; }
        SHORT a = GetAsyncKeyState(0x41);      /* 'A' */
        if (a & 0x8000) g_down++;
        if (a & 1) g_latch++;
        if (GetKeyState(0x41) & 0x8000) g_gks++;
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, 1)) {
            if (msg.message == 0x12) return 0; /* WM_QUIT */
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (GetTickCount() - lastPub > 50) {
            publish(hwnd);
            lastPub = GetTickCount();
        }
        if (GetTickCount() - tick0 > 60000) break;
        Sleep(10);
    }
    return 0;
}
