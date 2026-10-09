#pragma once
#include "MainWindow.g.h"
#include "CoreClient.h"
#include "SmoothExpander.h"
namespace winrt::AMIEBL::Native::implementation {
struct MainWindow : MainWindowT<MainWindow> {
    MainWindow();
    void Start();
    void Navigate(Windows::Foundation::IInspectable const &,
                  Microsoft::UI::Xaml::Controls::NavigationViewSelectionChangedEventArgs const &);
    void ExitClicked(Windows::Foundation::IInspectable const &,
                     Microsoft::UI::Xaml::RoutedEventArgs const &);

  private:
    std::shared_ptr<amiebl::CoreClient> core;
    Windows::Data::Json::JsonObject config, status;
    Windows::Data::Json::JsonArray requests;
    Microsoft::UI::Xaml::DispatcherTimer timer;
    Microsoft::UI::Xaml::Controls::TextBlock overviewModel{nullptr}, overviewState{nullptr},
        overviewQueue{nullptr}, overviewMemory{nullptr}, overviewConnection{nullptr},
        overviewError{nullptr};
    Microsoft::UI::Xaml::Controls::Border overviewErrorCard{nullptr};
    Microsoft::UI::Xaml::Controls::ScrollViewer overviewScroll{nullptr};
    Microsoft::UI::Xaml::Controls::ListView taskList{nullptr};
    Microsoft::UI::Xaml::Controls::ContentControl taskDetail{nullptr};
    Microsoft::UI::Xaml::Controls::TextBox logBox{nullptr};
    std::shared_ptr<amiebl::SmoothExpander> serviceLogExpander;
    hstring page = L"總覽", selectedTask, logs;
    bool working = false, polling = false, exiting = false, selecting = false, connected = false,
         indicatorInitialized = false;
    using ActionTask = std::function<Windows::Foundation::IAsyncAction()>;
    fire_and_forget StartAsync();
    fire_and_forget PollTick();
    Windows::Foundation::IAsyncAction GuiAcceptance();
    Windows::Foundation::IAsyncAction Capture(hstring name);
    Windows::Foundation::IAsyncAction InvokeButton(hstring label);
    void TestResult(Windows::Data::Json::JsonObject const &result);
    fire_and_forget Run(ActionTask action);
    Windows::Foundation::IAsyncAction Poll();
    Windows::Foundation::IAsyncAction Exit();
    Windows::Foundation::IAsyncAction LoadDefault();
    Windows::Foundation::IAsyncAction Unload();
    Windows::Foundation::IAsyncAction ToggleAccepting();
    Windows::Foundation::IAsyncAction ToggleKeep();
    Windows::Foundation::IAsyncAction ConnectVSCode();
    Windows::Foundation::IAsyncOperation<bool> Confirm(hstring text, hstring title = L"確認操作");
    void Error(hstring message);
    void Message(hstring message);
    void ShowPage(hstring next);
    void UpdateFooter();
    void UpdateIndicator();
    Microsoft::UI::Xaml::UIElement BuildOverview();
    Microsoft::UI::Xaml::UIElement BuildTasks();
    void UpdateOverview();
    void UpdateTasks();
    void UpdateTaskDetail();
    static Microsoft::UI::Xaml::Controls::TextBlock Text(hstring text, double size = 14);
    static Microsoft::UI::Xaml::Controls::StackPanel Panel();
    static Microsoft::UI::Xaml::Controls::StackPanel
    Row(std::initializer_list<Microsoft::UI::Xaml::UIElement> controls);
    static Microsoft::UI::Xaml::Controls::ScrollViewer
    Scroll(Microsoft::UI::Xaml::UIElement content);
    static Microsoft::UI::Xaml::Controls::Border Card(hstring title,
                                                      Microsoft::UI::Xaml::UIElement content);
    Microsoft::UI::Xaml::Controls::Button Action(hstring label, ActionTask action);
    static hstring Phase(hstring phase);
    hstring ApplicationState();
};
} // namespace winrt::AMIEBL::Native::implementation
namespace winrt::AMIEBL::Native::factory_implementation {
struct MainWindow : MainWindowT<MainWindow, implementation::MainWindow> {};
} // namespace winrt::AMIEBL::Native::factory_implementation
