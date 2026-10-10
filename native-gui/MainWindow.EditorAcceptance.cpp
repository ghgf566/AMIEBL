#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
#include <winrt/Microsoft.UI.Xaml.Automation.Peers.h>
#include <winrt/Microsoft.UI.Xaml.Automation.Provider.h>
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
IAsyncAction MainWindow::EditorAcceptance() {
    auto check = [](bool ok, hstring text) {
        if (!ok)
            throw hresult_error(E_FAIL, text);
    };
    auto setText = [this](hstring key, hstring text) { inputs.at(key).as<TextBox>().Text(text); };
    auto choose = [this](hstring key, hstring id) {
        auto box = inputs.at(key).as<ComboBox>();
        for (auto v : box.Items())
            if (unbox_value<hstring>(v.as<ComboBoxItem>().Tag()) == id) {
                box.SelectedItem(v);
                return;
            }
        throw hresult_error(E_FAIL, L"找不到下拉選項。");
    };
    auto checked = [this](hstring key, bool state) {
        inputs.at(key).as<CheckBox>().IsChecked(state);
    };
    auto original = clone(config);
    auto modelId = str(config, L"default_model_id");
    ShowPage(L"模型庫");
    Root().UpdateLayout();
    check(inputs.size() == 21, L"模型欄位規格不完整。");
    check(!editor->dirty, L"初始模型草稿不應是未保存。");
    auto before = editor;
    setText(L"context", L"8192");
    sliders.at(L"output_percent").Value(50);
    for (int i = 0; i < 40 && (editor->Field(L"output_percent").text != L"50" ||
             std::wstring_view(allocationInfo.Text()).find(L"4096 tokens") == std::wstring_view::npos); i++) {
        co_await winrt::resume_after(std::chrono::milliseconds(25));
        co_await ResumeUI{DispatcherQueue()};
    }
    check(editor->Field(L"output_percent").text==L"50" && std::wstring_view(allocationInfo.Text()).find(L"4096 tokens")!=std::wstring_view::npos,L"比例滑桿未換算輸入輸出額度。");
    checked(L"auto_fit", false);
    check(fieldBlocks.at(L"gpu_layers").Visibility() == Visibility::Visible,
          L"手動 GPU 層數沒有顯示。");
    checked(L"auto_fit", true);
    checked(L"fit_target_enabled", true);
    check(fieldBlocks.at(L"fit_target_mib").Visibility() == Visibility::Visible,
          L"自訂預留記憶體沒有顯示。");
    checked(L"mtp", true);
    {
        auto model=entity(config,L"models",modelId);
        for(auto value : inputs.at(L"mtp_source").as<ComboBox>().Items()) {
            auto item=value.as<ComboBoxItem>();
            if(unbox_value<hstring>(item.Tag())==L"native")
                check(item.IsEnabled()==(str(model,L"mtp_capability")==L"available"),L"內建 MTP 選項未依模型能力限制。");
        }
    }
    choose(L"mtp_source", L"external");
    check(fieldBlocks.at(L"mtp_draft_path").Visibility() == Visibility::Visible,
          L"Draft 路徑沒有顯示。");
    checked(L"mtp", false);
    check(fieldBlocks.at(L"mtp_draft_path").Visibility() == Visibility::Collapsed,
          L"Draft 路徑沒有收合。");
    setText(L"temperature", L"");
    auto scroll = editorScroll;
    scroll.ChangeView(nullptr, 150, nullptr, true);
    co_await Poll();
    check(editor == before && inputs.at(L"context").as<TextBox>().Text() == L"8192" &&
              editorScroll == scroll,
          L"輪詢破壞草稿。");
    // Latest server configuration changes an unrelated field. Merge must retain
    // it.
    auto concurrent = co_await core->Request(L"GET", L"/manager/config");
    concurrent.SetNamedValue(L"native_editor_unknown", JsonObject::Parse(L"{\"preserve\":true}"));
    auto concurrentModel = entity(concurrent, L"models", modelId);
    concurrentModel.SetNamedValue(L"keep_loaded", JsonValue::CreateBooleanValue(true));
    replace_entity(concurrent, L"models", concurrentModel);
    co_await core->Request(L"PUT", L"/manager/config", concurrent);
    co_await SaveEditor();
    setText(L"context", L"8193");
    co_await resume_after(std::chrono::milliseconds(1400)); co_await ResumeUI{DispatcherQueue()};
    check(!editor->dirty && editorSaveState.Text()==L"已自動儲存", L"設定未自動儲存。");
    setText(L"context", L"8192"); co_await SaveEditor();
    auto saved = co_await core->Request(L"GET", L"/manager/config");
    check(number(entity(saved, L"models", modelId), L"context") == 8192, L"Context 沒有保存。");
    check(number(entity(saved, L"models", modelId), L"output_percent") == 50,
          L"比例滑桿的修改沒有保存。");
    check(flag(entity(saved, L"models", modelId), L"keep_loaded"), L"覆寫了未編輯的並行欄位。");
    check(flag(object(saved, L"native_editor_unknown"), L"preserve"), L"未知設定遺失。");
    check(value(entity(saved, L"models", modelId), L"temperature").ValueType() ==
              JsonValueType::Null,
          L"可選採樣欄位沒有保持 null：" + str(entity(saved, L"models", modelId), L"temperature") +
              L" / 草稿：" + before->Field(L"temperature").text);
    check(!editor->dirty, L"成功保存後仍標記草稿。");
    // A conflicting edit must fail before PUT and leave the user's control
    // intact.
    setText(L"context", L"4096");
    concurrent = clone(saved);
    concurrentModel = entity(concurrent, L"models", modelId);
    concurrentModel.SetNamedValue(L"context", JsonValue::CreateNumberValue(16384));
    replace_entity(concurrent, L"models", concurrentModel);
    co_await core->Request(L"PUT", L"/manager/config", concurrent);
    bool conflict = false;
    try {
        co_await SaveEditor();
    } catch (hresult_error const &e) {
        conflict = std::wstring(e.message()).find(L"草稿仍保留") != std::wstring::npos;
    }
    check(conflict && editor->dirty && inputs.at(L"context").as<TextBox>().Text() == L"4096",
          L"衝突沒有保留草稿：" + to_hstring(conflict) + L" / dirty=" + to_hstring(editor->dirty) +
              L" / text=" + inputs.at(L"context").as<TextBox>().Text() + L" / " +
              Notice().Message());
    check(number(entity(co_await core->Request(L"GET", L"/manager/config"), L"models", modelId),
                 L"context") == 16384,
          L"衝突覆寫了伺服器。");
    Notice().IsOpen(false);
    // Numeric validation must not mutate server state.
    editor->dirty = false;
    config = co_await core->Request(L"GET", L"/manager/config");
    ShowPage(L"模型庫");
    setText(L"context", L"511");
    bool invalid = false;
    try {
        co_await SaveEditor();
    } catch (hresult_error const &) {
        invalid = true;
    }
    check(invalid && editor->dirty, L"範圍錯誤沒有保留草稿。");
    Notice().IsOpen(false);
    // Exercise the actual navigation-away dialog: cancel, discard and save.
    auto cancelDraft = editor;
    Navigation().SelectedItem(Navigation().MenuItems().GetAt(2));
    co_await resume_after(std::chrono::milliseconds(250));
    co_await ResumeUI{DispatcherQueue()};
    co_await InvokeButton(L"取消");
    check(page == L"模型庫" && editor == cancelDraft && editor->dirty, L"取消切頁遺失草稿。");
    Navigation().SelectedItem(Navigation().MenuItems().GetAt(2));
    co_await resume_after(std::chrono::milliseconds(250));
    co_await ResumeUI{DispatcherQueue()};
    co_await InvokeButton(L"捨棄修改");
    check(page == L"使用模式", L"捨棄沒有切換頁面。");
    check(inputs.size() == 9 && !inputs.count(L"max_tokens"), L"模式仍顯示總生成上限。");
    auto profileId = editor->id;
    setText(L"name", L"\u3000ÉTUDE 測試模式\u00a0");
    choose(L"thinking_mode", L"on");
    choose(L"budget_mode", L"custom");
    check(fieldBlocks.at(L"thinking_budget").Visibility() == Visibility::Visible,
          L"自訂預算没有顯示。");
    setText(L"thinking_budget", L"64");
    setText(L"agent_tools", L"\u3000web\u00a0, read, \u3000, edit");
    setText(L"agent_instructions", L"保留使用者指令\n第二行");
    Navigation().SelectedItem(Navigation().MenuItems().GetAt(4));
    co_await resume_after(std::chrono::milliseconds(250));
    co_await ResumeUI{DispatcherQueue()};
    for(int i=0;i<100 && working;i++) { co_await resume_after(std::chrono::milliseconds(50)); co_await ResumeUI{DispatcherQueue()}; }
    check(page == L"系統", L"保存草稿沒有切頁。");
    saved = co_await core->Request(L"GET", L"/manager/config");
    auto profile = entity(saved, L"profiles", profileId);
    check(str(profile, L"name") == L"ÉTUDE 測試模式" &&
              array(profile, L"agent_tools").GetAt(0).GetString() == L"web",
          L"Unicode 空白處理與原版不同。");
    check(number(profile, L"thinking_budget") == 64 && array(profile, L"agent_tools").Size() == 3,
          L"模式設定或工具陣列沒有保存。");
    check(inputs.size() == 12, L"系統欄位規格不完整。");
    setText(L"idle_minutes", L"7");
    setText(L"model_dirs", hstring(core->dataDir.wstring()) + L"\n  \n");
    checked(L"log_request_bodies", true);
    co_await SaveEditor();
    saved = co_await core->Request(L"GET", L"/manager/config");
    check(number(saved, L"idle_minutes") == 7 && array(saved, L"model_dirs").Size() == 1 &&
              flag(saved, L"log_request_bodies"),
          L"系統設定沒有保存。");
    co_await Capture(L"系統");
    ShowPage(L"使用模式");
    entitySearch = L"étude";
    PopulateEntities(L"profiles");
    check(entityList.Items().Size() == 1, L"搜尋沒有使用序數大小寫比對。");
    entitySearch = L"";
    PopulateEntities(L"profiles");
    RestoreEntitySelection(L"profiles");
    co_await Capture(L"使用模式");
    co_await InvokeButton(L"複製");
    check(array(config, L"profiles").Size() == array(saved, L"profiles").Size() + 1,
          L"複製模式未保存。");
    check(str(entity(config, L"profiles", selectedProfile), L"agent_instructions") ==
              str(profile, L"agent_instructions"),
          L"複製模式遺失指令：source=" + profileId + L" selected=" + selectedProfile +
              L" expected=" + str(profile, L"agent_instructions") + L" actual=" +
              str(entity(config, L"profiles", selectedProfile), L"agent_instructions"));
    co_await InvokeButton(L"新增");
    check(str(entity(config, L"profiles", selectedProfile), L"name") == L"新的使用模式",
          L"新增模式未保存。");
    auto addedId = selectedProfile;
    auto deletion = InvokeButton(L"刪除此模式");
    co_await resume_after(std::chrono::milliseconds(250));
    co_await ResumeUI{DispatcherQueue()};
    co_await InvokeButton(L"取消");
    co_await deletion;
    check(entity(config, L"profiles", addedId).Size() != 0, L"取消刪除仍移除了模式。");
    deletion = InvokeButton(L"刪除此模式");
    co_await resume_after(std::chrono::milliseconds(250));
    co_await ResumeUI{DispatcherQueue()};
    co_await InvokeButton(L"確認");
    co_await deletion;
    check(entity(config, L"profiles", addedId).Size() == 0 &&
              array(config, L"profiles").Size() == array(saved, L"profiles").Size() + 1,
          L"確認刪除模式沒有保存。");
    // Restore fixture data using the same API; no production directory is used.
    config = co_await core->Request(L"PUT", L"/manager/config", original);
    ShowPage(L"模型庫");
    co_await Capture(L"模型庫");
    JsonObject result;
    result.SetNamedValue(L"ok", JsonValue::CreateBooleanValue(true));
    for (auto key :
         {L"editor_fields", L"dynamic_fields", L"dirty_navigation", L"conflict_preserved",
          L"unknown_fields_preserved", L"optional_sampling", L"profile_array_save", L"system_save",
          L"profile_add_duplicate", L"profile_delete_confirm", L"unicode_editor_search"})
        result.SetNamedValue(key, JsonValue::CreateBooleanValue(true));
    result.SetNamedValue(L"ui_parity_verified", JsonValue::CreateBooleanValue(false));
    TestResult(result);
}
} // namespace winrt::AMIEBL::Native::implementation
