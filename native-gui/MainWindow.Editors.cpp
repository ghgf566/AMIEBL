#include "pch.h"
#include "MainWindow.xaml.h"
#include "UiAwait.h"
#include <winrt/Microsoft.UI.Xaml.Automation.h>
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
static bool ContainsOrdinalIgnoreCase(hstring text, hstring query) {
    if (query.empty())
        return true;
    if (query.size() > text.size())
        return false;
    for (uint32_t i = 0; i <= text.size() - query.size(); ++i)
        if (CompareStringOrdinal(text.c_str() + i, static_cast<int>(query.size()), query.c_str(),
                                 static_cast<int>(query.size()), TRUE) == CSTR_EQUAL)
            return true;
    return false;
}
void MainWindow::PopulateEntities(hstring collection) {
    if (!entityList)
        return;
    selecting = true;
    entityList.Items().Clear();
    for (auto v : array(config, collection)) {
        auto data = v.GetObject();
        auto id = str(data, L"id"), name = str(data, L"name");
        auto search =
            name + L" " + id + (collection == L"models" ? L" " + str(data, L"path") : L"");
        if (!ContainsOrdinalIgnoreCase(search, entitySearch))
            continue;
        if (collection == L"models" && id == str(config, L"default_model_id"))
            name = name + L" · 預設";
        ListViewItem item;
        item.Tag(box_value(id));
        item.Content(box_value(name));
        entityList.Items().Append(item);
    }
    selecting = false;
}
void MainWindow::RestoreEntitySelection(hstring collection) {
    if (!entityList)
        return;
    selecting = true;
    entityList.SelectedItem(nullptr);
    auto id = collection == L"models" ? selectedModel : selectedProfile;
    for (auto v : entityList.Items())
        if (unbox_value<hstring>(v.as<ListViewItem>().Tag()) == id) {
            entityList.SelectedItem(v);
            break;
        }
    selecting = false;
}
void MainWindow::OpenEditor(hstring collection, hstring id) {
    auto data = entity(config, collection, id);
    if (data.Size() == 0)
        throw hresult_error(E_FAIL, L"此項目已被移除，請重新整理。");
    if (collection == L"models")
        selectedModel = id;
    else
        selectedProfile = id;
    editor = std::make_shared<EditorDraft>(collection, data, EditorSchema(collection, config));
    entityEditor.Content(BuildForm(editor));
}
UIElement MainWindow::BuildEntities(hstring collection) {
    Grid grid;
    grid.ColumnSpacing(24);
    ColumnDefinition a;
    a.Width({240, GridUnitType::Pixel});
    grid.ColumnDefinitions().Append(a);
    ColumnDefinition b;
    b.Width({1, GridUnitType::Star});
    grid.ColumnDefinitions().Append(b);
    Grid left;
    left.RowSpacing(12);
    for (int i = 0; i < 4; i++) {
        RowDefinition row;
        row.Height(i == 1 ? GridLength{1, GridUnitType::Star} : GridLengthHelper::Auto());
        left.RowDefinitions().Append(row);
    }
    TextBox search;
    search.PlaceholderText(L"搜尋名稱／ID");
    search.TextChanged([weak = get_weak(), collection](auto const &sender, auto const &) {
        if (auto s = weak.get())
            if (!s->working) {
                s->entitySearch = sender.template as<TextBox>().Text();
                s->PopulateEntities(collection);
            }
    });
    left.Children().Append(search);
    entityList = ListView();
    entityList.SelectionMode(ListViewSelectionMode::Single);
    entityList.Foreground(
        Microsoft::UI::Xaml::Media::SolidColorBrush(Windows::UI::Color{255, 240, 243, 249}));
    Grid::SetRow(entityList, 1);
    left.Children().Append(entityList);
    entityEditor = ContentControl();
    entityEditor.HorizontalContentAlignment(HorizontalAlignment::Stretch);
    Grid::SetColumn(entityEditor, 1);
    grid.Children().Append(left);
    grid.Children().Append(entityEditor);
    entityList.SelectionChanged([weak = get_weak(), collection](auto const &, auto const &) {
        if (auto s = weak.get()) {
            if (s->selecting)
                return;
            auto item = s->entityList.SelectedItem().try_as<ListViewItem>();
            if (!item)
                return;
            auto id = unbox_value<hstring>(item.Tag());
            if (s->working) {
                s->RestoreEntitySelection(collection);
                return;
            }
            s->Run([s, collection, id]() -> IAsyncAction {
                try {
                    if (co_await s->LeaveEditor())
                        s->OpenEditor(collection, id);
                } catch (...) {
                    s->RestoreEntitySelection(collection);
                    throw;
                }
                s->RestoreEntitySelection(collection);
            });
        }
    });
    auto actions = collection == L"models"
                       ? Row({Action(L"加入 GGUF", [this] { return AddModel(); }),
                              Action(L"掃描", [this] { return ScanModels(); })})
                       : Row({Action(L"新增", [this] { return AddProfile(); }),
                              Action(L"複製", [this] { return DuplicateProfile(); })});
    Grid::SetRow(actions, 2);
    left.Children().Append(actions);
    if (collection == L"models") {
        auto locations = BuildModelLocations();
        Grid::SetRow(locations.as<FrameworkElement>(), 3);
        left.Children().Append(locations);
    }
    PopulateEntities(collection);
    auto id = collection == L"models" ? selectedModel : selectedProfile;
    if (entity(config, collection, id).Size() == 0 && array(config, collection).Size() > 0)
        id = str(array(config, collection).GetAt(0).GetObject(), L"id");
    if (!id.empty()) {
        OpenEditor(collection, id);
        RestoreEntitySelection(collection);
    } else
        entityEditor.Content(Text(L"請先新增項目。", 18));
    return grid;
}
UIElement MainWindow::BuildSystem() {
    editor = std::make_shared<EditorDraft>(L"system", config, EditorSchema(L"system", config));
    return BuildForm(editor);
}
void MainWindow::EditField(hstring key, hstring text) {
    if (syncingFields || !editor)
        return;
    if (initialControlText.count(key) && text == initialControlText.at(key))
        text = initialFieldText.at(key);
    editor->Edit(key, text);
    UpdateFooter();
    UpdateDependencies();
    auto it = sliders.find(key);
    if (it != sliders.end())
        try {
            auto n = editor->Field(key).Parse();
            if (n.ValueType() != JsonValueType::Number)
                return;
            bool log = key != L"cpu_threads";
            double p = log ? std::log2(std::max(1.0, n.GetNumber())) : n.GetNumber();
            syncingFields = true;
            it->second.Value(std::clamp(p, it->second.Minimum(), it->second.Maximum()));
            syncingFields = false;
        } catch (hresult_error const &) {
            syncingFields = false;
        }
}
void MainWindow::UpdateDependencies() {
    if (!editor)
        return;
    auto preview = editor->Preview();
    for (auto const &f : editor->fields)
        if (f.spec.enabled && fieldBlocks.count(f.spec.key))
            fieldBlocks.at(f.spec.key)
                .Visibility(f.spec.enabled(preview) ? Visibility::Visible : Visibility::Collapsed);
}
static hstring ChoiceLabel(hstring key, hstring id, JsonObject const &config) {
    if (key == L"default_profile_id")
        return str(entity(config, L"profiles", id), L"name", id);
    std::map<hstring, std::map<hstring, hstring>> labels = {
        {L"thinking_mode",
         {{L"auto", L"自動判斷"},
          {L"on", L"固定開啟"},
          {L"off", L"固定關閉"},
          {L"model", L"跟隨模型預設"}}},
        {L"reasoning_level",
         {{L"light", L"輕量"}, {L"balanced", L"均衡"}, {L"deep", L"深入"}, {L"extreme", L"極深"}}},
        {L"budget_mode", {{L"auto", L"自動"}, {L"custom", L"自訂（需引擎支援）"}}},
        {L"mtp_source", {{L"native", L"模型內建"}, {L"external", L"外部 Draft"}}},
        {L"agent_sync_mode",
         {{L"preserve", L"保留手動工具與指令"}, {L"managed", L"由 AMIEBL 管理"}}}};
    return labels.count(key) && labels[key].count(id) ? labels[key][id] : id;
}
UIElement MainWindow::BuildForm(std::shared_ptr<EditorDraft> draft) {
    inputs.clear();
    initialFieldText.clear();
    initialControlText.clear();
    sliders.clear();
    fieldBlocks.clear();
    editorExpanders.clear();
    auto panel = Panel();
    if (draft->collection == L"system") {
        panel.Children().Append(Action(L"檢查連線", [this] { return CheckConnection(); }));
        panel.Children().Append(Text(L"資料位置：" + hstring(core->dataDir.wstring()), 12));
    }
    if (draft->collection == L"models") {
        capabilityText = Text(ModelCapability(draft->id));
        panel.Children().Append(Card(L"Thinking 支援 · 自動偵測", capabilityText));
    }
    panel.Children().Append(
        Text(draft->collection == L"models" ? L"載入參數需重新載入；採樣與預設模式供後續請求使用。"
             : draft->collection == L"profiles"
                 ? L"客戶端明確 reasoning 設定優先；自訂上限需引擎辨識思考結束標記。"
                 : L"連線埠與啟動設定可能需重啟才能生效。"));
    if (draft->collection != L"system")
        panel.Children().Append(Text(L"識別：" + draft->id));
    std::map<hstring, StackPanel> sections;
    for (auto const &f : draft->fields) {
        auto spec = f.spec;
        auto key = spec.key;
        auto block = Panel();
        Control control{nullptr};
        if (spec.kind == FieldKind::Boolean) {
            CheckBox box;
            box.Content(box_value(spec.label));
            box.IsChecked(f.text == L"true");
            box.Checked([weak = get_weak(), draft, key](auto const &, auto const &) {
                if (auto s = weak.get())
                    if (s->editor == draft)
                        s->EditField(key, L"true");
            });
            box.Unchecked([weak = get_weak(), draft, key](auto const &, auto const &) {
                if (auto s = weak.get())
                    if (s->editor == draft)
                        s->EditField(key, L"false");
            });
            control = box;
        } else if (spec.kind == FieldKind::Choice) {
            ComboBox box;
            box.Header(box_value(spec.label));
            box.HorizontalAlignment(HorizontalAlignment::Stretch);
            for (auto const &id : spec.options) {
                ComboBoxItem item;
                item.Tag(box_value(id));
                item.Content(box_value(ChoiceLabel(key, id, config)));
                box.Items().Append(item);
                if (id == f.text)
                    box.SelectedItem(item);
            }
            box.SelectionChanged([weak = get_weak(), draft, key](auto const &sender, auto const &) {
                if (auto s = weak.get())
                    if (s->editor == draft) {
                        auto item =
                            sender.template as<ComboBox>().SelectedItem().try_as<ComboBoxItem>();
                        if (item)
                            s->EditField(key, unbox_value<hstring>(item.Tag()));
                    }
            });
            control = box;
        } else {
            TextBox box;
            box.Header(box_value(spec.label));
            box.Text(f.text);
            box.AcceptsReturn(spec.kind == FieldKind::Multiline);
            box.TextWrapping(TextWrapping::Wrap);
            box.PlaceholderText(spec.optional ? L"空白 = 自動／預設" : L"");
            box.TextChanged([weak = get_weak(), draft, key](auto const &sender, auto const &) {
                if (auto s = weak.get())
                    if (s->editor == draft)
                        s->EditField(key, sender.template as<TextBox>().Text());
            });
            control = box;
        }
        initialFieldText[key] = f.text;
        initialControlText[key] = control.try_as<TextBox>() ? control.as<TextBox>().Text() : f.text;
        inputs.insert_or_assign(key, control);
        fieldBlocks.insert_or_assign(key, block);
        block.Children().Append(control);
        if (key == L"path" || key == L"mmproj" || key == L"mtp_draft_path" ||
            key == L"engine_dir") {
            block.Children().Append(Action(key == L"engine_dir" ? L"選擇資料夾" : L"選擇 GGUF",
                                           [this, draft, key]() -> IAsyncAction {
                                               auto path = key == L"engine_dir"
                                                               ? co_await PickFolder()
                                                               : co_await PickFile(L".gguf");
                                               if (!path.empty() && editor == draft)
                                                   inputs.at(key).as<TextBox>().Text(path);
                                           }));
        }
        if (key == L"context" || key == L"cpu_threads" || key == L"thinking_budget" ||
            key == L"max_tokens") {
            bool logarithmic = key != L"cpu_threads";
            double minimum = key == L"context" ? 512 : key == L"max_tokens" ? 256 : 0;
            double nativeContext = number(entity(config, L"models", draft->id), L"native_context");
            SYSTEM_INFO sys{};
            GetSystemInfo(&sys);
            double maximum =
                key == L"context"
                    ? (nativeContext > 0 ? std::clamp(nativeContext, 512.0, 2097152.0) : 2097152)
                : key == L"cpu_threads" ? std::max(1ul, sys.dwNumberOfProcessors)
                                        : 1048576;
            Slider slider;
            slider.Minimum(logarithmic ? std::log2(std::max(1.0, minimum)) : minimum);
            slider.Maximum(logarithmic ? std::log2(maximum) : maximum);
            slider.StepFrequency(1);
            slider.Header(box_value(L"快速調整"));
            slider.HorizontalAlignment(HorizontalAlignment::Stretch);
            Microsoft::UI::Xaml::Automation::AutomationProperties::SetName(slider,
                                                                           spec.label + L"滑桿");
            try {
                auto n = f.Parse().GetNumber();
                slider.Value(std::clamp(logarithmic ? std::log2(std::max(1.0, n)) : n,
                                        slider.Minimum(), slider.Maximum()));
            } catch (hresult_error const &) {
            }
            slider.ValueChanged([weak = get_weak(), draft, key, logarithmic,
                                 minimum](auto const &sender, auto const &) {
                if (auto s = weak.get())
                    if (s->editor == draft && !s->syncingFields) {
                        double p = sender.template as<Slider>().Value();
                        double n = logarithmic
                                       ? (p == 0 && minimum == 0 ? 0 : std::round(std::pow(2, p)))
                                       : std::round(p);
                        s->inputs.at(key).as<TextBox>().Text(to_hstring(static_cast<int>(n)));
                    }
            });
            sliders[key] = slider;
            block.Children().Append(slider);
        }
        auto hint = FieldHelp(key, draft->collection);
        if (!hint.empty()) {
            auto help = Text(hint, 12);
            help.Opacity(.72);
            block.Children().Append(help);
        }
        auto title = FieldSection(key, draft->collection);
        if (!sections.count(title)) {
            auto section = Panel();
            sections[title] = section;
            if (title == L"進階採樣" || title == L"VS Code Agent") {
                auto e = SmoothExpander::Create(title, section);
                panel.Children().Append(e->root);
                editorExpanders.push_back(e);
                auto scroll = std::make_shared<ScrollViewer>(nullptr);
                auto start = std::make_shared<double>(0);
                auto target = std::make_shared<double>(0);
                std::weak_ptr<SmoothExpander> weakExpander = e;
                e->native.Expanding([weak = get_weak(), weakExpander, scroll, start,
                                     target](auto const &, auto const &) {
                    if (auto s = weak.get())
                        if (auto p = weakExpander.lock()) {
                            s->revealingEditor = p;
                            *scroll = s->editorScroll;
                            *start = (*scroll).VerticalOffset();
                            *target = std::max(
                                *start,
                                static_cast<double>(
                                    p->root.TransformToVisual((*scroll).Content().as<UIElement>())
                                        .TransformPoint({0, 0})
                                        .Y) -
                                    12);
                        }
                });
                e->changed = [weak = get_weak(), weakExpander, scroll, start, target] {
                    if (auto s = weak.get())
                        if (auto p = weakExpander.lock())
                            if (*scroll && *scroll == s->editorScroll && p->native.IsExpanded() &&
                                s->revealingEditor.lock() == p) {
                                (*scroll).ChangeView(
                                    nullptr,
                                    std::clamp(*start + (*target - *start) * p->progress, 0.0,
                                               (*scroll).ScrollableHeight()),
                                    nullptr, true);
                            }
                };
            } else
                panel.Children().Append(Card(title, section));
        }
        sections.at(title).Children().Append(block);
    }
    UpdateDependencies();
    auto save = Row({Action(L"儲存設定", [this] { return SaveEditor(); }),
                     Action(L"重新讀取", [this, draft]() -> IAsyncAction {
                         if (editor && editor->dirty &&
                             !(co_await Confirm(L"捨棄尚未儲存的修改，重新讀取已保存設定？")))
                             co_return;
                         auto fresh = co_await core->Request(L"GET", L"/manager/config");
                         config = fresh;
                         if (draft->collection == L"system")
                             ShowPage(L"系統");
                         else {
                             PopulateEntities(draft->collection);
                             OpenEditor(draft->collection, draft->id);
                             RestoreEntitySelection(draft->collection);
                         }
                     })});
    if (draft->collection == L"models")
        panel.Children().Append(
            Row({Action(L"載入／重新載入",
                        [this, draft]() -> IAsyncAction {
                            co_await SaveEditor();
                            co_await core->Request(
                                L"POST", L"/manager/load",
                                body(L"model_id", JsonValue::CreateStringValue(draft->id)));
                            co_await Poll();
                        }),
                 Action(L"設為預設",
                        [this, draft]() -> IAsyncAction {
                            co_await SaveEditor();
                            co_await SaveConfig([draft](auto const &c) {
                                c.SetNamedValue(L"default_model_id",
                                                JsonValue::CreateStringValue(draft->id));
                            });
                            PopulateEntities(L"models");
                            RestoreEntitySelection(L"models");
                            Message(L"已設為預設模型。");
                        }),
                 DangerAction(L"移除登錄", [this] { return DeleteEntity(); })}));
    else if (draft->collection == L"profiles")
        panel.Children().Append(DangerAction(L"刪除此模式", [this] { return DeleteEntity(); }));
    else
        panel.Children().Append(
            Card(L"設定備份與還原", Row({Action(L"匯出設定", [this] { return Export(); }),
                                         Action(L"匯入設定", [this] { return Import(); })})));
    Grid layout;
    layout.RowSpacing(12);
    RowDefinition a;
    a.Height({1, GridUnitType::Star});
    layout.RowDefinitions().Append(a);
    RowDefinition b;
    b.Height(GridLengthHelper::Auto());
    layout.RowDefinitions().Append(b);
    editorScroll = Scroll(panel);
    editorScroll.PointerWheelChanged([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->revealingEditor.reset();
    });
    editorScroll.PointerPressed([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get())
            s->revealingEditor.reset();
    });
    layout.Children().Append(editorScroll);
    Grid::SetRow(save, 1);
    layout.Children().Append(save);
    return layout;
}
hstring MainWindow::ModelCapability(hstring id) {
    auto model = entity(config, L"models", id);
    auto cap = str(model, L"reasoning_capability", L"unknown");
    hstring supported = cap == L"toggle"   ? L"可切換 Thinking / Non-Thinking"
                        : cap == L"always" ? L"具有思考能力；模板未提供可靠的關閉方式"
                        : cap == L"none"   ? L"未偵測到可控制的思考能力"
                                           : L"尚未確認思考能力";
    auto join = [](JsonArray const &a) {
        hstring s;
        for (auto v : a) {
            if (!s.empty())
                s = s + L" / ";
            s = s + v.GetString();
        }
        return s.empty() ? hstring(L"未提供") : s;
    };
    auto effort = str(model, L"reasoning_default_effort");
    return supported + L"\n原生 Effort：" + join(array(model, L"reasoning_efforts")) +
           L"\n模型預設 Effort：" + (trim(effort).empty() ? hstring(L"未提供") : effort) + L"\n" +
           (flag(model, L"reasoning_budget_supported")
                ? hstring(L"模板含思考標記，可嘗試自動 Budget。")
                : hstring(L"尚未確認自動 Budget；自訂上限仍會傳給引擎。")) +
           L"\n原生 Effort 控制思考深度；自訂 token "
           L"預算控制上限。是否能截斷仍取決於 llama.cpp "
           L"parser，傳送參數不代表已生效。" +
           L"\n偵測來源：" +
           (str(model, L"reasoning_detection") == L"runtime" ? hstring(L"llama.cpp /props 驗證")
            : str(model, L"reasoning_detection") == L"gguf"  ? hstring(L"GGUF Chat Template")
                                                             : hstring(L"尚未完成偵測")) +
           L"\n模板開關：" + join(array(model, L"reasoning_toggle_keys")) + L"\n原生 Context：" +
           (number(model, L"native_context") > 0 ? str(model, L"native_context")
                                                 : hstring(L"未知")) +
           L" · MTP：" + str(model, L"mtp_capability", L"未知") + L"\n已儲存 Context：" +
           str(model, L"context") + L" · 引擎使用中：" +
           (str(status, L"model_id") == id
                ? str(object(status, L"loaded_model_settings"), L"context", L"尚未載入")
                : hstring(L"此模型未載入"));
}
} // namespace winrt::AMIEBL::Native::implementation

namespace winrt::AMIEBL::Native::implementation {
void MainWindow::SnapshotEditorControls() {
    if (!editor)
        return;
    for (auto const &[key, control] : inputs) {
        if (auto text = control.try_as<Microsoft::UI::Xaml::Controls::TextBox>())
            EditField(key, text.Text());
        else if (auto box = control.try_as<Microsoft::UI::Xaml::Controls::CheckBox>())
            EditField(key, box.IsChecked() && box.IsChecked().Value() ? L"true" : L"false");
        else if (auto choice = control.try_as<Microsoft::UI::Xaml::Controls::ComboBox>())
            if (auto item =
                    choice.SelectedItem().try_as<Microsoft::UI::Xaml::Controls::ComboBoxItem>())
                EditField(key, unbox_value<hstring>(item.Tag()));
    }
}
} // namespace winrt::AMIEBL::Native::implementation
