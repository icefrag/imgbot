/* injector.c — 同位宽注入器: 远程 LoadLibraryW(dmcore.dll) + 远程 DxInit 两跳.
 *
 * 用法: injector.exe <pid> <dll绝对路径>
 * 输出: OK base=0x... rc=0 成功; ERR <n> 失败原因码.
 * Python 宿主按目标位宽挑 injector32/64.exe; 本注入器与目标同位宽, 所以
 * kernel32!LoadLibraryW 地址在目标内有效, CreateRemoteThread 直用。
 */
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>

typedef DWORD (WINAPI *FnLoadLibraryW)(LPCWSTR);
typedef DWORD (WINAPI *FnThreadProc)(LPVOID);

static int fail(const char *what, DWORD e)
{
    printf("ERR %s gle=%lu\n", what, e);
    return 1;
}

int main(int argc, char **argv)
{
    if (argc < 3) { printf("ERR args\n"); return 2; }
    DWORD pid = (DWORD)strtoul(argv[1], NULL, 0);
    wchar_t dllPath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, dllPath, MAX_PATH);

    /* 0) 先拷到临时目录再注入: 仓内原文件永不进目标进程, 不会被锁;
     *    拷贝名带 size+mtime, 内容变了名字变, 避免 LoadLibrary 同名去重回旧模块 */
    wchar_t tmpPath[MAX_PATH];
    HANDLE f = CreateFileW(dllPath, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return fail("open-dll", GetLastError());
    LARGE_INTEGER size, mtime;
    GetFileSizeEx(f, &size);
    FILETIME ft;
    GetFileTime(f, NULL, NULL, &ft);
    mtime.LowPart = ft.dwLowDateTime; mtime.HighPart = ft.dwHighDateTime;
    CloseHandle(f);
    wchar_t fname[MAX_PATH], tmpDir[MAX_PATH];
    const wchar_t *p = wcsrchr(dllPath, L'\\');
    wcscpy(fname, p ? p + 1 : dllPath);
    GetTempPathW(MAX_PATH, tmpDir);
    _snwprintf(tmpPath, MAX_PATH, L"%simgbotdx_%s_%llX_%llX", tmpDir, fname,
               (unsigned long long)size.QuadPart, (unsigned long long)mtime.QuadPart);
    if (!CopyFileW(dllPath, tmpPath, FALSE)) {
        /* 同名副本仍被目标映射(锁死)时换唯一名重试 —— DLL 按内容指纹命名,
         * 内容不变名不变, 而留驻副本在目标存活期间永不释放 */
        wchar_t alt[MAX_PATH];
        _snwprintf(alt, MAX_PATH, L"%s_%X", tmpPath, GetTickCount());
        if (!CopyFileW(dllPath, alt, FALSE)) return fail("copy", GetLastError());
        wcscpy(tmpPath, alt);
    }
    wcscpy(dllPath, tmpPath);          /* 之后全部走临时副本 */

    /* 1) 本地加载同一 DLL 求 DxInit 的 RVA(文件同源, 布局一致) */
    HMODULE local = LoadLibraryW(dllPath);
    if (!local) return fail("local-load", GetLastError());
    FARPROC init = GetProcAddress(local, "DxInit");
    if (!init) { FreeLibrary(local); return fail("no-export", 0); }
    DWORD_PTR rva = (DWORD_PTR)init - (DWORD_PTR)local;
    FreeLibrary(local);

    /* 2) 远程 LoadLibraryW(路径) */
    DWORD rights = PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
                   PROCESS_VM_WRITE | PROCESS_VM_READ | PROCESS_QUERY_INFORMATION;
    HANDLE hp = OpenProcess(rights, FALSE, pid);
    if (!hp) return fail("open", GetLastError());
    SIZE_T pathBytes = (wcslen(dllPath) + 1) * sizeof(wchar_t);
    LPVOID remotePath = VirtualAllocEx(hp, NULL, pathBytes, MEM_COMMIT, PAGE_READWRITE);
    if (!remotePath) return fail("alloc", GetLastError());
    if (!WriteProcessMemory(hp, remotePath, dllPath, pathBytes, NULL))
        return fail("write", GetLastError());
    FnLoadLibraryW pLoad = (FnLoadLibraryW)(uintptr_t)
        GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    HANDLE th = CreateRemoteThread(hp, NULL, 0, (FnThreadProc)(uintptr_t)pLoad,
                                   remotePath, 0, NULL);
    if (!th) return fail("remote-load", GetLastError());
    WaitForSingleObject(th, 10000);
    CloseHandle(th);

    /* 3) 模块快照拿远程基址(退出码在 x64 会截断, 不用它) */
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
    if (snap == INVALID_HANDLE_VALUE) return fail("snap", GetLastError());
    MODULEENTRY32W me; me.dwSize = sizeof(me);
    DWORD_PTR remoteBase = 0;
    wchar_t want[MAX_PATH];
    wcscpy(want, dllPath);
    wchar_t *base = wcsrchr(want, L'\\');
    base = base ? base + 1 : want;
    _wcslwr(base);
    if (Module32FirstW(snap, &me)) {
        do {
            wchar_t cur[MAX_PATH];
            wcscpy(cur, me.szModule);
            _wcslwr(cur);
            if (wcscmp(cur, base) == 0) { remoteBase = (DWORD_PTR)me.modBaseAddr; break; }
        } while (Module32NextW(snap, &me));
    }
    CloseHandle(snap);
    if (!remoteBase) return fail("no-remote-base", 0);

    /* 4) 远程 DxInit */
    th = CreateRemoteThread(hp, NULL, 0, (FnThreadProc)(remoteBase + rva),
                            NULL, 0, NULL);
    if (!th) return fail("remote-init", GetLastError());
    WaitForSingleObject(th, 10000);
    DWORD rc = 0;
    GetExitCodeThread(th, &rc);
    CloseHandle(th);
    VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE);
    CloseHandle(hp);
    printf("OK base=0x%llx rc=%lu\n", (unsigned long long)remoteBase, rc);
    return rc ? 3 : 0;
}
