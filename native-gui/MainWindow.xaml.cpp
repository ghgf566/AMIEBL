#include "pch.h"
#include "MainWindow.xaml.h"
#include <microsoft.ui.xaml.window.h>
#if __has_include("MainWindow.g.cpp")
#include "MainWindow.g.cpp"
#endif
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Microsoft::UI::Xaml::Media;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
struct ControlCommand : implements<ControlCommand, Microsoft::UI::Xaml::Input::ICommand> {
    std::function<void()> execute;
    std::function<bool()> enabled;
    event<EventHandler<IInspectable>> changed;
    ControlCommand(std::function<void()> run, std::function<bool()> canRun)
        : execute(std::move(run)), enabled(std::move(canRun)) {}
    bool CanExecute(IInspectable const &) { return enabled(); }
    void Execute(IInspectable const &) {
        if (enabled())
            execute();
    }
    event_token CanExecuteChanged(EventHandler<IInspectable> const &handler) {
        return changed.add(handler);
    }
    void CanExecuteChanged(event_token const &token) noexcept { changed.remove(token); }
};
MainWindow::MainWindow() {
    InitializeComponent();
    VersionText().Text(L"v1.1.0-dev");
    TitleText().Text(L"正在啟動");
    AppWindow().Resize({1240, 860});
    Navigation().RegisterPropertyChangedCallback(
        NavigationView::IsPaneOpenProperty(), [weak = get_weak()](auto const &, auto const &) {
            if (auto s = weak.get())
                s->PaneBrandFooter().Visibility(
                    s->Navigation().IsPaneOpen() ? Visibility::Visible : Visibility::Collapsed);
        });
    Root().SizeChanged([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->UpdateIndicator();
    });
    Root().Loaded([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->UpdateIndicator();
    });
    Navigation().PaneOpened([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->UpdateIndicator();
    });
    Navigation().PaneClosed([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->UpdateIndicator();
    });
    AppWindow().Closing([weak = get_weak()](
                            auto const &,
                            Microsoft::UI::Windowing::AppWindowClosingEventArgs const &e) {
        if (auto s = weak.get())
            if (!s->exiting) {
                e.Cancel(true);
                if (s->lifecycle && s->lifecycle->tray && flag(s->config, L"close_to_tray", true))
                    s->AppWindow().Hide();
                else
                    s->Run([s] { return s->Exit(); });
            }
    });
    timer.Interval(std::chrono::milliseconds(1500));
    timer.Tick([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->PollTick();
    });
}
void MainWindow::Start() { StartAsync(); }
fire_and_forget MainWindow::StartAsync() {
    auto lifetime = get_strong();
    try {
        core = std::make_shared<CoreClient>();
        HWND hwnd = nullptr;
        check_hresult(get_strong().as<IWindowNative>()->get_WindowHandle(&hwnd));
        lifecycle = std::make_unique<WindowsLifecycle>(
            hwnd, core->dataDir, [weak = get_weak(), queue = DispatcherQueue()](int command) {
                queue.TryEnqueue([weak, command] {
                    if (auto s = weak.get()) {
                        if (command == 0) {
                            s->Reveal();
                            return;
                        }
                        s->Run([s, command]() -> IAsyncAction {
                            if (command == 1)
                                co_await s->LoadDefault();
                            else if (command == 2)
                                co_await s->Unload();
                            else if (command == 3)
                                co_await s->ToggleKeep();
                            else if (command == 4)
                                co_await s->ToggleAccepting();
                            else if (command == 5)
                                co_await s->Exit();
                        });
                    }
                });
            });
        if (!lifecycle->primary) {
            exiting = true;
            Close();
            co_return;
        }
        co_await core->Connect();
        config = co_await core->Request(L"GET", L"/manager/config");
        status = co_await core->Request(L"GET", L"/manager/status");
        connected = true;
        lifecycle->AddTray();
        selecting = true;
        Navigation().SelectedItem(Navigation().MenuItems().GetAt(0));
        selecting = false;
        ShowPage(L"總覽");
        if (CoreClient::HasFlag(L"--background") || flag(config, L"start_hidden"))
            AppWindow().Hide();
        timer.Start();
        co_await Poll();
        if (CoreClient::Option(L"--gui-test") == L"1" ||
            CoreClient::Option(L"--engine-test") == L"1" ||
            CoreClient::Option(L"--editor-test") == L"1" ||
            CoreClient::Option(L"--lifecycle-test") == L"1") {
            timer.Stop();
            if (CoreClient::Option(L"--engine-test") == L"1")
                co_await EnginesAcceptance();
            else if (CoreClient::Option(L"--lifecycle-test") == L"1")
                co_await LifecycleAcceptance();
            else if (CoreClient::Option(L"--editor-test") == L"1")
                co_await EditorAcceptance();
            else
                co_await GuiAcceptance();
        }
    } catch (hresult_error const &e) {
        Error(e.message());
        Reveal();
        Notice().ActionButton(Action(L"重新連接", [this] { return Reconnect(); }));
    } catch (std::exception const &e) {
        Error(to_hstring(e.what()));
        Reveal();
    }
    if (CoreClient::Option(L"--gui-test") == L"1" || CoreClient::Option(L"--engine-test") == L"1" || CoreClient::Option(L"--editor-test") == L"1" ||
        CoreClient::Option(L"--lifecycle-test") == L"1") {
        if (Notice().Severity() == InfoBarSeverity::Error) {
            JsonObject result;
            result.SetNamedValue(L"ok", JsonValue::CreateBooleanValue(false));
            result.SetNamedValue(L"error", JsonValue::CreateStringValue(Notice().Message()));
            TestResult(result);
        }
        exiting = true;
        timer.Stop();
        if (core)
            co_await core->Shutdown();
        lifecycle.reset();
        Close();
    }
}
fire_and_forget MainWindow::PollTick() {
    auto lifetime = get_strong();
    co_await Poll();
}
TextBlock MainWindow::Text(hstring text, double size) {
    TextBlock t;
    t.Text(text);
    t.IsTextSelectionEnabled(true);
    t.Foreground(SolidColorBrush(Windows::UI::Color{255, 240, 243, 249}));
    t.FontSize(size);
    t.TextWrapping(TextWrapping::Wrap);
    t.Margin({0, 0, 0, 8});
    return t;
}
StackPanel MainWindow::Panel() {
    StackPanel p;
    p.Spacing(12);
    return p;
}
StackPanel MainWindow::Row(std::initializer_list<UIElement> controls) {
    auto p = Panel();
    p.Orientation(Orientation::Horizontal);
    p.Spacing(8);
    for (auto c : controls)
        p.Children().Append(c);
    return p;
}
ScrollViewer MainWindow::Scroll(UIElement content) {
    ScrollViewer s;
    s.Content(content);
    s.VerticalScrollBarVisibility(ScrollBarVisibility::Auto);
    s.HorizontalScrollBarVisibility(ScrollBarVisibility::Disabled);
    return s;
}
Border MainWindow::Card(hstring title, UIElement content) {
    Grid body;
    body.RowSpacing(12);
    RowDefinition a;
    a.Height(GridLengthHelper::Auto());
    body.RowDefinitions().Append(a);
    RowDefinition b;
    b.Height({1, GridUnitType::Star});
    body.RowDefinitions().Append(b);
    body.Children().Append(Text(title, 18));
    Grid::SetRow(content.as<FrameworkElement>(), 1);
    body.Children().Append(content);
    Border card;
    card.Child(body);
    card.Padding({20, 20, 20, 20});
    card.CornerRadius({12, 12, 12, 12});
    card.BorderThickness({1, 1, 1, 1});
    card.BorderBrush(SolidColorBrush(Windows::UI::Color{255, 57, 65, 80}));
    card.Background(SolidColorBrush(Windows::UI::Color{255, 29, 35, 46}));
    return card;
}
Button MainWindow::Action(hstring label, ActionTask action) {
    Button b;
    b.Content(box_value(label));
    b.Margin({0, 0, 8, 4});
    auto command = make<ControlCommand>(
        [weak = get_weak(), action] {
            if (auto s = weak.get())
                s->Run(action);
        },
        [weak = get_weak()] {
            auto s = weak.get();
            return s && !s->working && !s->exiting;
        });
    commands.push_back(make_weak(command));
    b.Command(command);
    return b;
}
fire_and_forget MainWindow::Run(ActionTask action) {
    auto lifetime = get_strong();
    if (working || exiting)
        co_return;
    working = true;
    RefreshCommands();
    Navigation().IsEnabled(false);
    PageHost().IsEnabled(false);
    try {
        co_await action();
    } catch (hresult_error const &e) {
        Error(e.message());
    } catch (std::exception const &e) {
        Error(to_hstring(e.what()));
    }
    working = false;
    Navigation().IsEnabled(true);
    PageHost().IsEnabled(true);
    RefreshCommands();
}
void MainWindow::RefreshCommands() {
    std::vector<weak_ref<Microsoft::UI::Xaml::Input::ICommand>> alive;
    for (auto const &weak : commands)
        if (auto command = weak.get()) {
            alive.push_back(weak);
            get_self<ControlCommand>(command)->changed(command, nullptr);
        }
    commands = std::move(alive);
}
void MainWindow::Error(hstring m) {
    Notice().Severity(InfoBarSeverity::Error);
    Notice().Message(m);
    Notice().IsOpen(true);
}
void MainWindow::Message(hstring m) {
    Notice().Severity(InfoBarSeverity::Informational);
    Notice().Message(m);
    Notice().IsOpen(true);
}
IAsyncOperation<bool> MainWindow::Confirm(hstring text, hstring title) {
    ContentDialog d;
    d.XamlRoot(Root().XamlRoot());
    d.Title(box_value(title));
    d.Content(box_value(text));
    d.PrimaryButtonText(L"確認");
    d.CloseButtonText(L"取消");
    d.DefaultButton(ContentDialogButton::Close);
    co_return co_await d.ShowAsync() == ContentDialogResult::Primary;
}
void MainWindow::Navigate(IInspectable const &,
                          NavigationViewSelectionChangedEventArgs const &args) {
    if (!connected || selecting)
        return;
    if (auto item = args.SelectedItem().try_as<NavigationViewItem>()) {
        auto next = unbox_value<hstring>(item.Tag());
        if (next == page)
            return;
        if (working) {
            RestoreNavigation();
            return;
        }
        if (!editor || !editor->dirty)
            ShowPage(next);
        else
            Run([this, next]() -> IAsyncAction {
                try {
                    if (co_await LeaveEditor())
                        ShowPage(next);
                    else
                        RestoreNavigation();
                } catch (...) {
                    RestoreNavigation();
                    throw;
                }
            });
    }
}
void MainWindow::ShowPage(hstring next) {
    revealingEditor.reset();
    capabilityText = nullptr;
    editor.reset();
    entityList = nullptr;
    entityEditor = nullptr;
    editorScroll = nullptr;
    inputs.clear();
    sliders.clear();
    fieldBlocks.clear();
    editorExpanders.clear();
    entitySearch = L"";
    page = next;
    TitleText().Text(page);
    ExitButton().Visibility(page == L"系統" ? Visibility::Visible : Visibility::Collapsed);
    if (page == L"總覽")
        PageHost().Content(BuildOverview());
    else if (page == L"任務與紀錄")
        PageHost().Content(BuildTasks());
    else if (page == L"模型庫")
        PageHost().Content(BuildEntities(L"models"));
    else if (page == L"使用模式")
        PageHost().Content(BuildEntities(L"profiles"));
    else if (page == L"系統")
        PageHost().Content(BuildSystem());
    else
        PageHost().Content(nullptr);
    UpdateFooter();
    RestoreNavigation();
    UpdateIndicator();
}
void MainWindow::UpdateIndicator() {
    auto item = Navigation().SelectedItem().try_as<NavigationViewItem>();
    if (!item || item.ActualHeight() <= 0)
        return;
    auto p = item.TransformToVisual(Root()).TransformPoint({0, 0});
    auto v =
        Microsoft::UI::Xaml::Hosting::ElementCompositionPreview::GetElementVisual(NavIndicator());
    if (!indicatorInitialized) {
        indicatorInitialized = true;
        v.Offset({p.X, p.Y + static_cast<float>((item.ActualHeight() - 16) / 2), 0});
    }
    if (Windows::UI::ViewManagement::UISettings().AnimationsEnabled() && !v.ImplicitAnimations()) {
        auto a = v.Compositor().CreateVector3KeyFrameAnimation();
        a.Target(L"Offset");
        a.Duration(std::chrono::milliseconds(220));
        a.InsertExpressionKeyFrame(0, L"this.StartingValue");
        a.InsertExpressionKeyFrame(1, L"this.FinalValue");
        auto c = v.Compositor().CreateImplicitAnimationCollection();
        c.Insert(L"Offset", a);
        v.ImplicitAnimations(c);
    } else if (!Windows::UI::ViewManagement::UISettings().AnimationsEnabled())
        v.ImplicitAnimations(nullptr);
    NavIndicator().Opacity(1);
    v.Offset({p.X, p.Y + static_cast<float>((item.ActualHeight() - 16) / 2), 0});
}
hstring MainWindow::ApplicationState() {
    return flag(status, L"pending_restart")        ? L"已儲存，等待管理器重啟"
           : flag(status, L"pending_model_reload") ? L"已儲存，等待模型重新載入"
                                                   : L"已儲存；引擎載入設定無待處理差異";
}
void MainWindow::UpdateFooter() {
    Footer().Text((editor && editor->dirty ? hstring(L"編輯中，尚未儲存　·　") : hstring()) +
                  ApplicationState() + L"　·　http://127.0.0.1:" +
                  to_hstring(core ? core->port : 8080));
}
IAsyncAction MainWindow::Poll() {
    if (!core || polling || exiting)
        co_return;
    polling = true;
    auto revision = configRevision;
    try {
        if (core->OwnedExited())
            throw hresult_error(E_FAIL, L"背景服務異常退出；目前資料與紀錄保留，請重新啟動連線。");
        auto s = co_await core->Request(L"GET", L"/manager/status");
        auto r = co_await core->Request(L"GET", L"/manager/requests");
        if (revision != configRevision) {
            polling = false;
            co_return;
        }
        status = s;
        requests = array(r, L"requests");
        if (page == L"任務與紀錄") {
            auto result = co_await core->Request(L"GET", L"/manager/logs");
            logs = L"";
            for (auto v : array(result, L"lines")) {
                if (!logs.empty())
                    logs = logs + L"\n";
                logs = logs + v.GetString();
            }
            UpdateTasks();
        }
        if (page == L"總覽") {
            engineRuntime = co_await core->Request(L"GET",L"/manager/engines/runtime");
            UpdateOverview();
        }
        if (page == L"系統" && engineInfo)
            co_await RefreshEngines();
        UpdateFooter();
        if (lifecycle)
            lifecycle->Update(L"AMIEBL · " + str(status, L"state"));
        if (!working) {
            auto fresh = co_await core->Request(L"GET", L"/manager/config");
            if (!working && revision == configRevision) {
                config = fresh;
                configRevision++;
                if (capabilityText && !selectedModel.empty())
                    capabilityText.Text(ModelCapability(selectedModel));
            }
        }
    } catch (hresult_error const &e) {
        Error(e.message());
        Notice().ActionButton(Action(L"重新連接", [this] { return Reconnect(); }));
    }
    polling = false;
}
IAsyncAction MainWindow::LoadDefault() {
    co_await core->Request(
        L"POST", L"/manager/load",
        body(L"model_id", JsonValue::CreateStringValue(str(config, L"default_model_id"))));
    co_await Poll();
}
IAsyncAction MainWindow::Unload() {
    co_await core->Request(L"POST", L"/manager/unload");
    co_await Poll();
}
IAsyncAction MainWindow::ToggleAccepting() {
    co_await core->Request(
        L"POST", L"/manager/accepting",
        body(L"accepting", JsonValue::CreateBooleanValue(!flag(status, L"accepting", true))));
    co_await Poll();
}
IAsyncAction MainWindow::ToggleKeep() {
    auto id = str(status, L"model_id", str(config, L"default_model_id"));
    auto b = body(L"model_id", JsonValue::CreateStringValue(id));
    b.SetNamedValue(L"keep_loaded", JsonValue::CreateBooleanValue(
                                        !flag(entity(config, L"models", id), L"keep_loaded")));
    co_await core->Request(L"POST", L"/manager/keep-loaded", b);
    config = co_await core->Request(L"GET", L"/manager/config");
}
IAsyncAction MainWindow::ConnectVSCode() {
    if (!(co_await LeaveEditor()))
        co_return;
    auto p = co_await core->Request(L"GET", L"/manager/vscode/preview");
    if (co_await Confirm(str(p, L"summary", L"更新 VS Code 設定並備份既有內容？"),
                         L"同步至 VS Code")) {
        auto r = co_await core->Request(L"POST", L"/manager/vscode/apply");
        Message(str(r, L"message", L"已更新，請重新載入 VS Code 視窗。"));
    }
}
IAsyncAction MainWindow::Exit() {
    if (!(co_await LeaveEditor()))
        co_return;
    if (number(status, L"active_count") + number(status, L"queued_count") > 0 &&
        !(co_await Confirm(L"完全結束會取消目前任務並停止此程式啟動的服務。")))
        co_return;
    exiting = true;
    timer.Stop();
    if (core)
        co_await core->Shutdown();
    lifecycle.reset();
    Close();
}
void MainWindow::Reveal() {
    revealCount++;
    if (lifecycle)
        lifecycle->Reveal();
    AppWindow().Show();
    Activate();
}
IAsyncAction MainWindow::Reconnect() {
    SnapshotEditorControls();
    // Reuse owned/attached rules. Never replace an incompatible listener and
    // never clear an active form while restoring transport.
    co_await core->Connect();
    config = co_await core->Request(L"GET", L"/manager/config");
    configRevision++;
    connected = true;
    co_await Poll();
    Message(L"已重新連接本機服務。");
}
void MainWindow::ExitClicked(IInspectable const &, RoutedEventArgs const &) {
    Run([this] { return Exit(); });
}
hstring MainWindow::Phase(hstring p) {
    if (p == L"completed")
        return L"已完成";
    if (p == L"cancelled")
        return L"已取消";
    if (p == L"error")
        return L"錯誤";
    if (p == L"queued")
        return L"等待中";
    if (p == L"generating")
        return L"生成中";
    if (p == L"thinking")
        return L"思考中";
    if (p == L"processing")
        return L"處理上下文";
    return p;
}
} // namespace winrt::AMIEBL::Native::implementation
