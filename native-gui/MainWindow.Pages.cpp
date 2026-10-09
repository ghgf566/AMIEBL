#include "pch.h"
#include "MainWindow.xaml.h"
#include <shellapi.h>
namespace winrt::AMIEBL::Native::implementation {
using namespace Microsoft::UI::Xaml;
using namespace Microsoft::UI::Xaml::Controls;
using namespace Microsoft::UI::Xaml::Media;
using namespace Windows::Foundation;
using namespace Windows::Data::Json;
using namespace amiebl;
UIElement MainWindow::BuildOverview() {
    auto panel = Panel(), state = Panel();
    overviewModel = Text(L"", 20);
    overviewState = Text(L"");
    state.Children().Append(overviewModel);
    state.Children().Append(overviewState);
    state.Children().Append(Text(L"收到推理請求後，管理器會載入指定模型。", 12));
    state.Children().Append(Row({Action(L"同步至 VS Code", [this] { return ConnectVSCode(); }),
                                 Action(L"載入預設模型", [this] { return LoadDefault(); }),
                                 Action(L"卸載模型", [this] { return Unload(); })}));
    panel.Children().Append(Card(L"模型與服務", state));
    auto engine = Panel();
    overviewEngine = Text(L"正在讀取 server 狀態…");
    engine.Children().Append(overviewEngine);
    engine.Children().Append(Row({Action(L"停止推理引擎",[this]() -> IAsyncAction {
        if (!co_await Confirm(L"停止 server 並取消任務？模型會卸載，接收新任務會暫停；AMIEBL 繼續運作。")) co_return;
        co_await core->Request(L"POST",L"/manager/engines/stop",JsonObject());
        co_await Poll();
    }), Action(L"引擎管理",[this]() -> IAsyncAction { ShowPage(L"系統"); co_return; })}));
    panel.Children().Append(Card(L"推理引擎 · server",engine));
    Grid metrics;
    metrics.ColumnSpacing(16);
    for (int i = 0; i < 2; i++) {
        ColumnDefinition c;
        c.Width({1, GridUnitType::Star});
        metrics.ColumnDefinitions().Append(c);
    }
    overviewQueue = Text(L"", 18);
    overviewMemory = Text(L"");
    metrics.Children().Append(Card(L"任務佇列", overviewQueue));
    auto memory = Card(L"記憶體用量", overviewMemory);
    Grid::SetColumn(memory, 1);
    metrics.Children().Append(memory);
    panel.Children().Append(metrics);
    panel.Children().Append(
        Card(L"服務控制", Row({Action(L"暫停／恢復接收", [this] { return ToggleAccepting(); }),
                               Action(L"保持載入／恢復卸載", [this] { return ToggleKeep(); })})));
    auto connection = Panel();
    overviewConnection = Text(L"");
    connection.Children().Append(overviewConnection);
    connection.Children().Append(
        Text(L"同步模型清單、模式名稱與 Agent 設定，並備份現有內容。", 12));
    panel.Children().Append(Card(L"VS Code 整合", connection));
    overviewError = Text(L"");
    overviewErrorCard = Card(L"最近錯誤", overviewError);
    panel.Children().Append(overviewErrorCard);
    UpdateOverview();
    overviewScroll = Scroll(panel);
    return overviewScroll;
}
void MainWindow::UpdateOverview() {
    if (!overviewModel)
        return;
    overviewModel.Text(L"模型：" + str(status, L"model_name", L"尚未載入"));
    auto serverState=str(engineRuntime,L"state");
    hstring serverLabel=L"正在讀取";
    if(serverState==L"running") serverLabel=L"運行中 · 健康檢查正常";
    else if(serverState==L"starting") serverLabel=L"程序運行中 · server 尚未就緒";
    else if(serverState==L"stopped") serverLabel=L"已停止";
    else if(serverState==L"not_installed") serverLabel=L"未安裝或尚未選用";
    else if(serverState==L"unresponsive") serverLabel=L"程序運行中 · 服務無回應或回應異常";
    else if(serverState==L"exited") serverLabel=L"程序已退出 · "+str(engineRuntime,L"exit_detail");
    else if(serverState==L"external") serverLabel=L"外部服務 · AMIEBL 不管理此程序";
    else if(serverState==L"port_in_use") serverLabel=L"連接埠由其他程序占用";
    overviewEngine.Text(L"llama.cpp · "+serverLabel+L"\n版本："+str(engineRuntime,L"version",L"未取得")+
        L" · "+str(engineRuntime,L"build_tag")+L" · 套件："+str(engineRuntime,L"backend",L"外部指定")+
        L"\nPID："+str(engineRuntime,L"pid",L"—")+L" · "+str(engineRuntime,L"url"));
    overviewState.Text(L"狀態：" + Phase(str(status, L"state", L"unloaded")) + L" · " +
                       ApplicationState());
    overviewQueue.Text(L"執行中：" + str(status, L"active_count", L"0") + L"\n等待中：" +
                       str(status, L"queued_count", L"0"));
    auto r = object(status, L"resources");
    overviewMemory.Text(L"RAM：" + metric(r, L"ram_used_gb", L" GB") + L" / " +
                        metric(r, L"ram_total_gb", L" GB") + L"\nGPU：" +
                        metric(r, L"gpu_used_mib", L" MiB") + L" / " +
                        metric(r, L"gpu_total_mib", L" MiB"));
    overviewConnection.Text(L"API：" + str(status, L"api_url") + L"\n引擎版本：" +
                            str(status, L"engine_version", L"未取得"));
    overviewError.Text(str(status, L"last_error"));
    overviewErrorCard.Visibility(overviewError.Text().empty() ? Visibility::Collapsed
                                                              : Visibility::Visible);
}
UIElement MainWindow::BuildTasks() {
    Grid root;
    root.RowSpacing(16);
    RowDefinition a;
    a.Height({1, GridUnitType::Star});
    root.RowDefinitions().Append(a);
    RowDefinition b;
    b.Height(GridLengthHelper::Auto());
    root.RowDefinitions().Append(b);
    Grid split;
    split.ColumnSpacing(20);
    ColumnDefinition c;
    c.Width({260, GridUnitType::Pixel});
    split.ColumnDefinitions().Append(c);
    ColumnDefinition d;
    d.Width({1, GridUnitType::Star});
    split.ColumnDefinitions().Append(d);
    taskList = ListView();
    taskList.SelectionMode(ListViewSelectionMode::Single);
    taskList.SelectionChanged([weak = get_weak()](auto const &, auto const &) {
        if (auto s = weak.get()) {
            if (s->selecting)
                return;
            auto item = s->taskList.SelectedItem().try_as<ListViewItem>();
            s->selectedTask = item ? unbox_value<hstring>(item.Tag()) : L"";
            s->UpdateTaskDetail();
        }
    });
    split.Children().Append(Card(L"最近任務", taskList));
    taskDetail = ContentControl();
    taskDetail.HorizontalContentAlignment(HorizontalAlignment::Stretch);
    auto detail = Scroll(taskDetail);
    Grid::SetColumn(detail, 1);
    split.Children().Append(detail);
    root.Children().Append(split);
    logBox = TextBox();
    logBox.IsReadOnly(true);
    logBox.AcceptsReturn(true);
    logBox.TextWrapping(TextWrapping::NoWrap);
    logBox.Height(150);
    logBox.FontFamily(FontFamily(L"Consolas"));
    auto content = Panel();
    content.Children().Append(Text(L"預設只保留狀態與錯誤；完整請求記錄可在系統頁開啟。", 12));
    content.Children().Append(logBox);
    content.Children().Append(
        Row({Action(L"複製紀錄",
                    [this]() -> IAsyncAction {
                        Windows::ApplicationModel::DataTransfer::DataPackage data;
                        data.SetText(logBox.Text());
                        Windows::ApplicationModel::DataTransfer::Clipboard::SetContent(data);
                        Message(L"紀錄已複製。");
                        co_return;
                    }),
             Action(L"開啟紀錄資料夾",
                    [this]() -> IAsyncAction {
                        ShellExecuteW(nullptr, L"open", core->dataDir.c_str(), nullptr, nullptr,
                                      SW_SHOWNORMAL);
                        co_return;
                    }),
             Action(L"清除紀錄", [this]() -> IAsyncAction {
                 if (co_await Confirm(L"清除已完成的任務與服務紀錄？"))
                     co_await core->Request(L"POST", L"/manager/records/clear");
                 co_await Poll();
             })}));
    serviceLogExpander = SmoothExpander::Create(L"服務執行紀錄", content, ExpandDirection::Up);
    Grid::SetRow(serviceLogExpander->root, 1);
    root.Children().Append(serviceLogExpander->root);
    UpdateTasks();
    return root;
}
void MainWindow::UpdateTasks() {
    if (!taskList)
        return;
    selecting = true;
    bool rebuild = taskList.Items().Size() != requests.Size();
    if (!rebuild)
        for (uint32_t i = 0; i < requests.Size(); i++)
            if (unbox_value<hstring>(taskList.Items().GetAt(i).as<ListViewItem>().Tag()) !=
                str(requests.GetAt(i).GetObject(), L"id"))
                rebuild = true;
    if (rebuild) {
        taskList.Items().Clear();
        for (auto v : requests) {
            ListViewItem item;
            item.Tag(box_value(str(v.GetObject(), L"id")));
            taskList.Items().Append(item);
        }
    }
    IInspectable selected{nullptr};
    for (uint32_t i = 0; i < requests.Size(); i++) {
        auto r = requests.GetAt(i).GetObject();
        auto item = taskList.Items().GetAt(i).as<ListViewItem>();
        item.Content(box_value(Phase(str(r, L"phase")) + L"\n" + str(r, L"profile_name") + L" · " +
                               metric(r, L"elapsed_seconds", L" 秒")));
        if (str(r, L"id") == selectedTask)
            selected = item;
    }
    if (!selected && taskList.Items().Size())
        selected = taskList.Items().GetAt(0);
    taskList.SelectedItem(selected);
    selectedTask = selected ? unbox_value<hstring>(selected.as<ListViewItem>().Tag()) : L"";
    selecting = false;
    if (logBox && logBox.Text() != logs)
        logBox.Text(logs);
    UpdateTaskDetail();
}
void MainWindow::UpdateTaskDetail() {
    if (!taskDetail)
        return;
    auto panel = Panel();
    JsonObject r;
    for (auto v : requests)
        if (str(v.GetObject(), L"id") == selectedTask)
            r = v.GetObject();
    if (!r.Size()) {
        panel.Children().Append(
            Card(L"尚未收到任務",
                 Text(L"從 VS Code 送出請求後，這裡會顯示上下文處理、思考與生成狀態。")));
        taskDetail.Content(panel);
        return;
    }
    panel.Children().Append(
        Card(L"任務狀態 · " + Phase(str(r, L"phase")),
             Text(L"模型：" + str(r, L"model_name") + L"\n使用模式：" + str(r, L"profile_name") +
                  L"\n耗時：" + metric(r, L"elapsed_seconds", L" 秒") + L" · 首 token：" +
                  metric(r, L"first_token_seconds", L" 秒"))));
    panel.Children().Append(
        Card(L"輸入與生成", Text(L"輸入：" + metric(r, L"prompt_tokens", L" tokens", L"0") +
                                 L" · 快取：" + metric(r, L"cached_tokens", L" tokens", L"0") +
                                 L"\n輸入處理：" + metric(r, L"prompt_tps", L" tok/s") +
                                 L"\n生成：" + metric(r, L"generated_tokens", L" tokens", L"0") +
                                 L" · 速度：" + metric(r, L"generation_tps", L" tok/s"))));
    panel.Children().Append(Card(
        L"本次思考策略", Text(L"決策：" + str(r, L"decision", L"未提供") + L" · 判斷耗時：" +
                              metric(r, L"classifier_seconds", L" 秒") + L"\n強度：" +
                              str(r, L"reasoning_level", L"未指定") + L" · 原生 Effort：" +
                              str(r, L"effort", L"未使用") + L"\n思考預算：" +
                              metric(r, L"thinking_budget", L" tokens", L"0") + L" · 已用：" +
                              metric(r, L"thinking_tokens", L" tokens", L"0") + L"\n總生成上限：" +
                              metric(r, L"max_tokens", L" tokens", L"0"))));
    if (!str(r, L"error").empty())
        panel.Children().Append(Card(L"錯誤原因", Text(str(r, L"error"))));
    if (str(r, L"phase") == L"cancelled")
        panel.Children().Append(
            Text(flag(r, L"cancel_confirmed") ? L"已確認停止" : L"等待引擎確認停止"));
    auto cancel = Action(L"停止選取任務", [this]() -> IAsyncAction {
        if (selectedTask.empty())
            co_return;
        co_await core->Request(L"POST", L"/manager/requests/" + Uri::EscapeComponent(selectedTask) +
                                            L"/cancel");
        co_await Poll();
    });
    auto phase = str(r, L"phase");
    cancel.IsEnabled(phase != L"completed" && phase != L"cancelled" && phase != L"error");
    panel.Children().Append(cancel);
    panel.Children().Append(Text(L"請求識別：" + str(r, L"id"), 12));
    taskDetail.Content(panel);
}
} // namespace winrt::AMIEBL::Native::implementation
