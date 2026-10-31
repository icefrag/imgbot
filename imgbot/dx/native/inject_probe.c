/* inject_probe.c — 注入判别探针: 远程 LoadLibraryW 后查模块是否落地.
 * 用法: inject_probe.exe <pid> <dll绝对路径>
 * 输出: LOAD rc=0x... base=0x... found=1   成功
 *       LOAD rc=0x0 base=0x0 found=0       目标内 LoadLibrary 失败(被拦/依赖失败)
 *       ERR <what> gle=<n>                 宿主侧失败
 */
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>

typedef DWORD (WINAPI *FnLoadLibraryW)(LPCWSTR);
typedef DWORD (WINAPI *FnThreadProc)(LPVOID);

static DWORD_PTR find_module(DWORD pid, const wchar_t *lowerName)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    MODULEENTRY32W me; me.dwSize = sizeof(me);
    DWORD_PTR base = 0;
    if (Module32FirstW(&snap, &me)) {
        do {
            wchar_t cur[MAX_PATH];
            wcscpy(cur, me.szModule);
            _wcslwr(cur);
            if (wcscmp(cur, lowerName) == 0) { base = (DWORD_PTR)me.modBaseAddr; break; }
        } while (Module32NextW(&snap, &me));
    }
    CloseHandle(snap);
    return base;
}

int main(int argc, char **argv)
{
    if (argc < 3) { printf("ERR args\n"); return 2; }
    DWORD pid = (DWORD)strtoul(argv[1], NULL, 0);
    wchar_t dllPath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, argv[2], -1, dllPath, MAX_PATH);

    DWORD rights = PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
                   PROCESS_VM_WRITE | PROCESS_VM_READ | PROCESS_QUERY_INFORMATION;
    HANDLE hp = OpenProcess(rights, FALSE, pid);
    if (!hp) { printf("ERR open gle=%lu\n", GetLastError()); return 1; }

    SIZE_T pathBytes = (wcslen(dllPath) + 1) * sizeof(wchar_t);
    LPVOID remotePath = VirtualAllocEx(hp, NULL, pathBytes, MEM_COMMIT, PAGE_READWRITE);
    if (!remotePath) { printf("ERR alloc gle=%lu\n", GetLastError()); CloseHandle(hp); return 1; }
    if (!WriteProcessMemory(hp, remotePath, dllPath, pathBytes, NULL)) {
        printf("ERR write gle=%lu\n", GetLastError());
        VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE); CloseHandle(hp); return 1;
    }
    FnLoadLibraryW pLoad = (FnLoadLibraryW)(uintptr_t)
        GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
    HANDLE th = CreateRemoteThread(hp, NULL, 0, (FnThreadProc)pLoad, remotePath, 0, NULL);
    if (!th) { printf("ERR thread gle=%lu\n", GetLastError());
        VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE); CloseHandle(hp); return 1; }
    WaitForSingleObject(th, 10000);
    DWORD rc = 0;
    GetExitCodeThread(th, &rc);
    CloseHandle(th);
    VirtualFreeEx(hp, remotePath, 0, MEM_RELEASE);

    wchar_t baseLower[MAX_PATH];
    wcscpy(baseLower, wcsrchr(dllPath, L'\\') ? wcsrchr(dllPath, L'\\') + 1 : dllPath);
    _wcslwr(baseLower);
    Sleep(300);                       /* 等加载器把模块挂链 */
    DWORD_PTR base = find_module(pid, baseLower);
    printf("LOAD rc=0x%lx base=0x%llx found=%d\n", rc,
           (unsigned long long)base, base ? 1 : 0);
    CloseHandle(hp);
    return 0;
}
