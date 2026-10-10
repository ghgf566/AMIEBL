#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
#include <microsoft.ui.xaml.window.h>
#include <shobjidl.h>
#include <winrt/Windows.Storage.h>
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
static hstring NewId(hstring prefix) {
    GUID id{};
    check_hresult(CoCreateGuid(&id));
    std::wostringstream out;
    out << std::hex << std::setfill(L'0') << std::setw(8) << id.Data1;
    return prefix + hstring(out.str());
}
Button MainWindow::DangerAction(hstring label, ActionTask action) {
    auto b = Action(label, action);
    b.Background(ExitButton().Background());
    b.Foreground(ExitButton().Foreground());
    for (auto resource : ExitButton().Resources())
        b.Resources().Insert(resource.Key(), resource.Value());
    return b;
}
void MainWindow::RestoreNavigation() {
    selecting = true;
    for (auto v : Navigation().MenuItems())
        if (unbox_value<hstring>(v.as<NavigationViewItem>().Tag()) == page) {
            Navigation().SelectedItem(v);
            break;
        }
    selecting = false;
}
IAsyncOperation<bool> MainWindow::LeaveEditor() {
    while(editorSaving) { co_await winrt::resume_after(std::chrono::milliseconds(50)); co_await ResumeUI{DispatcherQueue()}; }
    SnapshotEditorControls();
    if (!editor || !editor->dirty)
        co_return true;
    try { co_await SaveEditor(); co_return true; } catch(hresult_error const &e) { editorSaveState.Text(L"尚未儲存："+e.message()); }
    ContentDialog dialog;
    dialog.XamlRoot(Root().XamlRoot());
    dialog.Title(box_value(L"尚未儲存的修改"));
    dialog.Content(box_value(L"要先儲存目前的修改嗎？"));
    dialog.PrimaryButtonText(L"儲存");
    dialog.SecondaryButtonText(L"捨棄修改");
    dialog.CloseButtonText(L"取消");
    dialog.DefaultButton(ContentDialogButton::Close);
    auto result = co_await dialog.ShowAsync();
    if (result == ContentDialogResult::None)
        co_return false;
    if (result == ContentDialogResult::Primary)
        co_await SaveEditor();
    else
        editor->dirty = false;
    co_return true;
}
IAsyncAction MainWindow::SaveConfig(std::function<void(JsonObject const &)> edit) {
    auto latest = co_await core->Request(L"GET", L"/manager/config");
    edit(latest);
    // Publish PUT's validated result before attempting the optional status read.
    config = co_await core->Request(L"PUT", L"/manager/config", latest);
    configRevision++;
    savedStatusUnavailable = false;
    try {
        status = co_await core->Request(L"GET", L"/manager/status");
    } catch (hresult_error const &) {
        savedStatusUnavailable = true;
        Message(L"設定已儲存，但暫時無法刷新服務狀態。請稍後重新整理。");
    }
    UpdateFooter();
}
void MainWindow::SetStartup(bool enabled) {
#ifdef AMIEBL_RELEASE_BUILD
    constexpr auto startupName = L"LocalModelManager";
#else
    constexpr auto startupName = L"AMIEBL.Native.Development";
#endif
    HKEY key = nullptr;
    check_hresult(HRESULT_FROM_WIN32(
        RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0,
                        nullptr, 0, KEY_SET_VALUE, nullptr, &key, nullptr)));
    LSTATUS result = ERROR_SUCCESS;
    if (enabled) {
        wchar_t exe[32768];
        GetModuleFileNameW(nullptr, exe, 32768);
        std::wstring command =
            L"\"" + std::wstring(exe) + L"\" --data-dir \"" + core->dataDir.wstring() + L"\"";
        result = RegSetValueExW(key, startupName, 0, REG_SZ,
                                reinterpret_cast<BYTE const *>(command.c_str()),
                                static_cast<DWORD>((command.size() + 1) * sizeof(wchar_t)));
    } else {
        result = RegDeleteValueW(key, startupName);
        if (result == ERROR_FILE_NOT_FOUND)
            result = ERROR_SUCCESS;
    }
    RegCloseKey(key);
    check_hresult(HRESULT_FROM_WIN32(result));
}
IAsyncAction MainWindow::SaveEditor() {
    while(editorSaving) { co_await winrt::resume_after(std::chrono::milliseconds(50)); co_await ResumeUI{DispatcherQueue()}; }
    editorSaving=true;
    struct ResetSaving { bool &flag; ~ResetSaving() {flag=false;} } reset{editorSaving};
    if(editorSaveTimer) editorSaveTimer.Stop();
    SnapshotEditorControls();
    if (!editor)
        throw hresult_error(E_FAIL, L"沒有可儲存的編輯器。");
    auto draft = editor;
    auto changed = draft->Preview(true);
    auto revision = draft->revision;
    bool priorStartup = flag(config, L"auto_start");
    co_await SaveConfig(
        [draft, changed](JsonObject const &latest) { draft->Merge(latest, changed); });
    if (draft->collection == L"system" && priorStartup != flag(config, L"auto_start")) {
        std::exception_ptr failure;
        try {
            SetStartup(flag(config, L"auto_start"));
        } catch (...) {
            failure = std::current_exception();
        }
        if (failure) {
            co_await SaveConfig([priorStartup](auto const &c) {
                c.SetNamedValue(L"auto_start", JsonValue::CreateBooleanValue(priorStartup));
            });
            std::rethrow_exception(failure);
        }
    }
    if (draft->revision == revision)
        draft->dirty = false;
    auto persisted =
        draft->collection == L"system" ? config : entity(config, draft->collection, draft->id);
    hstring normalized;
    for (auto const &f : draft->fields)
        if (f.spec.key != L"agent_tools" && f.spec.key != L"model_dirs" &&
            !equal(value(changed, f.spec.key), value(persisted, f.spec.key))) {
            if (!normalized.empty())
                normalized = normalized + L"、";
            normalized = normalized + f.spec.label;
        }
    draft->original = clone(persisted);
    if(draft->dirty) {
        EditorDraft normalizedDraft(draft->collection,persisted,EditorSchema(draft->collection,config));
        syncingFields=true;
        for(auto &field : draft->fields) {
            bool unchanged=false;
            try { unchanged=equal(field.Parse(),value(changed,field.spec.key)); } catch(hresult_error const &) {}
            if(!unchanged) continue;
            field.text=normalizedDraft.Field(field.spec.key).text;
            auto control=inputs.at(field.spec.key);
            if(auto box=control.try_as<TextBox>()) box.Text(field.text);
            else if(auto check=control.try_as<CheckBox>()) check.IsChecked(field.text==L"true");
            else if(auto choice=control.try_as<ComboBox>()) for(auto item : choice.Items())
                if(unbox_value<hstring>(item.as<ComboBoxItem>().Tag())==field.text) choice.SelectedItem(item);
            initialFieldText[field.spec.key]=field.text;
            initialControlText[field.spec.key]=control.try_as<TextBox>()?control.as<TextBox>().Text():field.text;
        }
        syncingFields=false;
    }
    // Do not erase edits made while a persistence request was in flight.
    if (!draft->dirty) {
        *draft=EditorDraft(draft->collection,persisted,EditorSchema(draft->collection,config));
        syncingFields=true;
        for(auto const &field : draft->fields) {
            auto control=inputs.at(field.spec.key);
            if(auto box=control.try_as<TextBox>()) box.Text(field.text);
            else if(auto check=control.try_as<CheckBox>()) check.IsChecked(field.text==L"true");
            else if(auto choice=control.try_as<ComboBox>()) for(auto item : choice.Items())
                if(unbox_value<hstring>(item.as<ComboBoxItem>().Tag())==field.text) choice.SelectedItem(item);
            initialFieldText[field.spec.key]=field.text;
            initialControlText[field.spec.key]=control.try_as<TextBox>()?control.as<TextBox>().Text():field.text;
        }
        syncingFields=false;
        UpdateDependencies();
        if(draft->collection!=L"system") {
            PopulateEntities(draft->collection);
            RestoreEntitySelection(draft->collection);
        }
    }
    editorSaveState.Text(draft->dirty?L"等待自動儲存…":L"已自動儲存");
    UpdateFooter();
    if (!savedStatusUnavailable && !normalized.empty())
        Message(ApplicationState() +
                (normalized.empty() ? hstring()
                                    : L"。後端調整了：" + normalized + L"；已顯示實際儲存值。"));
}
IAsyncAction MainWindow::AutoSaveEditor() {
    auto lifetime=get_strong(); auto draft=editor;
    if(!draft || !draft->dirty) co_return;
    editorSaveState.Text(L"正在儲存…");
    try { co_await SaveEditor(); }
    catch(hresult_error const &e) { if(editor==draft) editorSaveState.Text(L"尚未儲存："+e.message()); }
    catch(std::exception const &e) { if(editor==draft) editorSaveState.Text(L"尚未儲存："+to_hstring(e.what())); }
}
// Windows native file dialogs preserve the existing picker workflow and HWND
// ownership. Cancellation never changes a draft or invokes a server mutation.
IAsyncOperation<hstring> MainWindow::PickFile(hstring extension) {
    HWND owner = nullptr;
    check_hresult(get_strong().as<IWindowNative>()->get_WindowHandle(&owner));
    com_ptr<IFileOpenDialog> picker;
    check_hresult(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_PPV_ARGS(picker.put())));
    std::wstring pattern = L"*" + std::wstring(extension);
    COMDLG_FILTERSPEC filter{extension.c_str(), pattern.c_str()};
    check_hresult(picker->SetFileTypes(1, &filter));
    auto hr = picker->Show(owner);
    if (hr == HRESULT_FROM_WIN32(ERROR_CANCELLED))
        co_return hstring();
    check_hresult(hr);
    com_ptr<IShellItem> item;
    check_hresult(picker->GetResult(item.put()));
    PWSTR path = nullptr;
    check_hresult(item->GetDisplayName(SIGDN_FILESYSPATH, &path));
    hstring result(path);
    CoTaskMemFree(path);
    co_return result;
}
IAsyncOperation<hstring> MainWindow::PickFolder() {
    HWND owner = nullptr;
    check_hresult(get_strong().as<IWindowNative>()->get_WindowHandle(&owner));
    com_ptr<IFileOpenDialog> picker;
    check_hresult(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_PPV_ARGS(picker.put())));
    DWORD flags;
    check_hresult(picker->GetOptions(&flags));
    check_hresult(picker->SetOptions(flags | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM));
    auto hr = picker->Show(owner);
    if (hr == HRESULT_FROM_WIN32(ERROR_CANCELLED))
        co_return hstring();
    check_hresult(hr);
    com_ptr<IShellItem> item;
    check_hresult(picker->GetResult(item.put()));
    PWSTR path = nullptr;
    check_hresult(item->GetDisplayName(SIGDN_FILESYSPATH, &path));
    hstring result(path);
    CoTaskMemFree(path);
    co_return result;
}
IAsyncAction MainWindow::AddModel() {
    if (!(co_await LeaveEditor()))
        co_return;
    auto path = co_await PickFile(L".gguf");
    if (path.empty())
        co_return;
    co_await AddModelPath(path);
    ShowPage(L"模型庫");
}
IAsyncAction MainWindow::AddModelPath(hstring path) {
    auto id = NewId(L"model-");
    JsonObject m;
    m.SetNamedValue(L"id", JsonValue::CreateStringValue(id));
    m.SetNamedValue(L"name", JsonValue::CreateStringValue(
                                 hstring(std::filesystem::path(path.c_str()).stem().wstring())));
    m.SetNamedValue(L"path", JsonValue::CreateStringValue(path));
    m.SetNamedValue(L"context", JsonValue::CreateNumberValue(32768));
    m.SetNamedValue(L"gpu_layers", JsonValue::CreateNumberValue(-1));
    m.SetNamedValue(L"auto_fit", JsonValue::CreateBooleanValue(true));
    m.SetNamedValue(L"fit_target_enabled", JsonValue::CreateBooleanValue(false));
    m.SetNamedValue(L"mtp", JsonValue::CreateBooleanValue(false));
    m.SetNamedValue(L"cache_type", JsonValue::CreateStringValue(L"q8_0"));
    co_await SaveConfig([m, id](auto const &c) {
        auto rows = array(c, L"models");
        rows.Append(m);
        c.SetNamedValue(L"models", rows);
        if (rows.Size() == 1)
            c.SetNamedValue(L"default_model_id", JsonValue::CreateStringValue(id));
    });
    selectedModel = id;
}
IAsyncAction MainWindow::ScanModels() {
    if (!(co_await LeaveEditor()))
        co_return;
    auto found = co_await core->Request(L"POST", L"/manager/scan");
    auto choices = Panel();
    std::vector<std::pair<CheckBox, hstring>> checks;
    for (auto v : array(found, L"models")) {
        auto path = str(v.GetObject(), L"path");
        CheckBox box;
        box.Content(box_value(path));
        choices.Children().Append(box);
        checks.emplace_back(box, path);
    }
    ContentDialog dialog;
    dialog.XamlRoot(Root().XamlRoot());
    dialog.Title(box_value(L"加入掃描到的模型"));
    dialog.Content(Scroll(choices));
    dialog.PrimaryButtonText(L"加入選取模型");
    dialog.CloseButtonText(L"取消");
    if (co_await dialog.ShowAsync() != ContentDialogResult::Primary)
        co_return;
    for (auto const &[box, path] : checks)
        if (box.IsChecked() && box.IsChecked().Value())
            co_await AddModelPath(path);
    ShowPage(L"模型庫");
}
IAsyncAction MainWindow::AddProfile() {
    if (!(co_await LeaveEditor()))
        co_return;
    auto id = NewId(L"profile-");
    JsonObject p =
        JsonObject::Parse(L"{\"name\":\"新的使用模式\",\"thinking_mode\":\"auto\",\"reasoning_"
                          L"level\":\"balanced\",\"budget_mode\":\"auto\",\"thinking_budget\":1536,"
                          L"\"max_tokens\":8192}");
    p.SetNamedValue(L"id", JsonValue::CreateStringValue(id));
    co_await SaveConfig([p](auto const &c) {
        auto rows = array(c, L"profiles");
        rows.Append(p);
        c.SetNamedValue(L"profiles", rows);
    });
    selectedProfile = id;
    ShowPage(L"使用模式");
}
IAsyncAction MainWindow::DuplicateProfile() {
    if (!(co_await LeaveEditor()) || selectedProfile.empty())
        co_return;
    auto source = selectedProfile, id = NewId(L"profile-");
    co_await SaveConfig([source, id](auto const &c) {
        auto p = clone(entity(c, L"profiles", source));
        if (p.Size() == 0)
            throw hresult_error(E_FAIL, L"此項目已被移除，請重新整理。");
        p.SetNamedValue(L"id", JsonValue::CreateStringValue(id));
        p.SetNamedValue(L"name", JsonValue::CreateStringValue(str(p, L"name") + L" 副本"));
        auto rows = array(c, L"profiles");
        rows.Append(p);
        c.SetNamedValue(L"profiles", rows);
    });
    selectedProfile = id;
    ShowPage(L"使用模式");
}
IAsyncAction MainWindow::DeleteEntity() {
    auto draft = editor;
    if (!draft || !(co_await Confirm(L"移除此登錄？模型檔案不會刪除。")))
        co_return;
    co_await SaveConfig([draft](auto const &c) {
        auto rows = array(c, draft->collection);
        uint32_t index = rows.Size();
        for (uint32_t i = 0; i < rows.Size(); i++)
            if (str(rows.GetAt(i).GetObject(), L"id") == draft->id)
                index = i;
        if (index == rows.Size())
            throw hresult_error(E_FAIL, L"此項目已被移除，請重新整理。");
        if (draft->collection == L"profiles") {
            if (rows.Size() == 1)
                throw hresult_error(E_FAIL, L"至少保留一個使用模式。");
            auto fallback = str(rows.GetAt(index == 0 ? 1 : 0).GetObject(), L"id");
            if (str(c, L"default_profile_id") == draft->id)
                c.SetNamedValue(L"default_profile_id", JsonValue::CreateStringValue(fallback));
            for (auto v : array(c, L"models"))
                if (str(v.GetObject(), L"default_profile_id") == draft->id) {
                    auto model = v.GetObject();
                    model.SetNamedValue(L"default_profile_id",
                                        JsonValue::CreateStringValue(fallback));
                    replace_entity(c, L"models", model);
                }
        }
        rows.RemoveAt(index);
        c.SetNamedValue(draft->collection, rows);
        if (draft->collection == L"models" && str(c, L"default_model_id") == draft->id)
            c.SetNamedValue(L"default_model_id",
                            JsonValue::CreateStringValue(
                                rows.Size() ? str(rows.GetAt(0).GetObject(), L"id") : hstring()));
    });
    ShowPage(page);
}
UIElement MainWindow::BuildModelLocations() {
    auto content = Panel();
    content.Children().Append(Text(L"掃描來源；更改登錄不會移動或刪除 GGUF。", 12));
    for (auto v : array(config, L"model_dirs")) {
        auto path = v.GetString();
        content.Children().Append(Text(path, 12));
        content.Children().Append(Row({Action(L"變更位置",
                                              [this, path]() -> IAsyncAction {
                                                  if (!(co_await LeaveEditor()))
                                                      co_return;
                                                  auto replacement = co_await PickFolder();
                                                  if (replacement.empty())
                                                      co_return;
                                                  co_await SetModelLocation(path, replacement);
                                                  ShowPage(L"模型庫");
                                              }),
                                       Action(L"移除", [this, path]() -> IAsyncAction {
                                           if (!(co_await LeaveEditor()))
                                               co_return;
                                           co_await SetModelLocation(path, L"");
                                           ShowPage(L"模型庫");
                                       })}));
    }
    content.Children().Append(Action(L"＋ 新增模型資料夾", [this]() -> IAsyncAction {
        if (!(co_await LeaveEditor()))
            co_return;
        auto path = co_await PickFolder();
        if (path.empty())
            co_return;
        co_await SetModelLocation(L"", path);
        ShowPage(L"模型庫");
    }));
    modelLocations = SmoothExpander::Create(L"模型存放位置", Scroll(content), ExpandDirection::Up);
    modelLocations->root.MaxHeight(340);
    return modelLocations->root;
}
IAsyncAction MainWindow::SetModelLocation(hstring previous, hstring replacement) {
    co_await SaveConfig([previous, replacement](auto const &c) {
        JsonArray dirs;
        bool present = false;
        for (auto v : array(c, L"model_dirs")) {
            auto path = v.GetString();
            if (!previous.empty() && _wcsicmp(path.c_str(), previous.c_str()) == 0)
                continue;
            dirs.Append(v);
            if (_wcsicmp(path.c_str(), replacement.c_str()) == 0)
                present = true;
        }
        if (!replacement.empty() && !present)
            dirs.Append(JsonValue::CreateStringValue(replacement));
        c.SetNamedValue(L"model_dirs", dirs);
    });
}
IAsyncAction MainWindow::CheckConnection() {
    auto r = co_await core->Request(L"GET", L"/manager/connection");
    int count = 0;
    for (auto v : array(r, L"models"))
        if (std::wstring(str(v.GetObject(), L"id")).find(L"::") == std::wstring::npos)
            count++;
    Message((flag(r, L"ok") ? hstring(L"連線檢查通過") : hstring(L"連線檢查發現問題")) +
            L"\nAPI：" + str(r, L"api_url") + L"\nllama.cpp：" +
            (flag(r, L"engine_exists") ? hstring(L"已找到")
                                       : hstring(L"找不到引擎，請確認系統頁的引擎資料夾")) +
            L"\n引擎連接埠：" + str(r, L"port_status", L"未取得狀態") + L"\n本機模型登錄：" +
            to_hstring(count) + L" 個");
    Notice().Severity(flag(r, L"ok") ? InfoBarSeverity::Success : InfoBarSeverity::Warning);
}
IAsyncAction MainWindow::Export() {
    HWND owner = nullptr;
    check_hresult(get_strong().as<IWindowNative>()->get_WindowHandle(&owner));
    if (!(co_await LeaveEditor()))
        co_return;
    com_ptr<IFileSaveDialog> picker;
    check_hresult(CoCreateInstance(CLSID_FileSaveDialog, nullptr, CLSCTX_INPROC_SERVER,
                                   IID_PPV_ARGS(picker.put())));
    COMDLG_FILTERSPEC filter{L"JSON", L"*.json"};
    check_hresult(picker->SetFileTypes(1, &filter));
    check_hresult(picker->SetDefaultExtension(L"json"));
    check_hresult(picker->SetFileName(L"AMIEBL-settings.json"));
    auto hr = picker->Show(owner);
    if (hr == HRESULT_FROM_WIN32(ERROR_CANCELLED))
        co_return;
    check_hresult(hr);
    com_ptr<IShellItem> item;
    check_hresult(picker->GetResult(item.put()));
    PWSTR path = nullptr;
    check_hresult(item->GetDisplayName(SIGDN_FILESYSPATH, &path));
    std::filesystem::path file(path);
    CoTaskMemFree(path);
    auto exported = co_await core->Request(L"GET", L"/manager/export");
    std::ofstream out(file);
    out.exceptions(std::ios::failbit | std::ios::badbit);
    out << to_string(exported.Stringify());
    out.close();
    Message(L"設定已匯出。");
}
IAsyncAction MainWindow::Import() {
    if (!(co_await LeaveEditor()))
        co_return;
    auto path = co_await PickFile(L".json");
    if (path.empty())
        co_return;
    std::ifstream in(std::filesystem::path(path.c_str()));
    if (!in)
        throw hresult_error(E_FAIL, L"無法讀取設定檔案。");
    std::string text((std::istreambuf_iterator<char>(in)), {});
    auto imported = JsonObject::Parse(to_hstring(text));
    if (!(co_await Confirm(
            L"匯入會替換目前設定，後端會先備份。" +
            (flag(imported, L"auto_start") ? hstring(L"此設定會啟用登入自動啟動。") : hstring()))))
        co_return;
    bool prior = flag(config, L"auto_start");
    config = co_await core->Request(L"POST", L"/manager/import", imported);
    std::exception_ptr failure;
    try {
        if (prior != flag(config, L"auto_start"))
            SetStartup(flag(config, L"auto_start"));
    } catch (...) {
        failure = std::current_exception();
    }
    if (failure) {
        co_await SaveConfig([prior](auto const &c) {
            c.SetNamedValue(L"auto_start", JsonValue::CreateBooleanValue(prior));
        });
        std::rethrow_exception(failure);
    }
    config = co_await core->Request(L"GET", L"/manager/config");
    co_await Poll();
    ShowPage(L"系統");
    Message(ApplicationState());
}
} // namespace winrt::AMIEBL::Native::implementation
