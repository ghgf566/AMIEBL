#pragma once
#include "CoreClient.h"
#include "EditorState.h"
#include "MainWindow.g.h"
#include "SmoothExpander.h"
#include "WindowsLifecycle.h"
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
    std::unique_ptr<amiebl::WindowsLifecycle> lifecycle;
    void Reveal();
    unsigned revealCount = 0;
    Windows::Foundation::IAsyncAction LifecycleAcceptance();
    Windows::Foundation::IAsyncAction Reconnect();
    Windows::Data::Json::JsonObject config, status;
    Windows::Data::Json::JsonArray requests;
    Microsoft::UI::Xaml::DispatcherTimer timer;
    Microsoft::UI::Xaml::Controls::TextBlock overviewModel{nullptr}, overviewState{nullptr},
        overviewQueue{nullptr}, overviewMemory{nullptr}, overviewConnection{nullptr},
        overviewError{nullptr}, overviewEngine{nullptr};
    Windows::Data::Json::JsonObject engineRuntime;
    Microsoft::UI::Xaml::Controls::Border overviewErrorCard{nullptr};
    Microsoft::UI::Xaml::Controls::ScrollViewer overviewScroll{nullptr};
    Microsoft::UI::Xaml::Controls::ListView taskList{nullptr};
    Microsoft::UI::Xaml::Controls::ContentControl taskDetail{nullptr};
    Microsoft::UI::Xaml::Controls::TextBox logBox{nullptr};
    std::shared_ptr<amiebl::SmoothExpander> serviceLogExpander;
    std::shared_ptr<amiebl::EditorDraft> editor;
    Microsoft::UI::Xaml::Controls::ListView entityList{nullptr};
    Microsoft::UI::Xaml::Controls::ContentControl entityEditor{nullptr};
    Microsoft::UI::Xaml::Controls::ScrollViewer editorScroll{nullptr};
    std::map<hstring, Microsoft::UI::Xaml::Controls::Control> inputs;
    std::map<hstring, Microsoft::UI::Xaml::FrameworkElement> fieldBlocks;
    std::map<hstring, Microsoft::UI::Xaml::Controls::Slider> sliders;
    std::vector<std::shared_ptr<amiebl::SmoothExpander>> editorExpanders;
    std::shared_ptr<amiebl::SmoothExpander> modelLocations;
    hstring selectedModel, selectedProfile, entitySearch;
    std::map<hstring, hstring> initialFieldText, initialControlText;
    bool syncingFields = false;
    hstring page = L"總覽", selectedTask, logs;
    bool working = false, polling = false, exiting = false, selecting = false, connected = false,
         indicatorInitialized = false;
    using ActionTask = std::function<Windows::Foundation::IAsyncAction()>;
    std::vector<winrt::weak_ref<Microsoft::UI::Xaml::Input::ICommand>> commands;
    uint64_t configRevision = 0;
    bool savedStatusUnavailable = false;
    Microsoft::UI::Xaml::Controls::TextBlock capabilityText{nullptr};
    std::weak_ptr<amiebl::SmoothExpander> revealingEditor;
    void RefreshCommands();
    fire_and_forget StartAsync();
    fire_and_forget PollTick();
    Windows::Foundation::IAsyncAction GuiAcceptance();
    Windows::Foundation::IAsyncAction EditorAcceptance();
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
    void RestoreNavigation();
    Windows::Foundation::IAsyncOperation<bool> LeaveEditor();
    Windows::Foundation::IAsyncAction SaveEditor();
    Windows::Foundation::IAsyncAction
    SaveConfig(std::function<void(Windows::Data::Json::JsonObject const &)> edit);
    Microsoft::UI::Xaml::UIElement BuildEntities(hstring collection);
    Microsoft::UI::Xaml::UIElement BuildSystem();
    Microsoft::UI::Xaml::UIElement BuildEngines();
    Microsoft::UI::Xaml::Controls::TextBlock engineInfo{nullptr};
    Microsoft::UI::Xaml::Controls::ComboBox engineChannel{nullptr}, engineBackend{nullptr}, engineUpdate{nullptr}, engineMode{nullptr}, engineVersions{nullptr};
    Microsoft::UI::Xaml::Controls::CheckBox enginePinned{nullptr};
    bool enginePolicyLoaded = false;
    hstring engineCandidateId;
    bool engineCandidateInstalled = false;
    Microsoft::UI::Xaml::DispatcherTimer engineSaveTimer{nullptr};
    Microsoft::UI::Xaml::Controls::TextBlock engineHardware{nullptr}, engineCompatibility{nullptr}, engineDownloadText{nullptr};
    Microsoft::UI::Xaml::Controls::ProgressBar engineProgress{nullptr};
    Microsoft::UI::Xaml::Controls::Button engineDownload{nullptr}, engineCancel{nullptr}, engineCheck{nullptr};
    Windows::Foundation::IAsyncAction RefreshEngines();
    Windows::Foundation::IAsyncAction EngineAction(hstring path);
    Windows::Foundation::IAsyncAction SaveEnginePolicy();
    Windows::Foundation::IAsyncAction SelectEngine(bool remove);
    Windows::Foundation::IAsyncAction EnginesAcceptance();
    Microsoft::UI::Xaml::UIElement BuildForm(std::shared_ptr<amiebl::EditorDraft> draft);
    Microsoft::UI::Xaml::UIElement BuildModelLocations();
    void PopulateEntities(hstring collection);
    void RestoreEntitySelection(hstring collection);
    void OpenEditor(hstring collection, hstring id);
    void EditField(hstring key, hstring text);
    void SnapshotEditorControls();
    void UpdateDependencies();
    hstring ModelCapability(hstring id);
    Microsoft::UI::Xaml::Controls::Button DangerAction(hstring label, ActionTask action);
    Windows::Foundation::IAsyncOperation<hstring> PickFile(hstring extension);
    Windows::Foundation::IAsyncOperation<hstring> PickFolder();
    Windows::Foundation::IAsyncAction AddModel();
    Windows::Foundation::IAsyncAction AddModelPath(hstring path);
    Windows::Foundation::IAsyncAction ScanModels();
    Windows::Foundation::IAsyncAction AddProfile();
    Windows::Foundation::IAsyncAction DuplicateProfile();
    Windows::Foundation::IAsyncAction DeleteEntity();
    Windows::Foundation::IAsyncAction SetModelLocation(hstring previous, hstring replacement);
    Windows::Foundation::IAsyncAction CheckConnection();
    Windows::Foundation::IAsyncAction Export();
    Windows::Foundation::IAsyncAction Import();
    void SetStartup(bool enabled);
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
