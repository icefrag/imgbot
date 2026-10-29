/* d3d9test.c — 阶段3 验收靶: 640x480 窗口, 等 6 秒(给注入留窗口)再建 D3D9 设备,
 * 循环 Clear 变色 + Present(≈30fps), 跑 90 秒退出。颜色循环=帧差验收锚点。
 * zig cc -target x86-windows -luser32 -lgdi32 编译; d3d9 动态加载, 无需导入库。
 * vtable 索引/PRESENT_PARAMETERS 偏移均经 mingw d3d9.h 实数核对(见 dmcore.c)。 */
#include <windows.h>

typedef long HRESULT;

static LRESULT WINAPI wndproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    return DefWindowProcW(h, m, w, l);
}

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmd, int show)
{
    (void)prev; (void)cmd; (void)show;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = wndproc;
    wc.hInstance = inst;
    wc.lpszClassName = L"imgbot_d3d9test";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, L"imgbot_d3d9test", L"imgbot-d3d9-test",
        0x10CA0000 /*WS_VISIBLE|overlappedwindow*/, 100, 100, 640, 480,
        NULL, NULL, inst, NULL);

    Sleep(6000);                      /* 注入窗口: 宿主在此期间 attach + hook_d3d9 */

    HMODULE d3d = LoadLibraryA("d3d9.dll");
    if (!d3d) return 1;
    void *(*pCreate9)(UINT) =
        (void *)GetProcAddress(d3d, "Direct3DCreate9");
    if (!pCreate9) return 2;
    void *d3d9 = pCreate9(32);
    if (!d3d9) return 3;

    /* D3DPRESENT_PARAMETERS(x86 偏移): W@0 H@4 fmt@8 cnt@12 ms@16 msq@20
     * swap@24 hwnd@28 windowed@32 ads@36 adsfmt@40 flags@44 rate@48 interval@52 */
    BYTE pp[56];
    for (int i = 0; i < 56; i++) pp[i] = 0;
    *(UINT *)(pp + 0)  = 640;
    *(UINT *)(pp + 4)  = 480;
    *(DWORD *)(pp + 8) = 22;          /* X8R8G8B8 */
    *(DWORD *)(pp + 12) = 1;
    *(DWORD *)(pp + 24) = 1;          /* D3DSWAPEFFECT_DISCARD */
    *(HWND *)(pp + 28) = hwnd;
    *(DWORD *)(pp + 32) = 1;          /* Windowed */

    void *dev = NULL;
    void **d3dvt = *(void ***)d3d9;
    HRESULT hr = ((HRESULT (WINAPI *)(void *, UINT, DWORD, HWND, DWORD, void *,
                                      void **))d3dvt[16])(              /* CreateDevice */
        d3d9, 0, 1 /*HAL*/, hwnd, 0x40 /*SOFTWARE_VERTEXPROCESSING*/, pp, &dev);
    if (hr != 0 || !dev) return 4;

    void **vt = *(void ***)dev;
    HRESULT (WINAPI *pClear)(void *, DWORD, const RECT *, DWORD, DWORD, float, DWORD)
        = (void *)vt[43];             /* Clear=43 */
    HRESULT (WINAPI *pPresent)(void *, const RECT *, const RECT *, HWND,
                               const void *, DWORD) = (void *)vt[17]; /* Present=17 */

    DWORD tick0 = GetTickCount();
    for (int frame = 0; ; frame++) {
        DWORD color = (frame * 8) & 0xFFFFFF;
        pClear(dev, 0, NULL, 1 /*D3DCLEAR_TARGET*/, color, 1.0f, 0);
        pPresent(dev, NULL, NULL, NULL, NULL, 0);
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, 1)) {
            if (msg.message == 0x12) return 0;         /* WM_QUIT */
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (GetTickCount() - tick0 > 90000) break;
        Sleep(33);
    }
    return 0;
}
