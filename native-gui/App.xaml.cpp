#include "pch.h"
#include "App.xaml.h"
#include "MainWindow.xaml.h"
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <shobjidl.h>
namespace winrt::AMIEBL::Native::implementation {
App::App() { InitializeComponent(); }
void App::OnLaunched(Microsoft::UI::Xaml::LaunchActivatedEventArgs const &) {
#ifdef AMIEBL_RELEASE_BUILD
    check_hresult(SetCurrentProcessExplicitAppUserModelID(L"AMIEBL.LocalModelManager"));
#else
    check_hresult(SetCurrentProcessExplicitAppUserModelID(L"AMIEBL.Native.Development"));
#endif
    auto native = winrt::make<MainWindow>();
    window = native;
    window.Activate();
    // Startup/resource smoke only. This never claims page or interaction parity.
    if (auto length = GetEnvironmentVariableW(L"AMIEBL_NATIVE_GUI_SMOKE_RESULT", nullptr, 0)) {
        std::wstring result(length, L'\0');
        auto written =
            GetEnvironmentVariableW(L"AMIEBL_NATIVE_GUI_SMOKE_RESULT", result.data(), length);
        if (!written || written >= length)
            return;
        result.resize(written);
        auto path = std::filesystem::path(result);
        smoke_timer = window.DispatcherQueue().CreateTimer();
        smoke_timer.Interval(std::chrono::seconds(1));
        smoke_timer.IsRepeating(false);
        smoke_timer.Tick([this, path](auto const &, auto const &) {
            std::ofstream output(path);
            output << R"({"native_window_started":true,"ui_parity_verified":false})";
            output.close();
            window.Close();
        });
        smoke_timer.Start();
    } else
        winrt::get_self<MainWindow>(native)->Start();
}
} // namespace winrt::AMIEBL::Native::implementation
