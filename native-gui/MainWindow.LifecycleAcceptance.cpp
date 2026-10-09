#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
namespace winrt::AMIEBL::Native::implementation {
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
IAsyncAction MainWindow::LifecycleAcceptance() {
    auto check = [](bool ok, hstring message) {
        if (!ok)
            throw hresult_error(E_FAIL, message);
    };
    check(lifecycle && lifecycle->primary && lifecycle->tray, L"單例或系統匣沒有建立。");
    JsonObject ready;
    ready.SetNamedValue(L"hwnd", JsonValue::CreateNumberValue(static_cast<double>(
                                     reinterpret_cast<uintptr_t>(lifecycle->hwnd))));
    ready.SetNamedValue(L"hidden_start",
                        JsonValue::CreateBooleanValue(!IsWindowVisible(lifecycle->hwnd)));
    {
        std::ofstream file(core->dataDir / L"lifecycle-ready.json");
        file << to_string(ready.Stringify());
    }
    // The external test sends WM_CLOSE and starts a second real GUI process.
    for (int i = 0; i < 300 && !std::filesystem::exists(core->dataDir / L"lifecycle-hidden.txt");
         i++) {
        co_await resume_after(std::chrono::milliseconds(100));
        co_await ResumeUI{DispatcherQueue()};
    }
    check(std::filesystem::exists(core->dataDir / L"lifecycle-hidden.txt") &&
              !IsWindowVisible(lifecycle->hwnd),
          L"關閉視窗沒有留在背景。");
    {
        std::ofstream file(core->dataDir / L"lifecycle-hidden-confirmed.txt");
        file << "ready";
    }
    for (int i = 0; i < 300 && revealCount == 0; i++) {
        co_await resume_after(std::chrono::milliseconds(100));
        co_await ResumeUI{DispatcherQueue()};
    }
    check(revealCount > 0 && IsWindowVisible(lifecycle->hwnd), L"第二次啟動沒有喚醒原視窗。");
    {
        std::ofstream file(core->dataDir / L"lifecycle-revealed.txt");
        file << "ready";
    }
    for (int i = 0;
         i < 300 && !std::filesystem::exists(core->dataDir / L"lifecycle-core-stopped.txt"); i++) {
        co_await resume_after(std::chrono::milliseconds(100));
        co_await ResumeUI{DispatcherQueue()};
    }
    check(std::filesystem::exists(core->dataDir / L"lifecycle-core-stopped.txt"),
          L"沒有收到隔離服務退出訊號。");
    ShowPage(L"模型庫");
    inputs.at(L"context").as<Microsoft::UI::Xaml::Controls::TextBox>().Text(L"4096");
    auto draft = editor;
    co_await Reconnect();
    check(editor == draft && editor->dirty && editor->Field(L"context").text == L"4096",
          L"重新連線遺失草稿。");
    check(core->process && !core->OwnedExited() &&
              flag(co_await core->Request(L"GET", L"/health"), L"ok"),
          L"服務退出後沒有恢復自有 Core。");
    editor->dirty = false;
    ShowPage(L"總覽");
    JsonObject result;
    result.SetNamedValue(L"ok", JsonValue::CreateBooleanValue(true));
    for (auto key : {L"tray_registered", L"close_to_background", L"single_instance_reveal",
                     L"reconnect_preserved_draft", L"owned_recovery"})
        result.SetNamedValue(key, JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"ui_parity_verified", JsonValue::CreateBooleanValue(false));
    TestResult(result);
}
} // namespace winrt::AMIEBL::Native::implementation
