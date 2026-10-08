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
            string budgetLabel = J.S(item, "budget_mode", "custom") == "auto" ? "自動預算" : J.S(item, "thinking_budget") + " 思考 tokens";
            label.Children.Add(Text(ThinkingName(J.S(item, "thinking_mode")) + " · " + ReasoningLevelName(J.S(item, "reasoning_level", "balanced")) + " · " + budgetLabel, 11, true));
            list.Items.Add(new ListBoxItem { Content = label, Tag = J.S(item, "id") });
        }
        left.Children.Add(list);
        var editor = new ContentControl();
        Put(grid, Scroll(left), 0); Put(grid, editor, 2);
        ListBoxItem? previous = null;
        bool restoring = false;
        list.SelectionChanged += async (_, _) => await Guard(async () =>
        {
            if (restoring || list.SelectedItem is not ListBoxItem selected || selected.Tag is not string profileId) return;
            if (!await LeaveEditor()) { restoring = true; list.SelectedItem = previous; restoring = false; return; }
            previous = selected;
            var profile = J.A(config, "profiles").OfType<JsonObject>().FirstOrDefault(x => J.S(x, "id") == profileId);
            if (profile is null) throw new InvalidOperationException("此使用模式已被移除，請重新整理。");
            editor.Content = Scroll(BuildProfileEditor(profile)); dirty = false;
        });
        left.Children.Add(ActionRow(Button("＋ 新增模式", async () =>
        {
            if (!await LeaveEditor()) return;
            string id = "profile-" + Guid.NewGuid().ToString("N")[..8];
            await SaveConfig(c => J.A(c, "profiles").Add(new JsonObject { ["id"] = id, ["name"] = "新的使用模式", ["thinking_mode"] = "auto", ["reasoning_level"] = "balanced", ["budget_mode"] = "auto", ["thinking_budget"] = 1536, ["max_tokens"] = 8192, ["agent_sync_mode"] = "preserve", ["agent_name"] = "新的使用模式", ["agent_description"] = "AMIEBL local-model agent.", ["agent_tools"] = new JsonArray(JsonValue.Create("read"), JsonValue.Create("search"), JsonValue.Create("web")), ["agent_instructions"] = "Answer the user's request clearly and use the available tools only as needed." }));
            ShowPage("使用模式");
            SelectProfile(id);
        }, true)));
        left.Children.Add(Text("切換使用模式會調整每次請求，不需要重新載入模型。", 12, true));
        left.Children.Add(Text("VS Code Agent 會由「連接 VS Code」依這裡的設定產生；其他客戶端仍可使用「模型識別::模式識別」指定模式。", 12, true));
        if (list.Items.Count > 0) list.SelectedIndex = 0;
        else editor.Content = Text("先新增一個使用模式。", 18);
        grid.Tag = list;
        return grid;
    }

    private void SelectProfile(string id)
    {
        if (PageContent.Content is Grid grid && grid.Tag is ListBox list)
            list.SelectedItem = list.Items.Cast<ListBoxItem>().FirstOrDefault(x => x.Tag?.ToString() == id);
    }

    private static string ThinkingName(string mode) => mode switch { "auto" => "自動判斷", "on" => "固定思考", "off" => "關閉思考", "model" => "跟隨模型預設", _ => mode };
    private static string ReasoningLevelName(string level) => level switch { "light" => "輕量", "balanced" => "均衡", "deep" => "深入", "extreme" => "極深", _ => level };

    private UIElement BuildProfileEditor(JsonObject profile)
    {
        string id = J.S(profile, "id");
        var panel = Section("使用模式設定", "Profile 描述你想要的行為；AMIEBL 會依每顆模型實際偵測到的 reasoning 能力轉譯成原生 effort、token budget 或單純思考開關。");
        var name = Field(panel, "顯示名稱", J.S(profile, "name"));
        Field(panel, "模式識別（供 Agent 指定）", id, true);
        var mode = Choice(panel, "思考策略", J.S(profile, "thinking_mode", "auto"), ("auto", "自動判斷 · 本機快速規則"), ("on", "固定開啟思考"), ("off", "固定關閉思考"), ("model", "跟隨模型預設"));
        var level = Choice(panel, "AMIEBL 思考強度", J.S(profile, "reasoning_level", "balanced"),
            ("light", "輕量 · 優先速度"), ("balanced", "均衡"), ("deep", "深入"), ("extreme", "極深 · 優先推理"));
        var budgetMode = Choice(panel, "思考 Token 預算", J.S(profile, "budget_mode", "custom"),
            ("auto", "自動 · 依模型能力與思考強度"), ("custom", "自訂上限 · 需引擎支援"));
        var budgetPanel = new StackPanel { Margin = new Thickness(16, 0, 0, 0) };
        var budget = Field(budgetPanel, "自訂思考預算上限（tokens）", J.S(profile, "thinking_budget", "1536"));
        budget.Name = "ProfileBudgetInput";
        panel.Children.Add(budgetPanel);
        budgetPanel.Children.Add(Text("此值會與原生 effort 同時傳送；只有 llama.cpp parser 能辨識思考結束標記時才會限制思考。客戶端明確指定 reasoning 設定時優先，較小的整次生成上限也會縮減此預算。", 11, true));
        var max = Field(panel, "整次生成上限（包含思考與回答）", J.S(profile, "max_tokens", "8192"));
        var summary = Text("", 12, true);
        panel.Children.Add(summary);
        void RefreshSummary()
        {
            bool controls = Value(mode) is "auto" or "on";
            level.IsEnabled = controls; budgetMode.IsEnabled = controls;
            budgetPanel.Visibility = controls && Value(budgetMode) == "custom" ? Visibility.Visible : Visibility.Collapsed;
            summary.Text = Value(mode) switch
            {
                "auto" => "AMIEBL 先用本機快速規則判斷是否需要思考；若需要，再把「" + ReasoningLevelName(Value(level)) + "」轉成該模型真正支援的控制方式。",
                "off" => "若模型可切換 reasoning，要求直接回答；若模型屬於固定 reasoning，AMIEBL 不會硬塞不支援的關閉參數。",
                "model" => "完全沿用模型 Chat Template 的預設 reasoning 行為，不套用 AMIEBL 思考強度。",
                _ => "固定要求思考；AMIEBL 會優先使用模型原生 reasoning effort，沒有原生 effort 時再以 token budget 或思考開關實現。"
            };
        }
        mode.SelectionChanged += (_, _) => RefreshSummary();
        level.SelectionChanged += (_, _) => RefreshSummary();
        budgetMode.SelectionChanged += (_, _) => RefreshSummary();
        RefreshSummary();

        var agent = Section("VS Code Agent", "第一次沒有 Agent 檔時會由 AMIEBL 建立；之後可選擇保留 VS Code 手動修改，或由 AMIEBL 完整管理。");
        var agentSync = Choice(agent, "同步方式", J.S(profile, "agent_sync_mode", "preserve"),
            ("preserve", "保留 VS Code 手動設定 · 推薦"),
            ("managed", "由 AMIEBL 完整管理 · 每次同步覆寫"));
        var agentSyncHint = Text("", 11, true);
        agent.Children.Add(agentSyncHint);
        void RefreshAgentSyncHint()
        {
            agentSyncHint.Text = Value(agentSync) == "managed"
                ? "完整管理：每次按「連接 VS Code」都會用下方名稱、工具與行為指令重建此 Agent。"
                : "保留手動設定：若 Agent 已存在，AMIEBL 只維護 AMIEBL_PROFILE 標記；tools、prompt 與其他 VS Code 設定都不覆寫。下方欄位僅用於第一次建立 Agent。";
        }
        agentSync.SelectionChanged += (_, _) => RefreshAgentSyncHint();
        RefreshAgentSyncHint();
        var agentName = Field(agent, "Agent 顯示名稱 / 初次建立值", J.S(profile, "agent_name", J.S(profile, "name")));
        var agentDescription = Field(agent, "Agent 說明 / 初次建立值", J.S(profile, "agent_description", "AMIEBL local-model agent."));
        var agentTools = Field(agent, "可用工具 / 初次建立值（以逗號分隔）", string.Join(", ", J.A(profile, "agent_tools").Select(x => x?.ToString()).Where(x => !string.IsNullOrWhiteSpace(x))));
        var agentInstructions = MultiLineField(agent, "Agent 行為指令 / 初次建立值", J.S(profile, "agent_instructions", "Answer the user's request clearly and use the available tools only as needed."), 150);
        agent.Children.Add(Text("工具越多，VS Code 需要送進模型的工具描述通常也越多，可能增加輸入 token 與首輪處理時間。思考預算與生成上限仍由上方 AMIEBL 使用模式控制。", 11, true));
        panel.Children.Add(Card(agent));
        async Task Save()
        {
            var changed = Clone(profile);
            changed["name"] = Required(name, "模式名稱"); changed["thinking_mode"] = Value(mode); changed["reasoning_level"] = Value(level); changed["budget_mode"] = Value(budgetMode);
            int budgetValue = Number(budget, "思考預算", 0, 1_000_000), maxValue = Number(max, "整次生成上限", 256, 1_000_000);
            int answerReserve = Math.Min(256, Math.Max(1, maxValue / 4));
            if (Value(mode) is "auto" or "on" && Value(budgetMode) == "custom" && budgetValue > maxValue - answerReserve)
                throw new InvalidOperationException($"自訂思考預算過高；整次生成上限至少需保留 {answerReserve} tokens 給回答與工具呼叫。");
            changed["thinking_budget"] = budgetValue; changed["max_tokens"] = maxValue;
            changed["effort"] = Value(level) switch { "light" => "low", "balanced" => "medium", "deep" => "high", "extreme" => "xhigh", _ => "medium" }; // 舊版相容欄位
            changed["agent_sync_mode"] = Value(agentSync);
            changed["agent_name"] = Required(agentName, "Agent 顯示名稱");
            changed["agent_description"] = agentDescription.Text.Trim();
            changed["agent_tools"] = new JsonArray(agentTools.Text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).Where(x => x.Length > 0).Distinct(StringComparer.OrdinalIgnoreCase).Select(x => (JsonNode?)JsonValue.Create(x)).ToArray());
            changed["agent_instructions"] = Required(agentInstructions, "Agent 行為指令");
            await SaveConfig(c =>
            {
                var profiles = J.A(c, "profiles");
                var target = profiles.FirstOrDefault(x => J.S(x, "id") == id) as JsonObject;
                if (target is null) throw new InvalidOperationException("此模式已被移除，請重新開啟使用模式頁。");
                CopyFields(target, changed, "name", "thinking_mode", "reasoning_level", "budget_mode", "thinking_budget", "max_tokens", "effort", "agent_sync_mode", "agent_name", "agent_description", "agent_tools", "agent_instructions");
            });
            ShowNotice(Value(agentSync) == "managed" ? "使用模式已儲存。下次「連接 VS Code」會依 GUI 覆寫此 Agent。" : "使用模式已儲存。下次「連接 VS Code」只會維護模式標記，既有 VS Code Agent 的手動工具與指令會保留。");
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
        var note = Section("和 Agent 的配合", "VS Code 會用 Agent 內的 AMIEBL_PROFILE 標記選擇此模式；其他客戶端仍可透過 X-LLM-Profile 或「模型::模式」指定。客戶端明確指定的思考控制與較低輸出上限仍會優先保留。");
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
        text.AppendLine("AMIEBL 強度  " + ReasoningLevelName(J.S(request, "reasoning_level", "未指定")));
        text.AppendLine("模型原生 Effort  " + J.S(request, "effort", "未使用"));
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
        var service = Section("本機連線與引擎", "Agent 連線埠需完全結束並重新開啟管理器；llama.cpp 引擎位置與模型引擎連接埠會在下一次模型重新載入時生效。");
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
            string oldEngine = J.S(config, "engine_dir");
            int oldEnginePort = J.I(config, "engine_port", 8081);
            int idleValue = Number(idle, "閒置卸載分鐘", 0, 10080), daysValue = Number(days, "紀錄保留天數", 1, 365);
            string engineValue = Required(engine, "引擎資料夾");
            await SaveConfig(c => { c["auto_start"] = enabled; c["start_hidden"] = hidden.IsChecked == true; c["close_to_tray"] = closeTray.IsChecked == true; c["preload"] = preload.IsChecked == true; c["idle_minutes"] = idleValue; c["default_profile_id"] = Value(defaultProfile); c["engine_dir"] = engineValue; c["api_port"] = publicPort; c["engine_port"] = privatePort; c["log_request_bodies"] = bodies.IsChecked == true; c["log_retention_days"] = daysValue; });
            try { SetAutoStart(enabled); }
            catch (Exception ex)
            {
                await SaveConfig(c => c["auto_start"] = oldAuto);
                throw new InvalidOperationException("其他設定已儲存，但無法變更登入自動啟動：" + ex.Message);
            }
            ShowNotice(publicPort != App.Port
                ? "設定已儲存。新的 Agent 連線埠會在完全結束並重新開啟後生效，Agent 的 API 位址也需更新。"
                : (oldEnginePort != privatePort || !string.Equals(oldEngine, engineValue, StringComparison.OrdinalIgnoreCase))
                    ? "系統設定已儲存。llama.cpp 引擎位置／模型引擎連接埠會在下一個推理請求前自動重新載入模型，或可手動重新載入。"
                    : "系統設定已儲存。閒置、預設模式、紀錄與關閉行為等即時項目已套用；背景啟動與預載設定於下次開啟時生效。");
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
        bool previousStartup = J.B(config, "auto_start");
        if (!Confirm("匯入會替換目前設定，原設定會由服務備份。模型檔案不會移動。" + (wantsStartup ? "\n此設定包含登入 Windows 後自動啟動。" : ""))) return;
        await api!.Post("/manager/import", imported); config = await api.Get("/manager/config");
        try { SetAutoStart(J.B(config, "auto_start")); }
        catch (Exception ex)
        {
            await SaveConfig(c => c["auto_start"] = previousStartup);
            try { SetAutoStart(previousStartup); } catch { }
            throw new InvalidOperationException("設定內容已匯入，但登入自動啟動無法套用，因此已還原該項：" + ex.Message);
        }
        dirty = false; await Poll();
        ShowPage("系統"); ShowNotice(J.B(status, "pending_restart")
            ? "設定已匯入。Agent 連線埠需完全結束並重新開啟後生效；模型載入參數會在下次推理前重新載入。"
            : "設定已匯入。即時項目已套用；若有模型載入參數變更，下一次推理會先重新載入。");
    }

    private static void OpenFolder(string path) => Process.Start(new ProcessStartInfo("explorer.exe") { UseShellExecute = true, ArgumentList = { path } });
}
