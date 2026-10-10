#pragma once
#include <filesystem>
#include <functional>
#include <shellapi.h>
#include <thread>
#include <windows.h>
namespace amiebl {
// GUI-only Win32 integration. It never discovers or terminates other services.
struct WindowsLifecycle {
    HWND hwnd{};
    HANDLE mutex{}, signal{}, stop{};
    std::thread listener;
    std::function<void(int)> action;
    bool primary = false, tray = false;
    bool modelLoaded = false, modelBusy = false, keepLoaded = false, accepting = true;
    HICON icon{};
    UINT taskbarCreated{};
    static constexpr UINT TrayMessage = WM_APP + 61;
    WindowsLifecycle(HWND owner, std::filesystem::path const &data,
                     std::function<void(int)> callback);
    ~WindowsLifecycle();
    void AddTray();
    void Update(winrt::hstring const &text);
    void Reveal();
    static LRESULT CALLBACK Subclass(HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR);
};
} // namespace amiebl
