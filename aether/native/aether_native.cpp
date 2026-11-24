// THAMBI native helper - Windows-only, no external dependencies.
// Performance-critical file enumeration and safe ShellExecute launching.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <bcrypt.h>
#include <string>
#include <vector>
#include <cstdio>
#include <cstdint>
#include <cwchar>

#pragma comment(lib, "bcrypt.lib")

static std::string utf8(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), s.data(), n, nullptr, nullptr);
    return s;
}

static std::string json_escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 16);
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                else o += (char)c;
        }
    }
    return o;
}

static bool skip_dir(const std::wstring& name) {
    static const wchar_t* bad[] = {
        L".git", L".hg", L".svn", L"node_modules", L"__pycache__", L".venv", L"venv",
        L"env", L".tox", L".mypy_cache", L".pytest_cache", L"build", L"dist",
        L"target", L"bin", L"obj", L".next", L".cache", L"AppData"
    };
    for (auto b : bad) if (_wcsicmp(name.c_str(), b) == 0) return true;
    return false;
}

struct ScanStats { uint64_t files = 0; uint64_t bytes = 0; };

static void scan_dir(const std::wstring& dir, int depth, int max_depth,
                     uint64_t max_files, ScanStats& st, bool emit) {
    if (depth > max_depth || st.files >= max_files) return;
    std::wstring pattern = dir + L"\\*";
    WIN32_FIND_DATAW fd{};
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (st.files >= max_files) break;
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        std::wstring full = dir + L"\\" + fd.cFileName;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!skip_dir(fd.cFileName)) scan_dir(full, depth + 1, max_depth, max_files, st, emit);
        } else {
            ULARGE_INTEGER sz; sz.LowPart = fd.nFileSizeLow; sz.HighPart = fd.nFileSizeHigh;
            ULARGE_INTEGER mt; mt.LowPart = fd.ftLastWriteTime.dwLowDateTime; mt.HighPart = fd.ftLastWriteTime.dwHighDateTime;
            st.files++; st.bytes += sz.QuadPart;
            if (emit) {
                // FILETIME epoch 1601 -> Unix epoch
                long long unix_ms = (long long)(mt.QuadPart / 10000ULL) - 11644473600000LL;
                std::string p = utf8(full);
                printf("{\"p\":\"%s\",\"s\":%llu,\"m\":%lld}\n",
                       json_escape(p).c_str(), (unsigned long long)sz.QuadPart, unix_ms);
            }
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

static int cmd_scan(int argc, wchar_t** argv) {
    if (argc < 3) { fprintf(stderr, "usage: aether_native scan <dir> [maxDepth] [maxFiles]\n"); return 2; }
    std::wstring root = argv[2];
    int depth = argc > 3 ? _wtoi(argv[3]) : 12;
    uint64_t maxf = argc > 4 ? _wcstoui64(argv[4], nullptr, 10) : 200000;
    ScanStats st;
    scan_dir(root, 0, depth, maxf, st, true);
    fprintf(stderr, "{\"files\":%llu,\"bytes\":%llu}\n", (unsigned long long)st.files, (unsigned long long)st.bytes);
    return 0;
}

static int cmd_disk() {
    wchar_t drives[512]{};
    DWORD n = GetLogicalDriveStringsW(511, drives);
    for (DWORD i = 0; i < n;) {
        std::wstring d(drives + i);
        i += (DWORD)d.size() + 1;
        if (GetDriveTypeW(d.c_str()) != DRIVE_FIXED) continue;
        ULARGE_INTEGER freeb{}, totalb{}, totalfree{};
        if (GetDiskFreeSpaceExW(d.c_str(), &freeb, &totalb, &totalfree)) {
            printf("{\"drive\":\"%s\",\"free\":%llu,\"total\":%llu}\n",
                   json_escape(utf8(d)).c_str(),
                   (unsigned long long)freeb.QuadPart, (unsigned long long)totalb.QuadPart);
        }
    }
    return 0;
}

static int cmd_launch(int argc, wchar_t** argv) {
    if (argc < 3) { fprintf(stderr, "usage: aether_native launch <path> [args]\n"); return 2; }
    std::wstring target = argv[2];
    std::wstring args = argc > 3 ? argv[3] : L"";
    HINSTANCE r = ShellExecuteW(nullptr, L"open", target.c_str(), args.empty() ? nullptr : args.c_str(), nullptr, SW_SHOWNORMAL);
    if ((INT_PTR)r <= 32) { fprintf(stderr, "ShellExecute failed: %lld\n", (long long)(INT_PTR)r); return 1; }
    printf("{\"ok\":true}\n");
    return 0;
}

static int cmd_sha256(int argc, wchar_t** argv) {
    if (argc < 3) { fprintf(stderr, "usage: aether_native sha256 <file>\n"); return 2; }
    HANDLE f = CreateFileW(argv[2], GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) { fprintf(stderr, "open failed\n"); return 1; }
    BCRYPT_ALG_HANDLE alg = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    DWORD objlen = 0, got = 0, hashlen = 0;
    std::vector<unsigned char> obj, digest;
    BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0);
    BCryptGetProperty(alg, BCRYPT_OBJECT_LENGTH, (PUCHAR)&objlen, sizeof objlen, &got, 0);
    BCryptGetProperty(alg, BCRYPT_HASH_LENGTH, (PUCHAR)&hashlen, sizeof hashlen, &got, 0);
    obj.resize(objlen); digest.resize(hashlen);
    BCryptCreateHash(alg, &hash, obj.data(), objlen, nullptr, 0, 0);
    std::vector<unsigned char> buf(1 << 20);
    DWORD read = 0;
    while (ReadFile(f, buf.data(), (DWORD)buf.size(), &read, nullptr) && read > 0)
        BCryptHashData(hash, buf.data(), read, 0);
    BCryptFinishHash(hash, digest.data(), hashlen, 0);
    for (unsigned char c : digest) printf("%02x", c);
    printf("\n");
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(alg, 0); CloseHandle(f);
    return 0;
}

int wmain(int argc, wchar_t** argv) {
    SetConsoleOutputCP(CP_UTF8);
    if (argc < 2) {
        fprintf(stderr, "THAMBI native helper\ncommands: scan <dir> [depth] [max]\n          disk\n          launch <path> [args]\n          sha256 <file>\n");
        return 2;
    }
    std::wstring cmd = argv[1];
    if (cmd == L"scan") return cmd_scan(argc, argv);
    if (cmd == L"disk") return cmd_disk();
    if (cmd == L"launch") return cmd_launch(argc, argv);
    if (cmd == L"sha256") return cmd_sha256(argc, argv);
    fprintf(stderr, "unknown command\n");
    return 2;
}
