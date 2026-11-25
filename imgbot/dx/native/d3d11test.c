/* d3d11test.c — DXGI 图色验收靶(工单 damo-bg-capture/01): 640x480 窗口, 等 6 秒
 * (给注入留窗口)再建 D3D11Device+SwapChain, 循环 ClearOK 变色 + Present(≈30fps),
 * 跑 90 秒退出。颜色循环=帧差验收锚点。x86+x64 双份(build.py), 与 dmcore 同源
 * mingw 头(接口类型直用, 不再手数 vtable)。 */
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>

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
    wc.lpszClassName = L"imgbot_d3d11test";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, L"imgbot_d3d11test", L"imgbot-d3d11-test",
        0x10CA0000 /*WS_VISIBLE|overlappedwindow*/, 100, 100, 640, 480,
        NULL, NULL, inst, NULL);

    Sleep(6000);                      /* 注入窗口: 宿主在此期间 attach + hook_dxgi */

    DXGI_SWAP_CHAIN_DESC sd = {0};
    sd.BufferDesc.Width = 640;
    sd.BufferDesc.Height = 480;
    sd.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    sd.BufferDesc.RefreshRate.Numerator = 60;
    sd.BufferDesc.RefreshRate.Denominator = 1;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 1;
    sd.OutputWindow = hwnd;
    sd.Windowed = TRUE;
    sd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    ID3D11Device *dev = NULL;
    IDXGISwapChain *swap = NULL;
    ID3D11DeviceContext *ctx = NULL;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,
        D3D11_SDK_VERSION, &sd, &swap, &dev, NULL, &ctx);
    if (hr < 0 || !swap || !dev) {
        /* 无硬显(虚拟机/远程会话)退 WARP 软渲 */
        hr = D3D11CreateDeviceAndSwapChain(
            NULL, D3D_DRIVER_TYPE_WARP, NULL, 0, NULL, 0,
            D3D11_SDK_VERSION, &sd, &swap, &dev, NULL, &ctx);
        if (hr < 0 || !swap || !dev) return 4;
    }

    ID3D11Texture2D *back = NULL;
    if (swap->lpVtbl->GetBuffer(swap, 0, &IID_ID3D11Texture2D, (void **)&back) < 0)
        return 5;
    ID3D11RenderTargetView *rtv = NULL;
    if (dev->lpVtbl->CreateRenderTargetView(dev, (ID3D11Resource *)back, NULL, &rtv) < 0)
        return 6;
    back->lpVtbl->Release(back);

    DWORD tick0 = GetTickCount();
    FLOAT color[4];
    for (int frame = 0; ; frame++) {
        DWORD c = (frame * 8) & 0xFFFFFF;
        color[0] = (c & 0xFF) / 255.0f;           /* B */
        color[1] = ((c >> 8) & 0xFF) / 255.0f;    /* G */
        color[2] = ((c >> 16) & 0xFF) / 255.0f;   /* R */
        color[3] = 1.0f;
        ctx->lpVtbl->ClearRenderTargetView(ctx, rtv, color);
        swap->lpVtbl->Present(swap, 1, 0);
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
