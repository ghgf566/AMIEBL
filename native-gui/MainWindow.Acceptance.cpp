#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
#include <winrt/Microsoft.UI.Xaml.Automation.Peers.h>
#include <winrt/Microsoft.UI.Xaml.Automation.Provider.h>
#include <winrt/Microsoft.UI.Xaml.Media.Imaging.h>
#include <winrt/Windows.Graphics.Imaging.h>
#include <winrt/Windows.Storage.Streams.h>
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
static Button FindButton(DependencyObject root, hstring label) {
    if (auto b = root.try_as<Button>())
        if (b.Content() && unbox_value_or<hstring>(b.Content(), L"") == label)
            return b;
    for (int i = 0; i < Microsoft::UI::Xaml::Media::VisualTreeHelper::GetChildrenCount(root); i++) {
        auto b = FindButton(Microsoft::UI::Xaml::Media::VisualTreeHelper::GetChild(root, i), label);
        if (b)
            return b;
    }
    return nullptr;
}
void MainWindow::TestResult(JsonObject const &result) {
    auto dir = CoreClient::Option(L"--data-dir");
    if (dir.empty())
        return;
    std::ofstream f(std::filesystem::path(dir) / L"gui-e2e.json");
    f << to_string(result.Stringify());
}
IAsyncAction MainWindow::InvokeButton(hstring label) {
    Root().UpdateLayout();
    auto button = FindButton(Root(), label);
    if (!button)
        for (auto popup : Microsoft::UI::Xaml::Media::VisualTreeHelper::GetOpenPopupsForXamlRoot(
                 Root().XamlRoot())) {
            button = FindButton(popup.Child(), label);
            if (button)
                break;
        }
    if (!button || !button.IsEnabled())
        throw hresult_error(E_FAIL, L"測試找不到可操作按鈕：" + label);
    auto peer = Microsoft::UI::Xaml::Automation::Peers::FrameworkElementAutomationPeer::
        CreatePeerForElement(button);
    peer.GetPattern(Microsoft::UI::Xaml::Automation::Peers::PatternInterface::Invoke)
        .as<Microsoft::UI::Xaml::Automation::Provider::IInvokeProvider>()
        .Invoke();
    co_await resume_after(std::chrono::milliseconds(50));
    co_await ResumeUI{DispatcherQueue()};
    for (int i = 0; i < 400 && working; i++) {
        co_await resume_after(std::chrono::milliseconds(50));
        co_await ResumeUI{DispatcherQueue()};
    }
    if (working)
        throw hresult_error(E_FAIL, L"按鈕操作逾時：" + label);
    if (Notice().IsOpen() && Notice().Severity() == InfoBarSeverity::Error)
        throw hresult_error(E_FAIL, Notice().Message());
}
IAsyncAction MainWindow::Capture(hstring name) {
    Root().UpdateLayout();
    co_await resume_after(std::chrono::milliseconds(350));
    co_await ResumeUI{DispatcherQueue()};
    Microsoft::UI::Xaml::Media::Imaging::RenderTargetBitmap bitmap;
    co_await bitmap.RenderAsync(Root());
    auto pixels = co_await bitmap.GetPixelsAsync();
    Windows::Storage::Streams::InMemoryRandomAccessStream stream;
    auto encoder = co_await Windows::Graphics::Imaging::BitmapEncoder::CreateAsync(
        Windows::Graphics::Imaging::BitmapEncoder::PngEncoderId(), stream);
    auto reader = Windows::Storage::Streams::DataReader::FromBuffer(pixels);
    std::vector<uint8_t> bytes(pixels.Length());
    reader.ReadBytes(bytes);
    encoder.SetPixelData(Windows::Graphics::Imaging::BitmapPixelFormat::Bgra8,
                         Windows::Graphics::Imaging::BitmapAlphaMode::Premultiplied,
                         bitmap.PixelWidth(), bitmap.PixelHeight(), 96, 96, bytes);
    co_await encoder.FlushAsync();
    stream.Seek(0);
    Windows::Storage::Streams::DataReader output(stream.GetInputStreamAt(0));
    co_await output.LoadAsync(static_cast<uint32_t>(stream.Size()));
    bytes.resize(static_cast<size_t>(stream.Size()));
    output.ReadBytes(bytes);
    std::ofstream file(core->dataDir / (std::wstring(name) + L".png"), std::ios::binary);
    file.write(reinterpret_cast<char const *>(bytes.data()),
               static_cast<std::streamsize>(bytes.size()));
}
IAsyncAction MainWindow::GuiAcceptance() {
    auto check = [](bool passed, hstring text) {
        if (!passed)
            throw hresult_error(E_FAIL, text);
    };
    // Real controls/transport only. Test fixture must explicitly isolate data.
    check(core && connected, L"Core 未連線。");
    check(array(config, L"models").Size() > 0, L"缺少模型測試資料。");
    co_await InvokeButton(L"載入預設模型");
    for (int i = 0; i < 100 && str(status, L"state") != L"ready"; i++) {
        co_await resume_after(std::chrono::milliseconds(100));
        co_await ResumeUI{DispatcherQueue()};
        co_await Poll();
    }
    check(str(status, L"state") == L"ready",
          L"GUI 模型載入沒有就緒：" + str(status, L"state") + L" · " + str(status, L"last_error"));
    co_await InvokeButton(L"暫停／恢復接收");
    check(!flag(status, L"accepting", true), L"GUI 沒有暫停接收。");
    check(!lifecycle->accepting,L"系統匣接收狀態沒有同步。");
    co_await InvokeButton(L"暫停／恢復接收");
    check(flag(status, L"accepting"), L"GUI 沒有恢復接收。");
    co_await InvokeButton(L"保持載入／恢復卸載");
    check(flag(entity(config, L"models", str(config, L"default_model_id")), L"keep_loaded"),
          L"GUI 保持載入沒有保存。");
    check(lifecycle->keepLoaded && std::wstring_view(overviewState.Text()).find(L"保持載入已開啟")!=std::wstring_view::npos,L"保持載入狀態沒有顯示。");
    co_await InvokeButton(L"保持載入／恢復卸載");
    check(!lifecycle->keepLoaded,L"系統匣保持載入關閉狀態沒有同步。");
    co_await InvokeButton(L"卸載模型");
    check(str(status, L"state") == L"unloaded", L"GUI 模型卸載沒有完成。");
    auto content = PageHost().Content();
    overviewScroll.ChangeView(nullptr, 30, nullptr, true);
    co_await resume_after(std::chrono::milliseconds(50));
    co_await ResumeUI{DispatcherQueue()};
    auto offset = overviewScroll.VerticalOffset();
    co_await Poll();
    check(PageHost().Content() == content && std::abs(overviewScroll.VerticalOffset() - offset) < 1,
          L"總覽輪詢重設畫面或捲動。");
    co_await Capture(L"總覽");
    Navigation().IsPaneOpen(false);
    co_await Capture(L"導覽列精簡");
    check(PaneBrandFooter().Visibility() == Visibility::Collapsed, L"精簡導覽列品牌未隱藏。");
    Navigation().IsPaneOpen(true);
    // The orchestrator now sends an actual OpenAI/SSE request to the owned Core.
    {
        std::ofstream f(core->dataDir / L"gui-stage.txt");
        f << "await_request";
    }
    for (int i = 0; i < 200 && number(status, L"active_count") == 0; i++) {
        co_await resume_after(std::chrono::milliseconds(100));
        co_await ResumeUI{DispatcherQueue()};
        co_await Poll();
    }
    check(number(status, L"active_count") == 1, L"未觀察到作用中推理，不能驗證取消。");
    Navigation().SelectedItem(Navigation().MenuItems().GetAt(3));
    co_await Poll();
    Root().UpdateLayout();
    check(taskList && taskList.Items().Size() > 0, L"任務清單未接線。");
    co_await resume_after(std::chrono::milliseconds(100));
    co_await ResumeUI{DispatcherQueue()};
    check(serviceLogExpander && serviceLogExpander->viewport, L"SmoothExpander 未初始化。");
    serviceLogExpander->native.IsExpanded(true);
    co_await resume_after(std::chrono::milliseconds(80));
    co_await ResumeUI{DispatcherQueue()};
    auto intermediate = serviceLogExpander->Height();
    bool animationEnabled = Windows::UI::ViewManagement::UISettings().AnimationsEnabled();
    bool reversing = serviceLogExpander->animating;
    serviceLogExpander->native.IsExpanded(false);
    if (animationEnabled && reversing)
        check(std::abs(serviceLogExpander->from - intermediate) < 1, L"展開反向操作產生高度跳動。");
    else if (!animationEnabled)
        check(serviceLogExpander->Height() < 1 && !serviceLogExpander->animating,
              L"關閉系統動畫時未立即收合。");
    co_await resume_after(std::chrono::milliseconds(350));
    co_await ResumeUI{DispatcherQueue()};
    check(serviceLogExpander->Height() < 1 && !serviceLogExpander->animating,
          L"SmoothExpander 收合未完成。");
    serviceLogExpander->native.IsExpanded(true);
    co_await resume_after(std::chrono::milliseconds(350));
    co_await ResumeUI{DispatcherQueue()};
    check(serviceLogExpander->Height() > 0 && !serviceLogExpander->animating,
          L"SmoothExpander 展開未完成。");
    co_await Capture(L"服務記錄展開");
    serviceLogExpander->native.IsExpanded(false);
    co_await resume_after(std::chrono::milliseconds(350));
    co_await ResumeUI{DispatcherQueue()};

    auto ident = selectedTask;
    co_await Capture(L"任務作用中");
    co_await InvokeButton(L"停止選取任務");
    bool confirmed = false;
    for (int i = 0; i < 100 && !confirmed; i++) {
        co_await Poll();
        for (auto v : requests) {
            auto r = v.GetObject();
            if (str(r, L"id") == ident && str(r, L"phase") == L"cancelled" &&
                flag(r, L"cancel_confirmed"))
                confirmed = true;
        }
        if (!confirmed) {
            co_await resume_after(std::chrono::milliseconds(100));
            co_await ResumeUI{DispatcherQueue()};
        }
    }
    check(confirmed && number(status, L"active_count") == 0, L"GUI 取消沒有確認停止並釋放 Slot。");
    co_await Capture(L"任務已取消");
    JsonObject result;
    result.SetNamedValue(L"ok", JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"overview_controls", JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"task_cancel_confirmed", JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"scroll_preserved", JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"animations_enabled", JsonValue::CreateBooleanValue(animationEnabled));
    result.SetNamedValue(L"expander_reversal_checked",
                         JsonValue::CreateBooleanValue(reversing && animationEnabled));
    result.SetNamedValue(L"protocol", JsonValue::CreateNumberValue(1));
    result.SetNamedValue(L"ui_parity_verified", JsonValue::CreateBooleanValue(false));
    TestResult(result);
}
} // namespace winrt::AMIEBL::Native::implementation
