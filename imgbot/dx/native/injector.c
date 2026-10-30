/* injector.c — 同位宽注入器: 远程 LoadLibraryW(dmcore.dll) + 远程 DxInit 两跳.
 *
 * 用法: injector.exe <pid> <dll绝对路径>
 * 输出: OK base=0x... rc=0 成功; ERR <n> 失败原因码.
 * Python 宿主按目标位宽挑 injector32/64.exe; 本注入器与目标同位宽, 所以
 * kernel32!LoadLibraryW 地址在目标内有效, CreateRemoteThread 直用。
 *
 * 驻留复用: 上次会话的副本 BYE 后常驻目标(设计如此, vtable 钩闸着纯转发),
 * 临时副本名按 size+mtime 派生(确定性) —— 重绑时先按名扫模块, 命中就直接
 * 远程 DxInit 复用同模块(在世设备无缝续捕), 不再拷贝/重载新副本。
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

/* 模块快照按名(小写比较)找远程基址, 找不到返回 0 */
static DWORD_PTR find_module(DWORD pid, const wchar_t *lowerName)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    MODULEENTRY32W me; me.dwSize = sizeof(me);
    DWORD_PTR base = 0;
    if (Module32FirstW(snap, &me)) {
        do {
            wchar_t cur[MAX_PATH];
            wcscpy(cur, me.szModule);
            _wcslwr(cur);
            if (wcscmp(cur, lowerName) == 0) { base = (DWORD_PTR)me.modBaseAddr; break; }
        } while (Module32NextW(snap, &me));
    }
    CloseHandle(snap);
    return base;
}

/* 本地加载同一 DLL 求 DxInit 的 RVA(文件同源, 布局一致); 失败返回 0 */
static DWORD_PTR rva_of_init(const wchar_t *path)
{
    HMODULE local = LoadLibraryW(path);
    if (!local) return 0;
    FARPROC init = GetProcAddress(local, "DxInit");
    DWORD_PTR rva = init ? (DWORD_PTR)init - (DWORD_PTR)local : 0;
    FreeLibrary(local);
    return rva;
}

/* 远程执行 base+rva(lpParameter=param), 取退出码; 失败返回 -1 */
static int remote_call(HANDLE hp, DWORD_PTR addr, LPVOID param, DWORD *rcOut)
{
    HANDLE th = CreateRemoteThread(hp, NULL, 0, (FnThreadProc)addr, param, 0, NULL);
    if (!th) return -1;
    WaitForSingleObject(th, 10000);
    DWORD rc = 0;
    GetExitCodeThread(th, &rc);
    CloseHandle(th);
    *rcOut = rc;
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 3) { printf("ERR args\n"); return 2; }
    DWORD pid = (DWORD)strtoul(argv[1], NULL, 0);
    wchar_t dllPath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, dllPath, MAX_PATH);

    /* 0) 派生确定性临时副本名(= size+mtime 指纹): 拷贝与驻留扫描共用 */
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
    wchar_t wantLower[MAX_PATH];
    wcscpy(wantLower, wcsrchr(tmpPath, L'\\') ? wcsrchr(tmpPath, L'\\') + 1 : tmpPath);
    _wcslwr(wantLower);

    DWORD rights = PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
                   PROCESS_VM_WRITE | PROCESS_VM_READ | PROCESS_QUERY_INFORMATION;
    HANDLE hp = OpenProcess(rights, FALSE, pid);
    if (!hp) return fail("open", GetLastError());

    /* 1) 驻留复用: 同名副本已在目标里(上次会话注入, BYE 后常驻) —— 直接远程
     *    DxInit 起新 worker, 不拷贝不重载; 设备 vtable 钩还在(detached 闸着),
     *    hook_d3d9 清闸即在世设备无缝续捕。 */
    DWORD_PTR resident = find_module(pid, wantLower);
    if (resident) {
        DWORD_PTR rva = rva_of_init(dllPath);      /* 本地载原文件求 RVA(字节同源) */
        if (!rva) { CloseHandle(hp); return fail("no-export", 0); }
        DWORD rc = 0;
        if (remote_call(hp, resident + rva, NULL, &rc) != 0) {
            CloseHandle(hp);
            return fail("remote-init", GetLastError());
        }
        CloseHandle(hp);
        printf("OK base=0x%llx rc=%lu\n", (unsigned long long)resident, rc);
        return rc ? 3 : 0;
    }

    /* 2) 无驻留: 拷到临时目录再注入 —— 仓内原文件永不进目标进程, 不会被锁;
     *    拷贝名带 size+mtime, 内容变了名字变, 避免 LoadLibrary 同名去重回旧模块 */
    if (!CopyFileW(dllPath, tmpPath, FALSE)) {
        /* 同名副本仍被目标映射(锁死)时换唯一名重试 —— DLL 按内容指纹命名,
         * 内容不变名不变, 而留驻副本在目标存活期间永不释放 */
        wchar_t alt[MAX_PATH];
        _snwprintf(alt, MAX_PATH, L"%s_%X", tmpPath, GetTickCount());
        if (!CopyFileW(dllPath, alt, FALSE)) { CloseHandle(hp); return fail("copy", GetLastError()); }
        wcscpy(tmpPath, alt);
    }
    wcscpy(dllPath, tmpPath);          /* 之后全部走临时副本 */

    DWORD_PTR rva = rva_of_init(dllPath);
    if (!rva) { CloseHandle(hp); return fail("no-export", 0); }

    /* 3) 远程 LoadLibraryW(路径) */
    SIZE_T pathBytes = (wcslen(dllPath) + 1) * sizeof(wchar_t);
    LPVOID remotePath = VirtualAllocEx(hp, NULL, pathBytes, MEM_COMMIT, PAGE_READWRITE);
    if (!remotePath) { CloseHandle(hp); return fail("alloc", GetLastError()); }
    if (!WriteProcessMemory(hp, remotePath, dllPath, pathBytes, NULL)) {
        VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE);
        CloseHandle(hp);
        return fail("write", GetLastError());
    }
    FnLoadLibraryW pLoad = (FnLoadLibraryW)(uintptr_t)
        GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    DWORD rc = 0;
    if (remote_call(hp, (DWORD_PTR)(uintptr_t)pLoad, remotePath, &rc) != 0) {
        VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE);
        CloseHandle(hp);
        return fail("remote-load", GetLastError());
    }

    /* 4) 模块快照拿远程基址(退出码在 x64 会截断, 不用它) */
    wchar_t baseLower[MAX_PATH];
    wcscpy(baseLower, wcsrchr(dllPath, L'\\') ? wcsrchr(dllPath, L'\\') + 1 : dllPath);
    _wcslwr(baseLower);
    DWORD_PTR remoteBase = find_module(pid, baseLower);
    VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE);
    if (!remoteBase) { CloseHandle(hp); return fail("no-remote-base", 0); }

    /* 5) 远程 DxInit */
    if (remote_call(hp, remoteBase + rva, NULL, &rc) != 0) {
        CloseHandle(hp);
        return fail("remote-init", GetLastError());
    }
    CloseHandle(hp);
    printf("OK base=0x%llx rc=%lu\n", (unsigned long long)remoteBase, rc);
    return rc ? 3 : 0;
}
