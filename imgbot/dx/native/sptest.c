/* sptest.c — 变速齿轮验收靶(build.py 32/64 双构建)。
 * 机制: 自检"加速已生效"(QueryPerformanceCounter 相对真实墙钟跑赢)后,
 * 分别以 QPC 与 timeGetTime 各烧一段虚拟时间, 用不在钩面的墙钟
 * (GetSystemTimeAsFileTime)结算真实耗时 —— rate=r 时应 ≈ 名义值/r。
 * 打印 SPRESULT 行供 scripts/speed_smoke.py 断言; 超时未加速也照跑(烟测判负)。 */
#include <windows.h>
#include <stdio.h>
#include <mmsystem.h>

static ULONGLONG wall100ns(void)
{
    ULONGLONG v;
    GetSystemTimeAsFileTime((FILETIME *)&v);
    return v;
}

int main(void)
{
    LARGE_INTEGER f, a, b;
    QueryPerformanceFrequency(&f);
    printf("SPTEST ready\n");
    fflush(stdout);

    /* 阶段0: 等宿主注入并设速(滑窗看 QPC 对墙钟跑赢 1.5 倍), 最多 10s */
    double ratio = 1.0;
    for (int i = 0; i < 100; i++) {
        QueryPerformanceCounter(&a);
        ULONGLONG w0 = wall100ns();
        Sleep(100);
        ULONGLONG dw = wall100ns() - w0;
        QueryPerformanceCounter(&b);
        if (dw > 0) {
            ratio = (double)(b.QuadPart - a.QuadPart) * 1e7
                    / ((double)dw * (double)f.QuadPart);
            if (ratio > 1.5) break;
        }
    }
    printf("SPTEST ratio=%.2f\n", ratio);
    fflush(stdout);

    /* 阶段1: QPC 烧 3 秒虚拟时间; 每 500 虚拟ms 打一行采样(wall=真墙钟) */
    ULONGLONG wa = wall100ns();
    QueryPerformanceCounter(&a);
    LONGLONG next = 500;
    for (;;) {
        QueryPerformanceCounter(&b);
        LONGLONG virt = (b.QuadPart - a.QuadPart) * 1000 / f.QuadPart;
        if (virt >= next) {
            printf("SAMPLE virt=%lldms wall=%llums\n", virt,
                   (unsigned long long)((wall100ns() - wa) / 10000));
            fflush(stdout);
            next += 500;
        }
        if (virt >= 3000) break;
    }
    printf("SPRESULT qpc_wall_ms=%llu\n",
           (unsigned long long)((wall100ns() - wa) / 10000));
    fflush(stdout);

    /* 阶段2: timeGetTime 烧 2 秒虚拟时间 */
    wa = wall100ns();
    DWORD g0 = timeGetTime();
    while (timeGetTime() - g0 < 2000) {}
    printf("SPRESULT tgt_wall_ms=%llu\n",
           (unsigned long long)((wall100ns() - wa) / 10000));
    fflush(stdout);
    return 0;
}
