using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace LocalModelManager;

public partial class MainWindow
{
    private UIElement BuildProfiles()
    {
        var grid = Split();
        var left = new StackPanel();
        var list = new ListBox { MinHeight = 180, MaxHeight = 420 };
        foreach (var item in J.A(config, "profiles"))
        {
            var label = new StackPanel();
            label.Children.Add(Text(J.S(item, "name"), 15));
            label.Children.Add(Text(ThinkingName(J.S(item, "thinking_mode")) + " · " + J.S(item, "thinking_budget") + " 思考 tokens", 11, true));
            list.Items.Add(new ListBoxItem { Content = label, Tag = item });
        }
        left.Children.Add(list);
        var editor = new ContentControl();
        Put(grid, Scroll(left), 0); Put(grid, editor, 2);
        ListBoxItem? previous = null;
        bool restoring = false;
        list.SelectionChanged += async (_, _) => await Guard(async () =>
        {
            if (restoring || list.SelectedItem is not ListBoxItem selected || selected.Tag is not JsonObject profile) return;
            if (!await LeaveEditor()) { restoring = true; list.SelectedItem = previous; restoring = false; return; }
            previous = selected;
            editor.Content = Scroll(BuildProfileEditor(profile)); dirty = false;
        });
        left.Children.Add(ActionRow(Button("＋ 新增模式", async () =>
        {
            if (!await LeaveEditor()) return;
            string id = "profile-" + Guid.NewGuid().ToString("N")[..8];
            await SaveConfig(c => J.A(c, "profiles").Add(new JsonObject { ["id"] = id, ["name"] = "新的使用模式", ["thinking_mode"] = "auto", ["effort"] = "medium", ["thinking_budget"] = 1536, ["max_tokens"] = 8192 }));
            ShowPage("使用模式");
            SelectProfile(id);
        }, true)));
        left.Children.Add(Text("切換使用模式會調整每次請求，不需要重新載入模型。", 12, true));
        left.Children.Add(Text("Agent 可透過「模型識別::模式識別」明確指定模式。", 12, true));
        if (list.Items.Count > 0) list.SelectedIndex = 0;
        else editor.Content = Text("先新增一個使用模式。", 18);
        grid.Tag = list;
        return grid;
    }

    private void SelectProfile(string id)
    {
        if (PageContent.Content is Grid grid && grid.Tag is ListBox list)
            list.SelectedItem = list.Items.Cast<ListBoxItem>().FirstOrDefault(x => J.S(x.Tag as JsonObject, "id") == id);
    }

    private static string ThinkingName(string mode) => mode switch { "auto" => "自動判斷", "on" => "固定思考", "off" => "關閉思考", "model" => "跟隨模型預設", _ => mode };

    private UIElement BuildProfileEditor(JsonObject profile)
    {
        string id = J.S(profile, "id");
        var panel = Section("使用模式設定", "思考預算是上限，模型可以提早結束。每次請求會顯示實際套用結果。");
        var name = Field(panel, "顯示名稱", J.S(profile, "name"));
        Field(panel, "模式識別（供 Agent 指定）", id, true);
        var mode = Choice(panel, "思考策略", J.S(profile, "thinking_mode", "auto"), ("auto", "自動判斷 · 本機快速規則"), ("on", "固定開啟思考"), ("off", "固定關閉思考"), ("model", "跟隨模型預設"));
        var effort = Choice(panel, "思考程度／自動模式上限", J.S(profile, "effort", "medium"), ("low", "簡短 · low"), ("medium", "均衡 · medium"), ("xhigh", "深入 · xhigh"));
        var budget = Field(panel, "思考預算上限（tokens）", J.S(profile, "thinking_budget", "1536"));
        var max = Field(panel, "整次生成上限（包含思考與回答）", J.S(profile, "max_tokens", "8192"));
        var summary = Text("", 12, true);
        panel.Children.Add(summary);
        void RefreshSummary()
        {
            bool controls = Value(mode) is "auto" or "on";
            effort.IsEnabled = controls; budget.IsEnabled = controls;
            summary.Text = Value(mode) switch
            {
                "auto" => "管理器只檢查最近的使用者要求，以本機規則快速判斷是否開啟思考，不會額外呼叫模型。明確的問候、翻譯與改寫會直接回答；除錯、分析、規劃等任務會使用此模式的思考程度與預算；無法明確分類時採用此模式預設。",
                "off" => "要求支援此功能的模型直接回答。未支援思考開關的模型會保留原本行為。",
                "model" => "沿用模型模板的預設行為，不代表每一題都自動開關思考。",
                _ => "使用指定的思考程度與預算；實際可用程度取決於該模型的能力設定。"
            };
        }
        mode.SelectionChanged += (_, _) => RefreshSummary(); RefreshSummary();
        async Task Save()
        {
            var changed = Clone(profile);
            changed["name"] = Required(name, "模式名稱"); changed["thinking_mode"] = Value(mode); changed["effort"] = Value(effort);
            int budgetValue = Number(budget, "思考預算", 0, 1_000_000), maxValue = Number(max, "整次生成上限", 256, 1_000_000);
            if (Value(mode) is "auto" or "on" && budgetValue > maxValue - 256) throw new InvalidOperationException("整次生成上限需至少比思考預算多 256 tokens，為回答與工具呼叫保留空間。");
            changed["thinking_budget"] = budgetValue; changed["max_tokens"] = maxValue;
            await SaveConfig(c => { var profiles = J.A(c, "profiles"); int index = profiles.ToList().FindIndex(x => J.S(x, "id") == id); if (index < 0) throw new InvalidOperationException("此模式已被移除，請重新開啟使用模式頁。"); profiles[index] = changed; });
            ShowNotice("使用模式已儲存，新的請求會套用設定。");
        }
        pendingSave = Save;
        panel.Children.Add(ActionRow(Button("儲存使用模式", Save, true), Button("複製此模式", async () =>
        {
            await Save(); string newId = "profile-" + Guid.NewGuid().ToString("N")[..8];
            await SaveConfig(c => { var source = J.A(c, "profiles").First(x => J.S(x, "id") == id)!; var copied = Clone(source); copied["id"] = newId; copied["name"] = J.S(source, "name") + " 副本"; J.A(c, "profiles").Add(copied); });
            ShowPage("使用模式"); SelectProfile(newId);
        }), Button("刪除此模式", async () =>
        {
            if (J.A(config, "profiles").Count <= 1) throw new InvalidOperationException("至少需要保留一個使用模式。");
            if (!Confirm("刪除此模式後，使用此模式作為預設的模型會改用另一個模式。既有 Agent 的模式識別也需要更新。")) return;
            await SaveConfig(c =>
            {
                var profiles = J.A(c, "profiles"); var found = profiles.FirstOrDefault(x => J.S(x, "id") == id); if (found is not null) profiles.Remove(found);
                string fallback = J.S(profiles[0], "id");
                if (J.S(c, "default_profile_id") == id) c["default_profile_id"] = fallback;
                foreach (var model in J.A(c, "models").OfType<JsonObject>()) if (J.S(model, "default_profile_id") == id) model["default_profile_id"] = fallback;
            }); ShowPage("使用模式");
        })));
        var note = Section("和 Agent 的配合", "客戶端明確指定的思考控制會優先保留；若客戶端指定更低的輸出上限，也會遵守較低的上限。管理器會顯示最後採用的值。");
        panel.Children.Add(Card(note));
        return Card(panel);
    }

    private UIElement BuildTasks()
    {
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var grid = Split(250);
        var left = new DockPanel();
        var hint = Text("最近請求 · 持續更新", 12, true); DockPanel.SetDock(hint, Dock.Top); left.Children.Add(hint);
        taskList = new ListBox(); left.Children.Add(taskList); Put(grid, left, 0);
        var right = new DockPanel();
        var cancel = ActionRow(Button("停止選取的請求", async () =>
        {
            if (string.IsNullOrEmpty(selectedTask)) { ShowNotice("請先選取請求。"); return; }
            var request = requests.FirstOrDefault(x => J.S(x, "id") == selectedTask);
            if (request is null || IsFinished(request)) { ShowNotice("這筆請求已經結束。"); return; }
            await api!.Post("/manager/requests/" + Uri.EscapeDataString(selectedTask) + "/cancel"); ShowNotice("已送出停止要求，請查看取消確認結果。"); await Poll();
        }), Button("清除任務與服務紀錄", ClearRecords)); DockPanel.SetDock(cancel, Dock.Bottom); right.Children.Add(cancel);
        taskDetail = new TextBox { IsReadOnly = true, TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, BorderThickness = new Thickness(0), Background = Paint("#1B2940"), Padding = new Thickness(20), FontSize = 14 };
        right.Children.Add(taskDetail); Put(grid, right, 2);
        taskList.SelectionChanged += (_, _) => { selectedTask = (taskList.SelectedItem as ListBoxItem)?.Tag?.ToString(); UpdateTaskDetail(); };
        root.Children.Add(grid);
        logsText = new TextBox { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.NoWrap, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Height = 175, FontFamily = new FontFamily("Consolas"), FontSize = 11 };
        var logs = new StackPanel(); logs.Children.Add(Text("紀錄只保留狀態與錯誤。詳細內容記錄可在系統頁開啟。", 11, true)); logs.Children.Add(logsText);
        logs.Children.Add(ActionRow(Button("複製紀錄", () => { Clipboard.SetText(logsText?.Text ?? ""); ShowNotice("紀錄已複製。"); return Task.CompletedTask; }), Button("開啟紀錄資料夾", () => { OpenFolder(App.DataDir); return Task.CompletedTask; })));
        var expander = new Expander { Header = "服務執行紀錄", Content = logs, IsExpanded = false }; Grid.SetRow(expander, 1); root.Children.Add(expander);
        return root;
    }

    private static bool IsFinished(JsonNode? request) => new[] { "completed", "cancelled", "error" }.Contains(J.S(request, "phase"));
    private void UpdateTaskDetail()
    {
        if (taskDetail is null) return;
        var request = requests.FirstOrDefault(x => J.S(x, "id") == selectedTask);
        if (request is null) { taskDetail.Text = "尚未收到請求。\n\n從 VS Code 送出任務後，這裡會顯示上下文處理、思考與生成狀態。"; return; }
        var text = new StringBuilder();
        text.AppendLine(PhaseName(J.S(request, "phase"))).AppendLine();
        text.AppendLine("模型  " + J.S(request, "model_name", J.S(request, "model_id")));
        text.AppendLine("模式  " + J.S(request, "profile_name", J.S(request, "profile_id")));
        text.AppendLine("開始  " + LocalTime(J.S(request, "started_at")));
        text.AppendLine("耗時  " + J.Metric(request, "elapsed_seconds", " 秒"));
        text.AppendLine("首個 token 等待  " + J.Metric(request, "first_token_seconds", " 秒"));
        text.AppendLine().AppendLine("輸入與生成");
        text.AppendLine("輸入  " + J.Metric(request, "prompt_tokens", " tokens", "0") + "    快取重用  " + J.Metric(request, "cached_tokens", " tokens", "0"));
        text.AppendLine("輸入處理速度  " + J.Metric(request, "prompt_tps", " tok/s"));
        if (J.D(request, "prompt_progress") is double progress) text.AppendLine("上下文處理進度  " + (progress <= 1 ? progress * 100 : progress).ToString("0") + "%");
        text.AppendLine("已生成  " + J.Metric(request, "generated_tokens", " tokens", "0"));
        text.AppendLine("生成速度  " + J.Metric(request, "generation_tps", " tok/s"));
        text.AppendLine().AppendLine("本次思考策略");
        text.AppendLine("判斷結果  " + DecisionName(J.S(request, "decision", "未提供")));
        text.AppendLine("策略判斷耗時  " + J.Metric(request, "classifier_seconds", " 秒"));
        text.AppendLine("思考程度  " + J.S(request, "effort", "未指定"));
        text.AppendLine("思考預算  " + J.Metric(request, "thinking_budget", " tokens", "0"));
        text.AppendLine("思考用量  " + J.Metric(request, "thinking_tokens", " tokens", "0"));
        text.AppendLine("總生成上限  " + J.Metric(request, "max_tokens", " tokens", "0"));
        if (J.S(request, "phase") == "cancelled") text.AppendLine().AppendLine(J.B(request, "cancel_confirmed") ? "取消結果  已確認停止" : "取消結果  等待引擎確認停止");
        if (!string.IsNullOrEmpty(J.S(request, "error"))) text.AppendLine().AppendLine("錯誤原因  " + J.S(request, "error"));
        text.AppendLine().AppendLine("請求識別  " + J.S(request, "id"));
        taskDetail.Text = text.ToString();
    }

    private static string DecisionName(string value) => value switch { "off" => "直接回答", "on" => "啟用思考", "low" => "簡短思考", "medium" => "均衡思考", "xhigh" => "深入思考", "model" => "跟隨模型預設", _ => value };

    private UIElement BuildSystem()
    {
        var root = new StackPanel();
        var behavior = Section("啟動與背景執行", "設定一次，之後從系統匣隨時查看狀態。");
        var autoStart = Check(behavior, "登入 Windows 後自動啟動", J.B(config, "auto_start"));
        var hidden = Check(behavior, "啟動時直接在背景待命", J.B(config, "start_hidden"));
        var closeTray = Check(behavior, "關閉視窗時留在系統匣繼續運作", J.B(config, "close_to_tray", true));
        var preload = Check(behavior, "啟動時預先載入預設模型", J.B(config, "preload"));
        behavior.Children.Add(Text("未預載時，只有真正的推理請求才會載入模型。系統匣的「完全結束」會停止本管理器啟動的服務。", 12, true));
        root.Children.Add(Card(behavior));
        var modelBehavior = Section("閒置與預設模式");
        var idle = Field(modelBehavior, "沒有任務後，等待幾分鐘卸載（0 代表不自動卸載）", J.S(config, "idle_minutes", "15"));
        var defaultProfile = Choice(modelBehavior, "預設使用模式", J.S(config, "default_profile_id", "coding"), J.A(config, "profiles").Select(x => (J.S(x, "id"), J.S(x, "name"))).ToArray());
        modelBehavior.Children.Add(Text("模型可另設自己的閒置時間。Agent 執行工具時可能暫時沒有推理請求，建議保留足夠等待時間。", 12, true));
        root.Children.Add(Card(modelBehavior));
        var service = Section("本機連線與引擎", "修改連接埠需完全結束並重新開啟管理器；引擎位置在下次載入時生效。");
        var engine = Field(service, "llama.cpp 資料夾", J.S(config, "engine_dir"));
        service.Children.Add(Button("選擇引擎資料夾", () => { var dialog = new OpenFolderDialog { Title = "選擇 llama.cpp 資料夾" }; if (dialog.ShowDialog(this) == true) engine.Text = dialog.FolderName; return Task.CompletedTask; }));
        var port = Field(service, "Agent 連線埠", J.S(config, "api_port", "8080"));
        var enginePort = Field(service, "模型引擎連接埠", J.S(config, "engine_port", "8081"));
        service.Children.Add(ActionRow(Button("檢查連線", CheckConnection), Button("連接 VS Code", ConnectVSCode)));
        root.Children.Add(Card(service));
        var records = Section("紀錄與資料", "平常只記錄狀態、耗時與錯誤。");
        var bodies = Check(records, "除錯時保留完整請求內容（可能包含對話與程式碼）", J.B(config, "log_request_bodies"));
        var days = Field(records, "紀錄保留天數", J.S(config, "log_retention_days", "7"));
        Field(records, "設定與紀錄位置", App.DataDir, true);
        records.Children.Add(ActionRow(Button("開啟資料資料夾", () => { OpenFolder(App.DataDir); return Task.CompletedTask; }), Button("匯出設定", ExportConfig), Button("匯入設定", ImportConfig)));
        root.Children.Add(Card(records));
        async Task Save()
        {
            int publicPort = Number(port, "Agent 連線埠", 1024, 65535), privatePort = Number(enginePort, "模型引擎連接埠", 1024, 65535);
            if (publicPort == privatePort) throw new InvalidOperationException("Agent 與模型引擎必須使用不同的連接埠。");
            bool enabled = autoStart.IsChecked == true;
            bool oldAuto = J.B(config, "auto_start");
            int idleValue = Number(idle, "閒置卸載分鐘", 0, 10080), daysValue = Number(days, "紀錄保留天數", 1, 365);
            string engineValue = Required(engine, "引擎資料夾");
            await SaveConfig(c => { c["auto_start"] = enabled; c["start_hidden"] = hidden.IsChecked == true; c["close_to_tray"] = closeTray.IsChecked == true; c["preload"] = preload.IsChecked == true; c["idle_minutes"] = idleValue; c["default_profile_id"] = Value(defaultProfile); c["engine_dir"] = engineValue; c["api_port"] = publicPort; c["engine_port"] = privatePort; c["log_request_bodies"] = bodies.IsChecked == true; c["log_retention_days"] = daysValue; });
            try { SetAutoStart(enabled); }
            catch (Exception ex)
            {
                await SaveConfig(c => c["auto_start"] = oldAuto);
                throw new InvalidOperationException("其他設定已儲存，但無法變更登入自動啟動：" + ex.Message);
            }
            ShowNotice(publicPort != App.Port ? "設定已儲存。新的連接埠會在完全結束並重新開啟後生效，Agent 的 API 位址也需更新。" : "系統設定已儲存。啟動相關設定會在下次開啟時生效。");
        }
        pendingSave = Save;
        root.Children.Add(ActionRow(Button("儲存系統設定", Save, true), Button("完全結束管理器", RequestExit)));
        return Scroll(root);
    }

    private static string StartupValueName => "LocalModelManager";
    private static void SetAutoStart(bool enabled)
    {
        // Only called after an explicit save/import gesture. Smoke tests never invoke it.
        using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true) ?? throw new InvalidOperationException("無法開啟使用者啟動設定。");
        string executable = Environment.ProcessPath ?? throw new InvalidOperationException("無法取得程式位置。");
        if (enabled) key.SetValue(StartupValueName, "\"" + executable + "\" --data-dir \"" + App.DataDir + "\"");
        else key.DeleteValue(StartupValueName, false);
    }

    private async Task ExportConfig()
    {
        if (!await LeaveEditor()) return;
        var dialog = new SaveFileDialog { Filter = "管理器設定|*.json", FileName = "LocalModelManager-settings.json", DefaultExt = ".json" };
        if (dialog.ShowDialog(this) != true) return;
        var exported = await api!.Get("/manager/export");
        await File.WriteAllTextAsync(dialog.FileName, exported.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        ShowNotice("設定已匯出；不包含模型檔案與存取憑證。");
    }

    private async Task ImportConfig()
    {
        if (!await LeaveEditor()) return;
        var dialog = new OpenFileDialog { Filter = "管理器設定|*.json", CheckFileExists = true };
        if (dialog.ShowDialog(this) != true) return;
        var imported = JsonNode.Parse(await File.ReadAllTextAsync(dialog.FileName))?.AsObject() ?? throw new InvalidOperationException("設定檔格式無法辨識。");
        bool wantsStartup = J.B(imported, "auto_start");
        if (!Confirm("匯入會替換目前設定，原設定會由服務備份。模型檔案不會移動。" + (wantsStartup ? "\n此設定包含登入 Windows 後自動啟動。" : ""))) return;
        await api!.Post("/manager/import", imported); config = await api.Get("/manager/config");
        SetAutoStart(J.B(config, "auto_start")); dirty = false;
        ShowPage("系統"); ShowNotice("設定已匯入。新的載入參數於下次載入生效；連接埠與啟動行為於重新啟動後生效。");
    }

    private static void OpenFolder(string path) => Process.Start(new ProcessStartInfo("explorer.exe") { UseShellExecute = true, ArgumentList = { path } });
}
