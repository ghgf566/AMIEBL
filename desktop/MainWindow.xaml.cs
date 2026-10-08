using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;
using Forms = System.Windows.Forms;
using Brush = System.Windows.Media.Brush;
using Color = System.Windows.Media.Color;
using FontFamily = System.Windows.Media.FontFamily;
using Orientation = System.Windows.Controls.Orientation;

namespace LocalModelManager;

public partial class MainWindow : Window
{
    private ManagerClient? api;
    private JsonObject config = new();
    private JsonObject status = new();
    private JsonArray requests = new();
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(1.5) };
    private readonly Forms.NotifyIcon tray;
    private bool polling, exiting, dirty;
    private string currentPage = "總覽";
    private readonly Dictionary<string, Button> navButtons = new();
    private readonly Dictionary<string, TextBlock> live = new();
    private ListBox? taskList;
    private TextBox? taskDetail, logsText;
    private string? selectedTask;
    private Func<Task>? pendingSave;
    private int failedPolls;
    private static readonly string[] Pages = ["總覽", "模型庫", "使用模式", "任務與紀錄", "系統"];
    private static readonly string[] Glyphs = ["◈", "▦", "≋", "↗", "⚙"];
    private static Brush Paint(string hex) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));

    public MainWindow()
    {
        InitializeComponent();
        for (int i = 0; i < Pages.Length; i++)
        {
            string page = Pages[i];
            var button = new Button { Content = Glyphs[i] + "   " + page, HorizontalContentAlignment = HorizontalAlignment.Left, Background = Brushes.Transparent, BorderThickness = new Thickness(0), Padding = new Thickness(14, 13, 14, 13), Margin = new Thickness(0, 0, 0, 7) };
            button.Click += async (_, _) => await Guard(async () => { if (await LeaveEditor()) ShowPage(page); });
            Navigation.Children.Add(button); navButtons[page] = button;
        }
        tray = new Forms.NotifyIcon { Icon = MakeTrayIcon(), Text = "Local Model Manager · 正在啟動", Visible = true };
        tray.DoubleClick += (_, _) => Dispatcher.Invoke(Reveal);
        BuildTray();
        Closing += (_, args) =>
        {
            if (exiting) return;
            args.Cancel = true;
            if (J.B(config, "close_to_tray", true)) { Hide(); return; }
            _ = RequestExit();
        };
        timer.Tick += async (_, _) => await Poll();
        PageContent.Content = Text("正在準備背景服務，請稍候…", 18);
    }

    public async Task Connect(ManagerClient client)
    {
        api = client;
        config = await api.Get("/manager/config");
        await Poll(); ShowPage("總覽"); timer.Start();
        if (J.B(config, "pending_config")) ShowNotice("部分設定會在下次載入模型或重新啟動管理器後生效。");
    }
    public void Reveal() { Show(); WindowState = WindowState.Normal; ShowInTaskbar = true; Activate(); }
    public void PrepareExit() { exiting = true; timer.Stop(); tray.Visible = false; tray.ContextMenuStrip?.Dispose(); tray.Dispose(); ownedIcon?.Dispose(); }
    public void ShowError(string message) { MessageBoxArea.Background = Paint("#482D36"); MessageBoxArea.BorderBrush = Paint("#84505D"); MessageText.Text = message; MessageBoxArea.Visibility = Visibility.Visible; }
    private void ShowNotice(string message) { MessageBoxArea.Background = Paint("#203B41"); MessageBoxArea.BorderBrush = Paint("#366267"); MessageText.Text = message; MessageBoxArea.Visibility = Visibility.Visible; }
    private void DismissMessage(object sender, RoutedEventArgs e) => MessageBoxArea.Visibility = Visibility.Collapsed;
    public void ShowStartupFailure(string message)
    {
        ShowError(message); PageSubtitle.Text = "背景服務尚未連線。你的模型與原有設定仍保留。";
        var panel = new StackPanel(); panel.Children.Add(Text("無法啟動背景服務", 22)); panel.Children.Add(Text("請檢查服務連接埠與執行紀錄，修正後重新開啟管理器。", 14, true));
        panel.Children.Add(ActionRow(Button("開啟資料資料夾", () => { OpenFolder(App.DataDir); return Task.CompletedTask; }), Button("完全結束", RequestExit)));
        PageContent.Content = Card(panel);
    }
    private async Task Guard(Func<Task> action) { try { await action(); } catch (Exception ex) { ShowError(ex.Message); } }
    private async Task<bool> LeaveEditor()
    {
        if (!dirty) return true;
        var result = System.Windows.MessageBox.Show(this, "目前有尚未儲存的修改。要先儲存嗎？", "儲存修改", MessageBoxButton.YesNoCancel, MessageBoxImage.Question);
        if (result == MessageBoxResult.Cancel) return false;
        if (result == MessageBoxResult.Yes && pendingSave is not null) await pendingSave();
        dirty = false; return true;
    }
    private void ShowPage(string page)
    {
        currentPage = page; dirty = false; pendingSave = null; live.Clear(); taskList = null; logsText = null; taskDetail = null;
        PageTitle.Text = page;
        PageEyebrow.Text = page switch { "總覽" => "WORKSPACE", "模型庫" => "MODELS", "使用模式" => "PROFILES", "任務與紀錄" => "ACTIVITY", _ => "PREFERENCES" };
        PageSubtitle.Text = page switch { "總覽" => "隨時待命，需要時才載入。", "模型庫" => "模型放在哪裡、如何載入，都由你決定。", "使用模式" => "同一個模型，依任務選擇不同思考策略。", "任務與紀錄" => "看清楚每次請求正在做什麼。", _ => "讓工作台配合你的日常使用方式。" };
        foreach (var entry in navButtons) entry.Value.Background = Paint(entry.Key == page ? "#244C50" : "#0C1322");
        if (api is null) { PageContent.Content = Text("背景服務尚未就緒。", 18); return; }
        PageContent.Content = page switch { "總覽" => BuildOverview(), "模型庫" => BuildModels(), "使用模式" => BuildProfiles(), "任務與紀錄" => BuildTasks(), _ => BuildSystem() };
        UpdateLive();
    }
    private async Task Poll()
    {
        if (api is null || polling || exiting) return;
        polling = true;
        try
        {
            var fetched = await Task.WhenAll(api.Get("/manager/status"), api.Get("/manager/requests"));
            status = fetched[0]; requests = J.A(fetched[1], "requests"); failedPolls = 0;
            SidebarStatus.Text = "●  服務已連線";
            if (logsText is not null) { var result = await api.Get("/manager/logs"); logsText.Text = string.Join(Environment.NewLine, J.A(result, "lines").Select(x => x?.ToString())); }
            UpdateLive();
        }
        catch (Exception ex)
        {
            failedPolls++; SidebarStatus.Text = "●  服務暫時離線";
            if (failedPolls == 1) ShowError("暫時無法取得服務狀態：" + ex.Message);
            tray.Text = "Local Model Manager · 服務離線";
        }
        finally { polling = false; }
    }
    private static string StateName(string state) => state switch { "unloaded" => "待命，尚未載入模型", "loading" => "正在載入模型", "ready" => "模型已就緒", "unloading" => "正在釋放模型記憶體", "error" => "需要處理", _ => "正在連接" };
    private static string PhaseName(string phase) => phase switch { "queued" => "等待執行", "loading" => "等待模型載入", "classifying" => "判斷思考策略", "prompt" => "讀取上下文", "thinking" => "思考中", "generating" => "生成中", "completed" => "已完成", "cancelled" => "已取消", "error" => "失敗", _ => phase };
    private void UpdateLive()
    {
        string state = StateName(J.S(status, "state"));
        SetLive("state", state); SetLive("model", J.S(status, "model_name", "收到任務後自動載入預設模型"));
        SetLive("active", J.S(status, "active_count", "0")); SetLive("queued", J.S(status, "queued_count", "0"));
        SetLive("ram", J.Metric(status["resources"], "ram_used_gb", " GB") + " / " + J.Metric(status["resources"], "ram_total_gb", " GB"));
        SetLive("gpu", J.Metric(status["resources"], "gpu_used_mib", " MiB", "0") + " / " + J.Metric(status["resources"], "gpu_total_mib", " MiB", "0"));
        SetLive("pid", "模型進程 PID  " + J.S(status, "pid", "—") + "   ·   " + (J.B(status, "accepting", true) ? "接受新請求" : "已暫停接收"));
        SetLive("idle", J.D(status, "unload_in_seconds") is double time ? $"若持續閒置，約 {Math.Ceiling(time / 60)} 分鐘後卸載" : "按需載入；閒置行為依模型設定執行");
        SetLive("error", J.S(status, "last_error", ""));
        SetLive("pending", J.B(status, "pending_config") ? "有設定等待下次載入或重新啟動後生效。" : "設定已套用");
        var active = requests.FirstOrDefault(x => !IsFinished(x) && J.S(x, "phase") != "queued");
        SetLive("phase", active is null ? "目前沒有執行中的任務" : PhaseName(J.S(active, "phase")));
        SetLive("speed", active is null ? "—" : J.Metric(active, "generation_tps", " tok/s"));
        SetLive("tokens", active is null ? "—" : J.Metric(active, "generated_tokens", " tokens", "0"));
        SetLive("slot", active is null ? (J.S(status, "state") == "ready" ? "Slot 0 · 閒置" : "等待引擎就緒") : $"Slot 0 · {J.S(active, "profile_name", J.S(active, "profile_id"))} · {J.Metric(active, "elapsed_seconds", " 秒")}");
        tray.Text = ("Local Model Manager · " + state)[..Math.Min(63, ("Local Model Manager · " + state).Length)];
        UpdateTray();
        Footer.Text = $"{api?.BaseUrl}  ·  " + (J.B(status, "pending_config") ? "部分設定等待重新載入" : "背景服務已連線") + "  ·  僅接受本機連線";
        if (taskList is not null)
        {
            string? selected = (taskList.SelectedItem as ListBoxItem)?.Tag?.ToString() ?? selectedTask;
            taskList.Items.Clear();
            foreach (var request in requests)
            {
                var row = new StackPanel(); row.Children.Add(Text(PhaseName(J.S(request, "phase")) + "  ·  " + J.S(request, "profile_name", J.S(request, "profile_id")), 14));
                row.Children.Add(Text(LocalTime(J.S(request, "started_at")) + "  ·  " + J.Metric(request, "elapsed_seconds", " 秒"), 11, true));
                var item = new ListBoxItem { Content = row, Tag = J.S(request, "id") }; taskList.Items.Add(item);
                if (J.S(request, "id") == selected) taskList.SelectedItem = item;
            }
            if (taskList.SelectedIndex < 0 && taskList.Items.Count > 0) taskList.SelectedIndex = 0;
            UpdateTaskDetail();
        }
    }
    private static string LocalTime(string value) => DateTimeOffset.TryParse(value, out var date) ? date.ToLocalTime().ToString("MM/dd HH:mm:ss") : value;
    private void SetLive(string key, string value) { if (live.TryGetValue(key, out var control)) control.Text = value; }
    private TextBlock Live(string key, string text, double size = 14, bool muted = false) { var result = Text(text, size, muted); live[key] = result; return result; }
    private static TextBlock Text(string text, double size = 14, bool muted = false) => new() { Text = text, FontSize = size, Foreground = Paint(muted ? "#C4CDDB" : "#F4F7FB"), Margin = new Thickness(0, 0, 0, 9), TextWrapping = TextWrapping.Wrap };
    private static Border Card(UIElement content) => new() { Child = content, Background = Paint("#1B2940"), BorderBrush = Paint("#3A4B66"), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12), Padding = new Thickness(22), Margin = new Thickness(0, 0, 0, 17) };
    private static ScrollViewer Scroll(UIElement content) => new() { Content = content, Padding = new Thickness(0, 0, 12, 0) };
    private Button Button(string label, Func<Task> action, bool primary = false)
    {
        var button = new Button { Content = label };
        if (primary) { button.Background = Paint("#5DE4BB"); button.Foreground = Paint("#0D2826"); button.BorderThickness = new Thickness(0); button.FontWeight = FontWeights.SemiBold; }
        button.Click += async (_, _) => { button.IsEnabled = false; try { await Guard(action); } finally { button.IsEnabled = true; } };
        return button;
    }
    private static WrapPanel ActionRow(params UIElement[] buttons) { var row = new WrapPanel { Margin = new Thickness(0, 12, 0, 0) }; foreach (var button in buttons) row.Children.Add(button); return row; }
    private static StackPanel Section(string title, string? description = null) { var panel = new StackPanel(); panel.Children.Add(Text(title, 18)); if (description is not null) panel.Children.Add(Text(description, 12, true)); return panel; }
    private async Task SaveConfig(Action<JsonObject> edit)
    {
        if (api is null) return;
        JsonObject latest = await api.Get("/manager/config"); edit(latest); await api.Put("/manager/config", latest); config = await api.Get("/manager/config"); dirty = false;
        ShowNotice("設定已儲存。模型載入參數會在下次載入生效。"); await Poll();
    }
    private static JsonObject Clone(JsonNode node) => node.DeepClone().AsObject();
    private string DefaultModel() => J.S(config, "default_model_id");
    private async Task LoadDefault() { if (api is null) return; await api.Post("/manager/load", new JsonObject { ["model_id"] = DefaultModel() }); ShowNotice("已送出載入要求。可在總覽查看進度。"); await Poll(); }
    private async Task Unload() { if (api is null) return; var result = await api.Post("/manager/unload"); ShowNotice(J.B(result, "deferred") ? "目前仍有任務，完成後會卸載模型。" : "已要求卸載模型。"); await Poll(); }
    private async Task ToggleAccepting() { if (api is null) return; await api.Post("/manager/accepting", new JsonObject { ["accepting"] = !J.B(status, "accepting", true) }); await Poll(); }
    private async Task ToggleKeep()
    {
        if (api is null) return;
        string id = J.S(status, "model_id", DefaultModel()); var model = J.A(config, "models").FirstOrDefault(x => J.S(x, "id") == id);
        bool keep = !J.B(model, "keep_loaded"); await api.Post("/manager/keep-loaded", new JsonObject { ["model_id"] = id, ["keep_loaded"] = keep });
        config = await api.Get("/manager/config"); ShowNotice(keep ? "此模型已設為保持載入。" : "此模型恢復閒置卸載。"); await Poll();
    }
    private async Task CancelActive()
    {
        var active = requests.FirstOrDefault(x => !IsFinished(x) && J.S(x, "phase") != "queued") ?? requests.FirstOrDefault(x => !IsFinished(x));
        if (active is null || api is null) { ShowNotice("目前沒有需要停止的任務。"); return; }
        await api.Post("/manager/requests/" + Uri.EscapeDataString(J.S(active, "id")) + "/cancel"); ShowNotice("停止要求已送出，正在確認任務已結束。"); await Poll();
    }
    private async Task ClearRecords()
    {
        if (api is null) return;
        if (!Confirm("會清除已完成的任務、服務執行紀錄，以及已啟用的請求除錯檔案。正在執行的任務不會被停止。")) return;
        var result = await api.Post("/manager/records/clear");
        selectedTask = null;
        ShowNotice($"已清除 {J.I(result, "requests")} 筆任務紀錄、{J.I(result, "logs")} 行服務紀錄與 {J.I(result, "request_bodies")} 個請求檔案。執行中的任務仍會保留。");
        await Poll();
    }
    private UIElement BuildOverview()
    {
        var root = new StackPanel();
        var hero = Section("本機服務"); hero.Children.Add(Live("state", "", 27)); hero.Children.Add(Live("model", "", 16, true));
        hero.Children.Add(Live("pid", "", 12, true)); hero.Children.Add(Live("idle", "", 12, true)); hero.Children.Add(Live("error", "", 13));
        hero.Children.Add(ActionRow(Button("載入預設模型", LoadDefault, true), Button("卸載模型", Unload), Button("切換保持載入", ToggleKeep), Button("暫停／恢復接收", ToggleAccepting)));
        root.Children.Add(Card(hero));
        var metrics = new System.Windows.Controls.Primitives.UniformGrid { Columns = 4 };
        foreach (var spec in new[] { ("active", "執行中"), ("queued", "等待中"), ("ram", "系統記憶體"), ("gpu", "顯示記憶體") })
        {
            var item = new StackPanel { Margin = new Thickness(0, 0, 12, 0) }; item.Children.Add(Text(spec.Item2, 12, true)); item.Children.Add(Live(spec.Item1, "—", spec.Item1 is "ram" or "gpu" ? 15 : 30)); metrics.Children.Add(item);
        }
        root.Children.Add(Card(metrics));
        var slot = Section("目前任務"); slot.Children.Add(Live("slot", "", 12, true)); slot.Children.Add(Live("phase", "", 21));
        var detail = new WrapPanel(); detail.Children.Add(Live("speed", "", 18)); var gap = Text("   ·   "); detail.Children.Add(gap); detail.Children.Add(Live("tokens", "", 18)); slot.Children.Add(detail);
        slot.Children.Add(ActionRow(Button("停止目前請求", CancelActive), Button("查看任務詳情", () => { ShowPage("任務與紀錄"); return Task.CompletedTask; })));
        root.Children.Add(Card(slot));
        var connect = Section("連接你的 Agent", "API 已在本機待命。查詢模型清單不會載入模型。");
        var address = new TextBox { Text = api!.BaseUrl + "/v1", IsReadOnly = true, FontFamily = new FontFamily("Cascadia Mono, Consolas") }; connect.Children.Add(address);
        connect.Children.Add(ActionRow(Button("複製 API 位址", () => { Clipboard.SetText(address.Text); ShowNotice("API 位址已複製。"); return Task.CompletedTask; }), Button("檢查連線", CheckConnection), Button("連接 VS Code", ConnectVSCode)));
        connect.Children.Add(Live("pending", "", 12, true)); root.Children.Add(Card(connect)); return Scroll(root);
    }
    private async Task CheckConnection()
    {
        var result = await api!.Get("/manager/connection");
        ShowNotice($"API：{J.S(result, "api_url", api.BaseUrl)}\nllama-server：{(J.B(result, "engine_exists") ? "已找到" : "未找到，請檢查引擎位置")}　Python：{(J.B(result, "python_ok") ? "可用" : "需要檢查")}\n{J.S(result, "port_status")}\n可用模型識別：{J.A(result, "models").Count} 個");
    }
    private async Task ConnectVSCode()
    {
        var preview = await api!.Get("/manager/vscode/preview");
        string description = J.S(preview, "summary", "將在 VS Code 建立本機模型與 Quick Chat、Coding、Deep Coding 模式。現有設定會先備份，完成後需重新載入 VS Code 視窗。");
        if (System.Windows.MessageBox.Show(this, description + "\n\n確認更新 VS Code 設定？", "連接 VS Code", MessageBoxButton.OKCancel, MessageBoxImage.Information) != MessageBoxResult.OK) return;
        var result = await api.Post("/manager/vscode/apply");
        ShowNotice("已更新 VS Code 設定，請在 VS Code 重新載入視窗。\n" + J.S(result, "message"));
    }
    private static Grid Split(double width = 265)
    {
        var grid = new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(width) }); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(20) }); grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }); return grid;
    }
    private static void Put(Grid grid, UIElement child, int col) { Grid.SetColumn(child, col); grid.Children.Add(child); }
    private TextBox Field(StackPanel panel, string label, string value, bool readOnly = false)
    {
        panel.Children.Add(Text(label, 12, true)); var input = new TextBox { Text = value, IsReadOnly = readOnly }; input.TextChanged += (_, _) => { if (!readOnly) dirty = true; }; panel.Children.Add(input); return input;
    }
    private TextBox FieldWithHint(StackPanel panel, string label, string value, string hint, string placeholder = "auto（預設）", bool readOnly = false)
    {
        var header = new DockPanel { Margin = new Thickness(0, 0, 0, 4) };
        var labelText = Text(label, 12, true);
        DockPanel.SetDock(labelText, Dock.Left);
        header.Children.Add(labelText);
        var hintIcon = new Border
        {
            Width = 16,
            Height = 16,
            CornerRadius = new CornerRadius(8),
            Background = Paint("#253147"),
            BorderBrush = Paint("#3A4B66"),
            BorderThickness = new Thickness(1),
            Margin = new Thickness(8, 0, 0, 0),
            Cursor = System.Windows.Input.Cursors.Help,
            VerticalAlignment = VerticalAlignment.Center,
            ToolTip = new ToolTip
            {
                Content = new TextBlock
                {
                    Text = hint,
                    MaxWidth = 360,
                    TextWrapping = TextWrapping.Wrap,
                    FontSize = 12,
                    Foreground = Paint("#F1F5F9")
                },
                Background = Paint("#1E293B"),
                BorderBrush = Paint("#475569"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(10, 8, 10, 8)
            },
            Child = new TextBlock
            {
                Text = "?",
                FontSize = 11,
                FontWeight = FontWeights.Bold,
                Foreground = Paint("#94A3B8"),
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center
            }
        };
        DockPanel.SetDock(hintIcon, Dock.Left);
        header.Children.Add(hintIcon);
        panel.Children.Add(header);
        var container = new Grid { Margin = new Thickness(0, 0, 0, 13) };
        var input = new TextBox { Text = value, IsReadOnly = readOnly, Margin = new Thickness(0) };
        var placeholderBlock = new TextBlock
        {
            Text = placeholder,
            Foreground = Paint("#64748B"),
            FontSize = 13,
            Margin = new Thickness(11, 0, 0, 0),
            VerticalAlignment = VerticalAlignment.Center,
            IsHitTestVisible = false,
            Visibility = string.IsNullOrEmpty(value) ? Visibility.Visible : Visibility.Collapsed
        };
        input.TextChanged += (_, _) =>
        {
            placeholderBlock.Visibility = string.IsNullOrEmpty(input.Text) ? Visibility.Visible : Visibility.Collapsed;
            if (!readOnly) dirty = true;
        };
        container.Children.Add(input);
        container.Children.Add(placeholderBlock);
        panel.Children.Add(container);
        return input;
    }
    private TextBox MultiLineField(StackPanel panel, string label, string value, double minHeight = 120)
    {
        panel.Children.Add(Text(label, 12, true));
        var input = new TextBox
        {
            Text = value,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            MinHeight = minHeight
        };
        input.TextChanged += (_, _) => dirty = true;
        panel.Children.Add(input);
        return input;
    }
    private CheckBox Check(StackPanel panel, string label, bool value)
    {
        var input = new CheckBox { Content = label, IsChecked = value }; input.Checked += (_, _) => dirty = true; input.Unchecked += (_, _) => dirty = true; panel.Children.Add(input); return input;
    }
    private ComboBox Choice(StackPanel panel, string label, string value, params (string id, string name)[] options)
    {
        panel.Children.Add(Text(label, 12, true)); var input = new ComboBox { Foreground = Paint("#172036"), Background = Paint("#E2E9F2") }; foreach (var option in options) input.Items.Add(new ComboBoxItem { Content = new TextBlock { Text = option.name, Foreground = Paint("#172036") }, Tag = option.id, Foreground = Paint("#172036"), Background = Paint("#E2E9F2") });
        input.SelectedItem = input.Items.Cast<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == value) ?? input.Items.Cast<ComboBoxItem>().FirstOrDefault(); input.SelectionChanged += (_, _) => dirty = true; panel.Children.Add(input); return input;
    }
    private static string Value(ComboBox input) => (input.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "";
    private static int Number(TextBox field, string name, int min = 0, int max = int.MaxValue)
    {
        if (!int.TryParse(field.Text.Trim(), out int value) || value < min || value > max) throw new InvalidOperationException($"「{name}」請填入 {min} 到 {max} 之間的整數。"); return value;
    }
    private static double Decimal(TextBox field, string name, double min = 0, double max = 100)
    {
        if (!double.TryParse(field.Text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double value) || !double.IsFinite(value) || value < min || value > max) throw new InvalidOperationException($"「{name}」請填入 {min} 到 {max} 之間的數字。"); return value;
    }
    private static int? OptionalNumber(TextBox field, string name, int min = 0, int max = int.MaxValue)
    {
        string text = field.Text.Trim();
        if (string.IsNullOrWhiteSpace(text) || text.Equals("auto", StringComparison.OrdinalIgnoreCase)) return null;
        if (!int.TryParse(text, out int value) || value < min || value > max) throw new InvalidOperationException($"「{name}」請填入 {min} 到 {max} 之間的整數，或保留空白/auto。");
        return value;
    }
    private static double? OptionalDecimal(TextBox field, string name, double min = 0, double max = 100)
    {
        string text = field.Text.Trim();
        if (string.IsNullOrWhiteSpace(text) || text.Equals("auto", StringComparison.OrdinalIgnoreCase)) return null;
        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double value) || !double.IsFinite(value) || value < min || value > max) throw new InvalidOperationException($"「{name}」請填入 {min} 到 {max} 之間的數字，或保留空白/auto。");
        return value;
    }
    private static string Required(TextBox input, string name) => string.IsNullOrWhiteSpace(input.Text) ? throw new InvalidOperationException($"請填入{name}。") : input.Text.Trim();

    private UIElement BuildModels()
    {
        var grid = Split(); var left = new StackPanel(); var list = new ListBox { MinHeight = 140, MaxHeight = 260 };
        var search = new TextBox { ToolTip = "搜尋模型名称或檔案位置" }; left.Children.Add(Text("搜尋模型", 12, true)); left.Children.Add(search);
        foreach (var model in J.A(config, "models"))
        {
            var item = new StackPanel(); item.Children.Add(Text(J.S(model, "name"), 14)); item.Children.Add(Text(J.S(model, "id") == DefaultModel() ? "預設模型" : Path.GetFileName(J.S(model, "path")), 11, true)); list.Items.Add(new ListBoxItem { Content = item, Tag = model });
        }
        left.Children.Add(list); var editor = new ContentControl(); Put(grid, Scroll(left), 0); Put(grid, editor, 2);
        ListBoxItem? previous = null; bool restoring = false;
        search.TextChanged += (_, _) => { foreach (ListBoxItem item in list.Items) { var model = item.Tag as JsonObject; string query = search.Text.Trim(); item.Visibility = (J.S(model, "name") + " " + J.S(model, "path")).Contains(query, StringComparison.OrdinalIgnoreCase) ? Visibility.Visible : Visibility.Collapsed; } };
        list.SelectionChanged += async (_, _) => await Guard(async () =>
        {
            if (restoring || list.SelectedItem is not ListBoxItem selected || selected.Tag is not JsonObject model) return;
            if (!await LeaveEditor()) { restoring = true; list.SelectedItem = previous; restoring = false; return; }
            previous = selected;
            editor.Content = Scroll(BuildModelEditor(model)); dirty = false;
        });
        left.Children.Add(ActionRow(Button("掃描資料夾", ScanModels, true), Button("加入檔案", AddModelFile)));
        left.Children.Add(Text("模型位置", 17));
        foreach (var dir in J.A(config, "model_dirs"))
        {
            string path = dir?.ToString() ?? ""; var row = new StackPanel(); row.Children.Add(Text(path, 12, true)); row.Children.Add(Button("移除此位置", async () => { if (!await LeaveEditor()) return; await SaveConfig(c => { var array = J.A(c, "model_dirs"); var found = array.FirstOrDefault(x => x?.ToString() == path); if (found is not null) array.Remove(found); }); ShowPage("模型庫"); })); left.Children.Add(row);
        }
        left.Children.Add(Button("＋ 新增模型資料夾", async () =>
        {
            if (!await LeaveEditor()) return; var picker = new OpenFolderDialog { Title = "選擇模型資料夾" }; if (picker.ShowDialog(this) != true) return;
            await SaveConfig(c => { var dirs = J.A(c, "model_dirs"); if (!dirs.Any(x => string.Equals(x?.ToString(), picker.FolderName, StringComparison.OrdinalIgnoreCase))) dirs.Add(picker.FolderName); c["model_dirs"] = dirs; }); ShowPage("模型庫");
        }));
        left.Children.Add(Text("移除模型或資料夾只會取消登錄，不會刪除磁碟檔案。", 11, true));
        if (list.Items.Count > 0) list.SelectedIndex = 0; else editor.Content = Text("新增模型資料夾並掃描，或直接選擇 GGUF 檔案。", 18);
        return grid;
    }
    private UIElement BuildModelEditor(JsonObject model)
    {
        string id = J.S(model, "id"); var panel = Section("模型設定", "載入參數在下次載入生效；每個模型各自保存。");
        var name = Field(panel, "顯示名稱", J.S(model, "name")); Field(panel, "API 模型識別（建立後固定）", id, true);
        var path = Field(panel, "模型 GGUF 路徑", J.S(model, "path")); panel.Children.Add(Button("選擇模型檔案", () => { PickFile(path, "GGUF 模型|*.gguf"); return Task.CompletedTask; }));
        int nativeContext = J.I(model, "native_context", 0);
        int currentContext = J.I(model, "context", 32768);
        int contextSliderMax = nativeContext > 0 ? Math.Max(2048, nativeContext) : Math.Max(131072, currentContext);
        var contextPanel = Section("上下文容量", nativeContext > 0 ? $"GGUF 宣告的原生上限：{nativeContext:N0} tokens" : "尚未偵測到模型原生上限；仍可直接輸入精確 token 數。");
        var context = new TextBox { Text = currentContext.ToString(CultureInfo.InvariantCulture), Margin = new Thickness(0, 0, 0, 6) };
        contextPanel.Children.Add(context);
        double minContextLog = Math.Log(2048, 2);
        double maxContextLog = Math.Log(contextSliderMax, 2);
        var contextSlider = new Slider { Minimum = minContextLog, Maximum = maxContextLog, Value = Math.Clamp(Math.Log(Math.Max(2048, currentContext), 2), minContextLog, maxContextLog), TickFrequency = 1, IsSnapToTickEnabled = true, TickPlacement = System.Windows.Controls.Primitives.TickPlacement.BottomRight, SmallChange = 1, LargeChange = 1, Margin = new Thickness(0, 0, 0, 5) };
        contextPanel.Children.Add(contextSlider);
        var contextTicks = Text("", 11, true);
        var tickNames = new List<string>();
        for (long value = 2048; value <= contextSliderMax && value <= 2097152; value *= 2) tickNames.Add(value >= 1048576 ? $"{value / 1048576.0:0.#}M" : $"{value / 1024}K");
        if (tickNames.Count == 0 || (1L << (int)Math.Floor(maxContextLog)) < contextSliderMax) tickNames.Add(contextSliderMax >= 1048576 ? $"{contextSliderMax / 1048576.0:0.#}M" : $"{contextSliderMax / 1024.0:0.#}K");
        contextTicks.Text = string.Join("   ", tickNames);
        contextPanel.Children.Add(contextTicks);
        bool syncingContext = false;
        contextSlider.ValueChanged += (_, _) =>
        {
            if (syncingContext) return;
            syncingContext = true;
            int value = (int)Math.Clamp(Math.Round(Math.Pow(2, contextSlider.Value)), 2048, contextSliderMax);
            context.Text = value.ToString(CultureInfo.InvariantCulture);
            syncingContext = false;
            dirty = true;
        };
        context.TextChanged += (_, _) =>
        {
            if (syncingContext) return;
            dirty = true;
            if (!int.TryParse(context.Text.Trim(), out int value) || value <= 0) return;
            syncingContext = true;
            contextSlider.Value = Math.Clamp(Math.Log(Math.Max(2048, value), 2), minContextLog, maxContextLog);
            syncingContext = false;
        };
        panel.Children.Add(contextPanel);
        var autoFit = Check(panel, "自動估算可使用的 GPU 記憶體", J.B(model, "auto_fit", true));
        var gpuSettings = new StackPanel();
        var gpu = Field(gpuSettings, "GPU 層數（-1 為全部）", J.S(model, "gpu_layers", "-1"));
        panel.Children.Add(gpuSettings);
        var customReserve = Check(panel, "自訂預留顯示記憶體", J.B(model, "fit_target_enabled", true));
        var reserveSettings = new StackPanel { Margin = new Thickness(16, 0, 0, 0) };
        var reserve = Field(reserveSettings, "預留顯示記憶體（MiB）", J.S(model, "fit_target_mib", "2048"));
        reserveSettings.Children.Add(Text("未自訂時不傳入 --fit-target，直接使用 llama.cpp / llama-fit-params 的預設值（目前為每張 GPU 1024 MiB）。", 11, true));
        panel.Children.Add(reserveSettings);
        var fitHint = Text("", 12, true);
        panel.Children.Add(fitHint);
        void RefreshGpuSettings()
        {
            bool automatic = autoFit.IsChecked == true;
            bool custom = customReserve.IsChecked == true;
            gpuSettings.Visibility = automatic ? Visibility.Collapsed : Visibility.Visible;
            customReserve.Visibility = automatic ? Visibility.Visible : Visibility.Collapsed;
            reserveSettings.Visibility = automatic && custom ? Visibility.Visible : Visibility.Collapsed;
            fitHint.Text = !automatic
                ? "已關閉自動計算，將直接使用手動指定的 GPU 層數。"
                : custom
                    ? "已啟用自動計算，載入時會依上下文與自訂預留記憶體呼叫 llama-fit-params.exe。"
                    : "已啟用自動計算；預留記憶體交由 llama-fit-params 使用原生預設值（目前 1024 MiB）。";
        }
        autoFit.Checked += (_, _) => RefreshGpuSettings();
        autoFit.Unchecked += (_, _) => RefreshGpuSettings();
        customReserve.Checked += (_, _) => RefreshGpuSettings();
        customReserve.Unchecked += (_, _) => RefreshGpuSettings();
        RefreshGpuSettings();
        var cache = Choice(panel, "KV cache 精度", J.S(model, "cache_type", "q4_0"), ("f16", "f16 · 較高精度"), ("q8_0", "q8_0 · 均衡"), ("q4_0", "q4_0 · 較省記憶體"));
        int logicalThreads = Math.Max(1, Environment.ProcessorCount);
        int savedThreads = Math.Clamp(J.I(model, "cpu_threads", 0), 0, logicalThreads);
        var cpuPanel = Section("CPU 執行緒上限", "0 代表 Auto；指定數值時，生成與批次／上下文處理都會套用相同的執行緒上限。");
        var cpuValue = Text("", 12, true);
        var cpuSlider = new Slider { Minimum = 0, Maximum = logicalThreads, Value = savedThreads, TickFrequency = 1, IsSnapToTickEnabled = true, TickPlacement = System.Windows.Controls.Primitives.TickPlacement.BottomRight, SmallChange = 1, LargeChange = Math.Max(1, logicalThreads / 4), Margin = new Thickness(0, 0, 0, 4) };
        void RefreshCpuLabel() => cpuValue.Text = cpuSlider.Value < 0.5 ? $"Auto（llama.cpp 自動決定，系統可用 {logicalThreads} 個邏輯執行緒）" : $"最多 {(int)Math.Round(cpuSlider.Value)} 個 CPU 執行緒";
        cpuSlider.ValueChanged += (_, _) => { RefreshCpuLabel(); dirty = true; };
        RefreshCpuLabel();
        cpuPanel.Children.Add(cpuSlider); cpuPanel.Children.Add(cpuValue); panel.Children.Add(cpuPanel);
        string mtpCapability = J.S(model, "mtp_capability", "unknown");
        bool nativeMtpAvailable = mtpCapability == "available";
        var mtp = Check(panel, "啟用 MTP", J.B(model, "mtp"));
        var mtpStatus = Text(mtpCapability switch
        {
            "available" => $"此 GGUF 內建 MTP / NextN 權重（{J.S(model, "mtp_layers", "1")} 層），可直接使用；也可以改用外部 MTP Draft。",
            "incomplete" => "此 GGUF 的內建 MTP / NextN metadata 不完整，因此不能使用內建模式；仍可指定外部 MTP Draft。",
            "unavailable" => "此 GGUF 沒有內建 MTP / NextN head；仍可透過外部 MTP Draft 啟用 MTP。",
            _ => "尚未確認此 GGUF 是否有內建 MTP / NextN head；內建模式暫時停用，但仍可指定外部 Draft。"
        }, 11, true);
        panel.Children.Add(mtpStatus);
        var mtpSettings = new StackPanel { Margin = new Thickness(16, 0, 0, 0) };
        mtpSettings.Children.Add(Text("MTP 來源", 12, true));
        var mtpSource = new ComboBox { Foreground = Paint("#172036"), Background = Paint("#E2E9F2") };
        var nativeMtpItem = new ComboBoxItem { Content = new TextBlock { Text = nativeMtpAvailable ? "模型內建 MTP / NextN" : "模型內建 MTP / NextN（此 GGUF 不可用）", Foreground = Paint("#172036") }, Tag = "native", IsEnabled = nativeMtpAvailable, Foreground = Paint("#172036"), Background = Paint("#E2E9F2") };
        var externalMtpItem = new ComboBoxItem { Content = new TextBlock { Text = "外部 MTP Draft GGUF", Foreground = Paint("#172036") }, Tag = "external", Foreground = Paint("#172036"), Background = Paint("#E2E9F2") };
        mtpSource.Items.Add(nativeMtpItem); mtpSource.Items.Add(externalMtpItem);
        string savedMtpSource = J.S(model, "mtp_source", "native");
        mtpSource.SelectedItem = savedMtpSource == "native" && nativeMtpAvailable ? nativeMtpItem : externalMtpItem;
        mtpSettings.Children.Add(mtpSource);
        var externalMtpSettings = new StackPanel { Margin = new Thickness(12, 6, 0, 0) };
        var mtpDraftPath = Field(externalMtpSettings, "外部 MTP Draft GGUF 路徑", J.S(model, "mtp_draft_path"));
        externalMtpSettings.Children.Add(Button("選擇 MTP Draft", () => { PickFile(mtpDraftPath, "GGUF 模型|*.gguf"); return Task.CompletedTask; }));
        externalMtpSettings.Children.Add(Text("外部 Draft 會額外占用 RAM / VRAM；目前 llama-fit-params 的獨立預估不會把這顆 Draft 一起算入，顯存吃緊時請提高「自訂預留顯示記憶體」。", 11, true));
        mtpSettings.Children.Add(externalMtpSettings);
        var mtpDraftMax = FieldWithHint(mtpSettings, "MTP 最大猜測 Token 數（spec-draft-n-max）", J.S(model, "mtp_draft_max", ""), "每次投機預測最多嘗試猜測的 token 數量。\n\n• 典型設置：2 或 3。\n• 範圍：1 ~ 16。\n• 保留空白或 auto：使用 AMIEBL 預設值（2）。", "auto（預設 2）");
        panel.Children.Add(mtpSettings);
        void RefreshMtpSettings()
        {
            bool enabled = mtp.IsChecked == true;
            mtpSettings.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
            string source = (mtpSource.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "external";
            externalMtpSettings.Visibility = enabled && source == "external" ? Visibility.Visible : Visibility.Collapsed;
        }
        mtpSource.SelectionChanged += (_, _) => { RefreshMtpSettings(); dirty = true; };
        mtp.Checked += (_, _) => RefreshMtpSettings();
        mtp.Unchecked += (_, _) => RefreshMtpSettings();
        RefreshMtpSettings();
        var vision = Check(panel, "啟用視覺輸入", J.B(model, "vision"));
        var projector = Field(panel, "配對的視覺模型（mmproj）", J.S(model, "mmproj")); panel.Children.Add(Button("選擇視覺模型", () => { PickFile(projector, "GGUF 視覺模型|*.gguf"); return Task.CompletedTask; }));
        var keep = Check(panel, "保持載入，不因閒置而卸載", J.B(model, "keep_loaded"));
        var idle = Field(panel, "閒置卸載分鐘（留白沿用系統設定）", J.S(model, "idle_minutes"));
        var profile = Choice(panel, "預設使用模式", J.S(model, "default_profile_id", "coding"), J.A(config, "profiles").Select(x => (J.S(x, "id"), J.S(x, "name"))).ToArray());
        var advanced = new StackPanel { Margin = new Thickness(0, 15, 0, 0) };
        var temperature = FieldWithHint(advanced, "Temperature（隨機度 / 創意程度）", J.S(model, "temperature", ""), "控制回答的「隨機性與創意發散程度」。\n\n• 範圍：0.0 ~ 2.0\n\n• 典型值：\n  - 寫程式／數學／邏輯推理：0.0 ~ 0.2（精準嚴謹、不胡思亂想）。\n  - 日常聊天／通用問答：0.7 ~ 0.8（自然生動）。\n  - 創意寫作／故事創作：0.9 ~ 1.1（詞彙豐富多元）。\n\n• 調高／調低影響：\n  - 調低：回答固定、冷靜、嚴謹，重複發問會得到一致答案。\n  - 調高：回答更有想像力，但太高（>1.2）容易語無倫次。\n\n• 保留空白或 auto：由客戶端或 llama.cpp 自動決定。", "auto（預設）");
        var topP = FieldWithHint(advanced, "Top P（核採樣 / 候選詞累積機率）", J.S(model, "top_p", ""), "控制候選字詞的「篩選累積機率範圍」。\n\n• 範圍：0.0 ~ 1.0\n\n• 典型值：0.9 ~ 0.95（或保持 auto）。\n\n• 調高／調低影響：\n  - 調低（如 0.5~0.7）：只在累積機率最高的核心詞彙中挑選，回答聚焦保守。\n  - 調高（如 0.95~1.0）：允許考慮更多可能的詞彙，多樣性更高。\n\n• 建議：通常保持 auto，若不熟悉請微調 Temperature 即可。\n\n• 保留空白或 auto：由客戶端或 llama.cpp 自動決定。", "auto（預設）");
        var topK = FieldWithHint(advanced, "Top K（候選詞數量上限）", J.S(model, "top_k", ""), "每次生成時「只保留機率最高的前 K 個詞」來進行抽樣。\n\n• 範圍：0 ~ 100（0 代表不設限）。\n\n• 典型值：40（llama.cpp 常用 40）。\n\n• 調高／調低影響：\n  - 調低（如 20）：強制排除冷門詞，杜絕奇怪生僻字。\n  - 調高（如 40~80）：詞彙更靈活豐富；0 代表不限制。\n\n• 保留空白或 auto：由客戶端或 llama.cpp 自動決定（通常為 40）。", "auto（預設）");
        var minP = FieldWithHint(advanced, "Min P（最低相對機率門檻）", J.S(model, "min_p", ""), "以「最高機率的詞」為基準，剔除相對機率太低的極冷門候選詞。\n\n• 範圍：0.0 ~ 1.0\n\n• 典型值：0.05（即相對最高機率不到 5% 的詞直接淘汰）；0 代表不啟用。\n\n• 調高／調低影響：\n  - 這是一種比 Top P 更自然的新型抗幻覺採樣技術。\n  - 調高（如 0.1）：更強烈排除冷門詞，提高回答嚴謹度。\n  - 調低（如 0.01~0.05）：放寬篩選，保持語言流暢。\n\n• 保留空白或 auto：由客戶端或 llama.cpp 自動決定（0.05 或停用）。", "auto（預設）");
        var reasoningInfo = Section("Reasoning 能力 · 自動偵測");
        string reasoningCapability = J.S(model, "reasoning_capability", "unknown");
        reasoningInfo.Children.Add(Text(reasoningCapability switch
        {
            "toggle" => "✓ 可切換 Thinking / Non-Thinking",
            "always" => "✓ 模型具有 reasoning，但 Chat Template 未提供可靠的關閉方式",
            "none" => "— 未從 Chat Template 偵測到可控制 reasoning",
            _ => "? 尚未能確認 reasoning 能力"
        }, 12, true));
        var detectedEfforts = J.A(model, "reasoning_efforts").Select(x => x?.ToString()).Where(x => !string.IsNullOrWhiteSpace(x)).ToArray();
        reasoningInfo.Children.Add(Text(detectedEfforts.Length > 0 ? "原生 Effort：" + string.Join(" / ", detectedEfforts) : "原生 Effort：無", 11, true));
        string defaultEffort = J.S(model, "reasoning_default_effort");
        if (!string.IsNullOrWhiteSpace(defaultEffort)) reasoningInfo.Children.Add(Text("模型預設 Effort：" + defaultEffort, 11, true));
        reasoningInfo.Children.Add(Text(J.B(model, "reasoning_budget_supported") ? "✓ 可使用 reasoning token budget" : "— 未偵測到可用的 reasoning token budget 標記", 11, true));
        string toggleKeys = string.Join(", ", J.A(model, "reasoning_toggle_keys").Select(x => x?.ToString()).Where(x => !string.IsNullOrWhiteSpace(x)));
        if (!string.IsNullOrWhiteSpace(toggleKeys)) reasoningInfo.Children.Add(Text("Template 控制：" + toggleKeys, 11, true));
        reasoningInfo.Children.Add(Text("來源：" + (J.S(model, "reasoning_detection", "pending") == "gguf" ? "GGUF tokenizer.chat_template" : "尚未完成 GGUF 偵測 / 舊設定相容"), 11, true));
        advanced.Children.Add(Card(reasoningInfo));
        panel.Children.Add(new Expander { Header = "進階採樣與能力設定", Content = advanced });
        async Task Save()
        {
            var changed = Clone(model); changed["name"] = Required(name, "模型名稱"); changed["path"] = Required(path, "模型路徑"); changed["context"] = Number(context, "上下文容量", 512, nativeContext > 0 ? nativeContext : 2_097_152); changed["cpu_threads"] = (int)Math.Round(cpuSlider.Value); changed["gpu_layers"] = autoFit.IsChecked == true ? J.I(model, "gpu_layers", -1) : Number(gpu, "GPU 層數", -1); changed["auto_fit"] = autoFit.IsChecked == true; changed["fit_target_enabled"] = customReserve.IsChecked == true; changed["fit_target_mib"] = Number(reserve, "預留顯示記憶體"); changed["cache_type"] = Value(cache); string selectedMtpSource = (mtpSource.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "external"; changed["mtp"] = mtp.IsChecked == true; changed["mtp_source"] = selectedMtpSource; changed["mtp_draft_path"] = mtpDraftPath.Text.Trim(); changed["mtp_draft_max"] = mtp.IsChecked == true && OptionalNumber(mtpDraftMax, "MTP 最大猜測 Token", 1, 64) is int draftVal ? JsonValue.Create(draftVal) : null; if (mtp.IsChecked == true && selectedMtpSource == "native" && !nativeMtpAvailable) throw new InvalidOperationException("此 GGUF 沒有可用的內建 MTP / NextN，請改用外部 MTP Draft。"); if (mtp.IsChecked == true && selectedMtpSource == "external" && string.IsNullOrWhiteSpace(mtpDraftPath.Text)) throw new InvalidOperationException("請指定外部 MTP Draft GGUF。"); changed["vision"] = vision.IsChecked == true; changed["mmproj"] = projector.Text.Trim(); changed["keep_loaded"] = keep.IsChecked == true; changed["idle_minutes"] = string.IsNullOrWhiteSpace(idle.Text) ? null : JsonValue.Create(Number(idle, "閒置卸載分鐘", 1)); changed["default_profile_id"] = Value(profile); changed["temperature"] = OptionalDecimal(temperature, "Temperature", 0, 5) is double tempVal ? JsonValue.Create(tempVal) : null; changed["top_p"] = OptionalDecimal(topP, "Top P", 0, 1) is double topPVal ? JsonValue.Create(topPVal) : null; changed["top_k"] = OptionalNumber(topK, "Top K", 0, 100000) is int topKVal ? JsonValue.Create(topKVal) : null; changed["min_p"] = OptionalDecimal(minP, "Min P", 0, 1) is double minPVal ? JsonValue.Create(minPVal) : null;
            await SaveConfig(c => { var models = J.A(c, "models"); int index = models.ToList().FindIndex(x => J.S(x, "id") == id); if (index < 0) throw new InvalidOperationException("此模型已被移除，請重新整理模型庫。"); models[index] = changed; });
        }
        pendingSave = Save;
        panel.Children.Add(ActionRow(Button("儲存模型設定", Save, true), Button("設為預設模型", async () => { await Save(); await SaveConfig(c => c["default_model_id"] = id); ShowNotice("已設為預設模型。"); }), Button("載入此模型", async () => { await Save(); await api!.Post("/manager/load", new JsonObject { ["model_id"] = id }); ShowNotice("已要求載入模型。"); await Poll(); })));
        panel.Children.Add(ActionRow(Button("恢復保守預設值", async () =>
        {
            if (!Confirm("會重設此模型的載入與採樣參數，保留名稱及檔案位置。")) return;
            var defaults = NewModel(J.S(model, "path"), J.S(model, "name"), id); defaults["default_profile_id"] = J.S(config, "default_profile_id"); await SaveConfig(c => { var models = J.A(c, "models"); models[models.ToList().FindIndex(x => J.S(x, "id") == id)] = defaults; }); ShowPage("模型庫");
        }), Button("移除登錄", async () =>
        {
            if (!Confirm("只移除此模型的登錄與設定，不會刪除 GGUF 檔案。")) return;
            await SaveConfig(c => { var models = J.A(c, "models"); var found = models.FirstOrDefault(x => J.S(x, "id") == id); if (found is not null) models.Remove(found); if (J.S(c, "default_model_id") == id) c["default_model_id"] = models.Count > 0 ? J.S(models[0], "id") : ""; }); ShowPage("模型庫");
        })));
        return Card(panel);
    }
    private bool Confirm(string message) => System.Windows.MessageBox.Show(this, message, "確認操作", MessageBoxButton.OKCancel, MessageBoxImage.Question) == MessageBoxResult.OK;
    private void PickFile(TextBox input, string filter)
    {
        var picker = new OpenFileDialog { Filter = filter, CheckFileExists = true }; if (picker.ShowDialog(this) == true) input.Text = picker.FileName;
    }
    private static JsonObject NewModel(string path, string name, string id) => new() { ["id"] = id, ["name"] = name, ["path"] = path, ["mmproj"] = "", ["vision"] = false, ["context"] = 32768, ["native_context"] = 0, ["cpu_threads"] = 0, ["gpu_layers"] = -1, ["auto_fit"] = true, ["fit_target_enabled"] = false, ["fit_target_mib"] = 2048, ["cache_type"] = "q8_0", ["mtp"] = false, ["mtp_source"] = "native", ["mtp_draft_path"] = "", ["mtp_draft_max"] = null, ["mtp_capability"] = "unknown", ["mtp_layers"] = 0, ["keep_loaded"] = false, ["idle_minutes"] = null, ["default_profile_id"] = "coding", ["temperature"] = null, ["top_p"] = null, ["top_k"] = null, ["min_p"] = null, ["reasoning_supported"] = false, ["reasoning_capability"] = "unknown", ["reasoning_efforts"] = new JsonArray(), ["reasoning_default_effort"] = "", ["reasoning_budget_supported"] = false, ["reasoning_toggle_keys"] = new JsonArray(), ["reasoning_detection"] = "pending" };
    private async Task AddModel(string path, string name)
    {
        if (J.A(config, "models").Any(x => string.Equals(J.S(x, "path"), path, StringComparison.OrdinalIgnoreCase))) { ShowNotice("此模型已在模型庫中。"); return; }
        string id = "model-" + Guid.NewGuid().ToString("N")[..8];
        await SaveConfig(c => { var models = J.A(c, "models"); var added = NewModel(path, name, id); added["default_profile_id"] = J.S(c, "default_profile_id"); models.Add(added); c["models"] = models; if (string.IsNullOrEmpty(J.S(c, "default_model_id"))) c["default_model_id"] = id; });
    }
    private async Task AddModelFile()
    {
        if (!await LeaveEditor()) return; var picker = new OpenFileDialog { Filter = "GGUF 模型|*.gguf", CheckFileExists = true }; if (picker.ShowDialog(this) != true) return;
        await AddModel(picker.FileName, Path.GetFileNameWithoutExtension(picker.FileName)); ShowPage("模型庫");
    }
    private async Task ScanModels()
    {
        if (!await LeaveEditor()) return;
        var result = await api!.Post("/manager/scan"); var models = J.A(result, "models"); var content = Section("掃描結果", $"找到 {models.Count} 個模型與 {J.A(result, "projectors").Count} 個視覺模型。選取模型後加入模型庫。");
        var checks = new List<(CheckBox check, JsonNode model)>();
        foreach (var model in models.Where(x => x is not null))
        {
            bool exists = J.A(config, "models").Any(x => string.Equals(J.S(x, "path"), J.S(model, "path"), StringComparison.OrdinalIgnoreCase));
            var check = new CheckBox { Content = J.S(model, "name") + (exists ? "（已加入）" : ""), IsEnabled = !exists, Margin = new Thickness(0, 12, 0, 6) }; content.Children.Add(check); content.Children.Add(Text(J.S(model, "path"), 11, true)); checks.Add((check, model!));
        }
        var dialog = new Window { Owner = this, Title = "掃描模型", Width = 680, Height = 560, WindowStartupLocation = WindowStartupLocation.CenterOwner, Content = Scroll(content), Padding = new Thickness(25), ShowInTaskbar = false };
        content.Children.Add(ActionRow(Button("加入選取模型", async () => { foreach (var entry in checks.Where(x => x.check.IsChecked == true)) await AddModel(J.S(entry.model, "path"), J.S(entry.model, "name")); dialog.Close(); ShowPage("模型庫"); }, true), Button("關閉", () => { dialog.Close(); return Task.CompletedTask; })));
        dialog.ShowDialog();
    }
}
