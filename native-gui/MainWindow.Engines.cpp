#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
static ComboBox EngineChoice(std::initializer_list<std::pair<hstring,hstring>> values) {
    ComboBox box;
    for (auto const &[id,label] : values) { ComboBoxItem item; item.Content(box_value(label)); item.Tag(box_value(id)); box.Items().Append(item); }
    box.SelectedIndex(0); box.MinWidth(220); return box;
}
static hstring Selected(ComboBox const &box) {
    if (auto item = box.SelectedItem().try_as<ComboBoxItem>()) return unbox_value<hstring>(item.Tag());
    return L"";
}
static void Select(ComboBox const &box, hstring id) {
    for (uint32_t i=0;i<box.Items().Size();i++) if (unbox_value<hstring>(box.Items().GetAt(i).as<ComboBoxItem>().Tag())==id) { box.SelectedIndex(i); return; }
}
UIElement MainWindow::BuildEngines() {
    auto panel = Panel(); enginePolicyLoaded = false;
    if (engineSaveTimer) engineSaveTimer.Stop();
    engineHardware = Text(L"正在辨識這台電腦的硬體…",14); panel.Children().Append(engineHardware);
    engineInfo = Text(L"讀取引擎版本…",14); panel.Children().Append(engineInfo);
    engineMode = EngineChoice({{L"managed",L"AMIEBL 自動管理"},{L"external",L"自行指定外部引擎"}});
    engineChannel = EngineChoice({{L"stable",L"Stable · 正式版"},{L"preview",L"Preview · 預覽版"}});
    engineBackend = EngineChoice({{L"auto",L"自動選擇建議套件"},{L"cpu",L"CPU · x64"},{L"cuda12",L"NVIDIA · CUDA 12.4"},{L"cuda13",L"NVIDIA · CUDA 13.4"},{L"sycl",L"Intel · SYCL"},{L"openvino",L"Intel · OpenVINO"},{L"rocm",L"AMD · ROCm / HIP"},{L"vulkan",L"跨廠牌 · Vulkan"}});
    engineUpdate = EngineChoice({{L"notify",L"啟動時檢查並通知"},{L"download",L"自動下載，手動啟用"},{L"auto",L"自動下載，模型卸載後更新"},{L"off",L"僅手動檢查更新"}});
    panel.Children().Append(Row({engineMode,engineChannel}));
    panel.Children().Append(Text(L"運算套件與更新方式",12));
    panel.Children().Append(Row({engineBackend,engineUpdate}));
    engineCompatibility = Text(L"硬體相容條件會顯示在這裡。",12); panel.Children().Append(engineCompatibility);
    enginePinned = CheckBox(); enginePinned.Content(box_value(L"固定目前版本，暫停自動更新")); panel.Children().Append(enginePinned);
    engineDownload = Action(L"下載並安裝更新",[this]() -> IAsyncAction {
        if(engineCandidateInstalled) { co_await core->Request(L"POST",L"/manager/engines/activate",body(L"id",JsonValue::CreateStringValue(engineCandidateId))); enginePolicyLoaded=false; co_await RefreshEngines(); }
        else co_await EngineAction(L"install");
    });
    engineCheck = Action(L"檢查更新",[this]{return EngineAction(L"check");});
    panel.Children().Append(Row({engineCheck,engineDownload}));
    panel.Children().Append(Action(L"停止推理引擎",[this]() -> IAsyncAction {
        if (!co_await Confirm(L"停止推理引擎？正在執行的任務會取消，模型會卸載，並暫停接受新任務。AMIEBL 會繼續開啟。")) co_return;
        co_await EngineAction(L"stop");
    }));
    engineProgress = ProgressBar(); engineProgress.Minimum(0); engineProgress.Maximum(100); engineProgress.Visibility(Visibility::Collapsed); panel.Children().Append(engineProgress);
    engineDownloadText = Text(L"",12); engineDownloadText.Visibility(Visibility::Collapsed); panel.Children().Append(engineDownloadText);
    engineCancel = Action(L"取消下載",[this]{return EngineAction(L"cancel");}); engineCancel.Visibility(Visibility::Collapsed); panel.Children().Append(engineCancel);
    auto advanced = Panel(); engineVersions = ComboBox(); engineVersions.MinWidth(320); advanced.Children().Append(engineVersions);
    advanced.Children().Append(Row({Action(L"啟用選取版本",[this]{return SelectEngine(false);}),Action(L"回滾引擎版本",[this]{return EngineAction(L"rollback");}),Action(L"移除選取版本",[this]{return SelectEngine(true);})}));
    advanced.Children().Append(Text(L"引擎放在軟體旁的 engines 資料夾。停止後可移除目前版本，也可移除全部版本；刪除最後一個會暫停自動更新。模型與外部引擎會保留。",12));
    Expander details; details.Header(box_value(L"已安裝版本與還原")); details.Content(advanced); details.HorizontalAlignment(HorizontalAlignment::Stretch); panel.Children().Append(details);
    engineSaveTimer = DispatcherTimer(); engineSaveTimer.Interval(std::chrono::milliseconds(500));
    engineSaveTimer.Tick([weak=get_weak()](auto const &, auto const &) { if(auto self=weak.get()) { self->engineSaveTimer.Stop(); if(self->working) {self->engineSaveTimer.Start(); return;} self->Run([self]{return self->SaveEnginePolicy();}); } });
    auto changed = [weak=get_weak()](auto const &, auto const &) { if(auto self=weak.get()) if(self->enginePolicyLoaded) { self->engineSaveTimer.Stop(); self->engineSaveTimer.Start(); self->engineCheck.IsEnabled(false); self->engineDownload.IsEnabled(false); } };
    for(auto const &box : {engineMode,engineChannel,engineBackend,engineUpdate}) box.SelectionChanged(changed);
    enginePinned.Checked(changed); enginePinned.Unchecked(changed);
    return Card(L"推理引擎更新中心 · llama.cpp",panel);
}
IAsyncAction MainWindow::RefreshEngines() {
    auto result = co_await core->Request(L"GET",L"/manager/engines");
    if (!engineInfo || page != L"系統") co_return;
    auto policy = object(result,L"policy"), candidate = object(result,L"candidate"), job = object(result,L"job"), hardware = object(result,L"hardware");
    if (!enginePolicyLoaded) {
        Select(engineMode,str(policy,L"mode")); Select(engineChannel,str(policy,L"channel")); Select(engineBackend,str(policy,L"backend")); Select(engineUpdate,str(policy,L"update")); enginePinned.IsChecked(flag(policy,L"pinned")); enginePolicyLoaded=true;
    }
    hstring summary;
    for(auto v : array(hardware,L"cpu")) { auto c=v.GetObject(); summary=summary+str(c,L"Name")+L" · "+str(c,L"NumberOfCores")+L" 核心 / "+str(c,L"NumberOfLogicalProcessors")+L" 執行緒\n"; }
    for(auto v : array(hardware,L"gpu")) { auto g=v.GetObject(); summary=summary+str(g,L"Name")+L" · 驅動 "+str(g,L"DriverVersion")+L"\n"; }
    if(hardware.HasKey(L"ram_bytes")) summary=summary+to_hstring(static_cast<int>(hardware.GetNamedNumber(L"ram_bytes")/1073741824.0+0.5))+L" GB 系統記憶體";
    if(!summary.empty()) engineHardware.Text(summary);
    auto packages=array(result,L"packages"); auto active=str(result,L"active"); hstring message=L"目前尚未啟用管理引擎";
    for(auto v : packages) {auto p=v.GetObject(); if(str(p,L"id")==active) message=L"目前版本  "+str(p,L"version")+L" ("+str(p,L"build_tag")+L") · "+str(p,L"backend")+L" · "+str(p,L"channel");}
    if(str(policy,L"mode")==L"external") message=L"使用外部引擎 · 自動更新已停用";
    bool available=candidate.Size() && str(candidate,L"id")!=active;
    bool installed=false; for(auto v : packages) if(str(v.GetObject(),L"id")==str(candidate,L"id")) installed=true;
    if(candidate.Size()) message=message+L"\n"+(available?L"可用套件  ":L"已是目前通道的最新版本  ")+str(candidate,L"version")+L" ("+str(candidate,L"build_tag")+L") · "+str(candidate,L"backend");
    if(flag(result,L"checking")) message=message+L"\n正在檢查更新…";
    if(result.HasKey(L"check_error") && !str(result,L"check_error").empty()) message=message+L"\n檢查更新失敗："+str(result,L"check_error");
    auto state=str(job,L"state"); bool busy=state==L"starting" || state==L"downloading" || state==L"extracting" || state==L"validating";
    if(state==L"failed") message=message+L"\n安裝失敗："+str(job,L"error");
    if(state==L"cancelled") message=message+L"\n下載已取消";
    if(installed && available) message=message+L"\n套件已安裝；可於模型卸載後啟用。";
    engineInfo.Text(message);
    hstring compatible=str(hardware,L"reason");
    auto selectedBackend=Selected(engineBackend); auto catalog=array(result,L"catalog");
    if(selectedBackend!=L"auto") for(auto v : (catalog.Size()?catalog:array(hardware,L"backends"))) {auto o=v.GetObject(); if(str(o,L"id")==selectedBackend) compatible=(flag(o,L"eligible")?L"符合初步硬體條件 · ":L"目前硬體不符合或尚未確認 · ")+str(o,L"reason");}
    if(candidate.Size()) for(auto v : catalog) {auto o=v.GetObject(); if(str(o,L"id")==str(candidate,L"backend") && !str(o,L"toolkit_version").empty()) message=message+L"\n套件執行期  "+str(o,L"id")+L" "+str(o,L"toolkit_version");}
    engineInfo.Text(message);
    engineCompatibility.Text(compatible);
    engineCandidateId=str(candidate,L"id"); engineCandidateInstalled=installed;
    engineDownload.Content(box_value(installed?L"啟用更新":L"下載並安裝更新"));
    engineDownload.Visibility(available && !busy?Visibility::Visible:Visibility::Collapsed);
    engineCheck.IsEnabled(!busy && !flag(result,L"checking") && !engineSaveTimer.IsEnabled());
    engineDownload.IsEnabled(!engineSaveTimer.IsEnabled());
    engineCancel.Visibility(busy?Visibility::Visible:Visibility::Collapsed);
    engineProgress.Visibility(busy?Visibility::Visible:Visibility::Collapsed);
    engineDownloadText.Visibility(busy?Visibility::Visible:Visibility::Collapsed);
    engineProgress.IsIndeterminate(state!=L"downloading");
    if(state==L"downloading") {double bytes=job.GetNamedNumber(L"bytes",0),total=job.GetNamedNumber(L"total",0); double percent=total>0?100*bytes/total:0; engineProgress.Value(percent); engineDownloadText.Text(L"下載中 · "+to_hstring(static_cast<int>(percent))+L"% · "+to_hstring(static_cast<int>(bytes/1048576))+L" / "+to_hstring(static_cast<int>(total/1048576))+L" MB");}
    else engineDownloadText.Text(state==L"extracting"?L"解壓縮中…":state==L"validating"?L"驗證引擎與硬體裝置…":L"準備下載…");
    for(auto const &box : {engineMode,engineChannel,engineBackend,engineUpdate}) box.IsEnabled(!busy);
    enginePinned.IsEnabled(!busy);
    auto selected = Selected(engineVersions); hstring ids;
    for (auto v : packages) ids = ids + str(v.GetObject(),L"id")+L";";
    auto versionState = ids + L"|active=" + str(result,L"active");
    auto previous = engineVersions.Tag().try_as<IPropertyValue>();
    if (!previous || previous.GetString()!=versionState) {
        engineVersions.Items().Clear();
        for (auto v : packages) { auto p=v.GetObject(); ComboBoxItem item; item.Tag(box_value(str(p,L"id"))); item.Content(box_value(str(p,L"channel")+L" / "+str(p,L"version")+L" / "+str(p,L"backend")+(str(p,L"id")==str(result,L"active")?L" · 目前選用":L""))); engineVersions.Items().Append(item); }
        engineVersions.Tag(box_value(versionState)); if (engineVersions.Items().Size()) engineVersions.SelectedIndex(0); Select(engineVersions,selected);
    }
}
IAsyncAction MainWindow::SaveEnginePolicy() {
    JsonObject policy;
    for (auto const &[key,choice] : std::vector<std::pair<hstring,ComboBox>>{{L"mode",engineMode},{L"channel",engineChannel},{L"backend",engineBackend},{L"update",engineUpdate}}) policy.SetNamedValue(key,JsonValue::CreateStringValue(Selected(choice)));
    policy.SetNamedValue(L"pinned",JsonValue::CreateBooleanValue(enginePinned.IsChecked() && enginePinned.IsChecked().Value()));
    std::exception_ptr error;
    try { co_await core->Request(L"POST",L"/manager/engines/policy",policy); } catch (...) { error=std::current_exception(); enginePolicyLoaded=false; }
    if(error) { co_await RefreshEngines(); std::rethrow_exception(error); }
    co_await RefreshEngines();
}
IAsyncAction MainWindow::EngineAction(hstring path) {
    if (path==L"rollback" && !co_await Confirm(L"回滾至上一個引擎版本？")) co_return;
    JsonObject request;
    if(path==L"install") request.SetNamedValue(L"activate_when_idle",JsonValue::CreateBooleanValue(true));
    co_await core->Request(L"POST",L"/manager/engines/"+path,request);
    if(path==L"rollback") enginePolicyLoaded=false;
    co_await RefreshEngines();
}
IAsyncAction MainWindow::SelectEngine(bool remove) {
    auto id=Selected(engineVersions); if (id.empty()) throw hresult_error(E_FAIL,L"請先選擇已安裝的引擎版本。");
    if (remove && !co_await Confirm(L"移除選取引擎版本？目前選用的版本需先停止運作；全部版本均可移除，刪除最後一個會暫停自動更新。模型、未知檔案與外部引擎會保留。")) co_return;
    co_await core->Request(L"POST",remove?L"/manager/engines/remove":L"/manager/engines/activate",body(L"id",JsonValue::CreateStringValue(id)));
    enginePolicyLoaded=false;
    co_await RefreshEngines();
}
IAsyncAction MainWindow::EnginesAcceptance() {
    if(CoreClient::Option(L"--server-view-test")==L"1") {
        ShowPage(L"總覽"); co_await Poll();
        if(!engineRuntime.HasKey(L"state") || overviewEngine.Text().empty()) throw hresult_error(E_FAIL,L"Server 狀態卡片未完成更新。");
        co_await Capture(L"server-overview");
        JsonObject proof; proof.SetNamedValue(L"ok",JsonValue::CreateBooleanValue(true));
        proof.SetNamedValue(L"server",engineRuntime); proof.SetNamedValue(L"server_text",JsonValue::CreateStringValue(overviewEngine.Text()));
        TestResult(proof); co_return;
    }
    ShowPage(L"系統"); co_await RefreshEngines();
    if(CoreClient::Option(L"--engine-view-test")==L"1") {
        for(int i=0;i<50;i++) {
            auto state=co_await core->Request(L"GET",L"/manager/engines");
            if(object(state,L"hardware").Size() && !flag(state,L"checking")) break;
            co_await winrt::resume_after(std::chrono::milliseconds(300)); co_await ResumeUI{DispatcherQueue()};
        }
        co_await RefreshEngines(); co_await Capture(L"engine-update-center");
        JsonObject proof; proof.SetNamedValue(L"ok",JsonValue::CreateBooleanValue(true)); TestResult(proof); co_return;
    }
    Select(engineChannel,L"preview"); Select(engineBackend,L"cpu"); Select(engineMode,L"external"); Select(engineUpdate,L"off"); enginePinned.IsChecked(true);
    co_await winrt::resume_after(std::chrono::milliseconds(1200));
    co_await ResumeUI{DispatcherQueue()};
    auto result = co_await core->Request(L"GET",L"/manager/engines"); auto policy=object(result,L"policy");
    if (str(policy,L"channel")!=L"preview" || str(policy,L"backend")!=L"cpu" || str(policy,L"mode")!=L"external" || str(policy,L"update")!=L"off" || !flag(policy,L"pinned")) throw hresult_error(E_FAIL,L"引擎介面設定未持久化。");
    if(engineCancel.Visibility()!=Visibility::Collapsed || engineProgress.Visibility()!=Visibility::Collapsed) throw hresult_error(E_FAIL,L"閒置時不應顯示下載操作。");
    for(int i=0;i<30;i++) {
        auto info=co_await core->Request(L"GET",L"/manager/engines");
        if(object(info,L"hardware").Size()) break;
        co_await winrt::resume_after(std::chrono::milliseconds(300)); co_await ResumeUI{DispatcherQueue()};
    }
    co_await RefreshEngines();
    co_await Capture(L"engine-management");
    JsonObject evidence; evidence.SetNamedValue(L"ok",JsonValue::CreateBooleanValue(true)); evidence.SetNamedValue(L"engine_controls",JsonValue::CreateBooleanValue(true)); evidence.SetNamedValue(L"policy",policy); TestResult(evidence);
}
} // namespace winrt::AMIEBL::Native::implementation
