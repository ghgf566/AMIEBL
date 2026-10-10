#include "pch.h"
#include "WindowsLifecycle.h"
#include <bcrypt.h>
#include <commctrl.h>
#include <iomanip>
#include <sstream>
namespace amiebl {
static std::wstring Identity(std::filesystem::path const &path) {
    auto text = path.wstring();
    int length = LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_UPPERCASE, text.data(),
                               static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr, 0);
    if (!length)
        winrt::throw_last_error();
    std::wstring upper(length, L'\0');
    if (!LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_UPPERCASE, text.data(),
                       static_cast<int>(text.size()), upper.data(), length, nullptr, nullptr, 0))
        winrt::throw_last_error();
    auto utf8 = winrt::to_string(winrt::hstring(upper));
    BYTE hash[32];
    auto status =
        BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0, reinterpret_cast<PUCHAR>(utf8.data()),
                   static_cast<ULONG>(utf8.size()), hash, sizeof(hash));
    if (status < 0)
        throw winrt::hresult_error(E_FAIL, L"無法建立視窗識別。");
    std::wostringstream s;
    s << std::hex << std::uppercase << std::setfill(L'0');
    for (int i = 0; i < 10; i++)
        s << std::setw(2) << static_cast<int>(hash[i]);
    return s.str();
}
WindowsLifecycle::WindowsLifecycle(HWND owner, std::filesystem::path const &data,
                                   std::function<void(int)> callback)
    : hwnd(owner), action(std::move(callback)) {
    auto hash = Identity(data);
    mutex = CreateMutexW(nullptr, TRUE, (L"Local\\LocalModelManager_" + hash).c_str());
    if (!mutex)
        winrt::throw_last_error();
    primary = GetLastError() != ERROR_ALREADY_EXISTS;
    signal =
        CreateEventW(nullptr, FALSE, FALSE, (L"Local\\LocalModelManager_Show_" + hash).c_str());
    if (!signal) {
        CloseHandle(mutex);
        mutex = nullptr;
        winrt::throw_last_error();
    }
    if (!primary) {
        SetEvent(signal);
        return;
    }
    stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!stop) {
        CloseHandle(signal);
        ReleaseMutex(mutex);
        CloseHandle(mutex);
        signal = nullptr;
        mutex = nullptr;
        winrt::throw_last_error();
    }
    listener = std::thread([this] {
        HANDLE handles[] = {stop, signal};
        while (WaitForMultipleObjects(2, handles, FALSE, INFINITE) == WAIT_OBJECT_0 + 1)
            action(0);
    });
    taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
    if (!SetWindowSubclass(hwnd, Subclass, 61, reinterpret_cast<DWORD_PTR>(this))) {
        SetEvent(stop);
        listener.join();
        CloseHandle(stop);
        CloseHandle(signal);
        ReleaseMutex(mutex);
        CloseHandle(mutex);
        stop = signal = mutex = nullptr;
        throw winrt::hresult_error(E_FAIL, L"無法建立系統匣視窗回呼。");
    }
}
WindowsLifecycle::~WindowsLifecycle() {
    if (stop)
        SetEvent(stop);
    if (listener.joinable())
        listener.join();
    if (tray) {
        NOTIFYICONDATAW n{};
        n.cbSize = sizeof(n);
        n.hWnd = hwnd;
        n.uID = 1;
        Shell_NotifyIconW(NIM_DELETE, &n);
    }
    if (primary && hwnd)
        RemoveWindowSubclass(hwnd, Subclass, 61);
    if (icon)
        DestroyIcon(icon);
    if (stop)
        CloseHandle(stop);
    if (signal)
        CloseHandle(signal);
    if (mutex) {
        if (primary)
            ReleaseMutex(mutex);
        CloseHandle(mutex);
    }
}
void WindowsLifecycle::AddTray() {
    if (!primary)
        return;
    if (!icon) {
        wchar_t path[32768];
        GetModuleFileNameW(nullptr, path, 32768);
        auto file = std::filesystem::path(path).parent_path() / L"assets" / L"manager.ico";
        icon = static_cast<HICON>(
            LoadImageW(nullptr, file.c_str(), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));
        if (!icon)
            icon = CopyIcon(LoadIconW(nullptr, IDI_APPLICATION));
    }
    NOTIFYICONDATAW n{};
    n.cbSize = sizeof(n);
    n.hWnd = hwnd;
    n.uID = 1;
    n.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    n.uCallbackMessage = TrayMessage;
    n.hIcon = icon;
    wcscpy_s(n.szTip, L"AMIEBL");
    tray = Shell_NotifyIconW(NIM_ADD, &n) != FALSE;
    if (!tray)
        throw winrt::hresult_error(E_FAIL, L"無法建立系統匣圖示；視窗將保持可見。");
}
void WindowsLifecycle::Update(winrt::hstring const &text) {
    if (!tray)
        return;
    NOTIFYICONDATAW n{};
    n.cbSize = sizeof(n);
    n.hWnd = hwnd;
    n.uID = 1;
    n.uFlags = NIF_TIP;
    wcsncpy_s(n.szTip, text.c_str(), 63);
    Shell_NotifyIconW(NIM_MODIFY, &n);
}
void WindowsLifecycle::Reveal() {
    ShowWindow(hwnd, SW_SHOW);
    if (IsIconic(hwnd))
        ShowWindow(hwnd, SW_RESTORE);
    SetForegroundWindow(hwnd);
}
LRESULT CALLBACK WindowsLifecycle::Subclass(HWND hwnd, UINT message, WPARAM w, LPARAM l, UINT_PTR,
                                            DWORD_PTR data) {
    auto self = reinterpret_cast<WindowsLifecycle *>(data);
    try {
        if (message == self->taskbarCreated) {
            self->tray = false;
            self->AddTray();
            return 0;
        }
        if (message == TrayMessage) {
            if (l == WM_LBUTTONDBLCLK) {
                self->action(0);
                return 0;
            }
            if (l == WM_RBUTTONUP || l == WM_CONTEXTMENU) {
                auto menu = CreatePopupMenu();
                if (!menu)
                    return 0;
                wchar_t const *labels[] = {L"開啟主視窗",
                    self->modelLoaded ? L"載入預設模型（已有模型載入）" : L"載入預設模型（尚未載入）",
                    self->modelBusy ? L"模型載入／卸載中…" : self->modelLoaded ? L"卸載模型" : L"卸載模型（已卸載）",
                    self->keepLoaded ? L"保持載入：已開啟（點擊恢復自動卸載）" : L"保持載入：已關閉（點擊開啟）",
                    self->accepting ? L"接受請求：已開啟（點擊暫停）" : L"接受請求：已暫停（點擊恢復）",
                    L"完全結束"};
                for (int i = 0; i < 6; i++) {
                    UINT flags=MF_STRING;
                    if ((i==3 && self->keepLoaded) || (i==4 && self->accepting)) flags|=MF_CHECKED;
                    if ((i==1 && self->modelBusy) || (i==2 && (!self->modelLoaded || self->modelBusy))) flags|=MF_GRAYED;
                    AppendMenuW(menu, flags, i + 1, labels[i]);
                }
                POINT point;
                GetCursorPos(&point);
                SetForegroundWindow(hwnd);
                auto chosen = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x,
                                             point.y, 0, hwnd, nullptr);
                DestroyMenu(menu);
                PostMessageW(hwnd, WM_NULL, 0, 0);
                if (chosen)
                    self->action(chosen - 1);
                return 0;
            }
        }
    } catch (...) {
        ShowWindow(hwnd, SW_SHOW);
    }
    return DefSubclassProc(hwnd, message, w, l);
}
} // namespace amiebl
