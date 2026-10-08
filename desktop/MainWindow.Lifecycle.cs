using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Forms = System.Windows.Forms;

namespace LocalModelManager;

public partial class MainWindow
{
    private Forms.ToolStripMenuItem? trayLoad, trayUnload, trayKeep, trayPause, trayState;
    private Icon? ownedIcon;

    private Icon MakeTrayIcon()
    {
        for (DirectoryInfo? dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            string path = Path.Combine(dir.FullName, "assets", "manager.ico");
            if (!File.Exists(path)) continue;
            try { using var source = new Icon(path); ownedIcon = (Icon)source.Clone(); return ownedIcon; }
            catch (ArgumentException) { }
        }
        // An in-memory icon keeps the tray usable if someone moves the executable alone.
        using var bitmap = new Bitmap(32, 32);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.Clear(System.Drawing.Color.FromArgb(17, 24, 39));
            graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using var brush = new SolidBrush(System.Drawing.Color.FromArgb(93, 228, 187));
            graphics.FillPolygon(brush, [new PointF(16, 3), new PointF(29, 16), new PointF(16, 29), new PointF(3, 16)]);
            using var center = new SolidBrush(System.Drawing.Color.FromArgb(17, 24, 39));
            graphics.FillEllipse(center, 11, 11, 10, 10);
        }
        IntPtr handle = bitmap.GetHicon();
        try { using var source = System.Drawing.Icon.FromHandle(handle); ownedIcon = (Icon)source.Clone(); }
        finally { DestroyIcon(handle); }
        return ownedIcon;
    }

    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr handle);

    private void BuildTray()
    {
        var menu = new Forms.ContextMenuStrip();
        trayState = new Forms.ToolStripMenuItem("正在連接服務") { Enabled = false };
        menu.Items.Add(trayState);
        Forms.ToolStripMenuItem Add(string label, Func<Task> action)
        {
            var item = new Forms.ToolStripMenuItem(label);
            item.Click += (_, _) => Dispatcher.InvokeAsync(async () => await Guard(action));
            menu.Items.Add(item); return item;
        }
        Add("開啟主視窗", () => { Reveal(); return Task.CompletedTask; });
        menu.Items.Add(new Forms.ToolStripSeparator());
        trayLoad = Add("載入預設模型", LoadDefault);
        trayUnload = Add("卸載模型", Unload);
        trayKeep = Add("保持載入", ToggleKeep);
        trayPause = Add("暫停接收新請求", ToggleAccepting);
        menu.Items.Add(new Forms.ToolStripSeparator());
        Add("完全結束", RequestExit);
        menu.Opening += (_, _) => UpdateTray();
        tray.ContextMenuStrip = menu;
    }

    private void UpdateTray()
    {
        if (trayState is null) return;
        trayState.Text = failedPolls > 0 ? "服務暫時離線" : StateName(J.S(status, "state"));
        bool connected = api is not null && failedPolls == 0;
        trayLoad!.Enabled = connected && !string.IsNullOrEmpty(DefaultModel()) && J.S(status, "state") is not "loading" and not "unloading";
        trayUnload!.Enabled = connected && J.S(status, "state") is "ready" or "loading";
        trayKeep!.Enabled = connected && !string.IsNullOrEmpty(DefaultModel());
        string id = J.S(status, "model_id", DefaultModel());
        trayKeep.Checked = J.B(J.A(config, "models").FirstOrDefault(x => J.S(x, "id") == id), "keep_loaded");
        trayPause!.Enabled = connected;
        trayPause.Text = J.B(status, "accepting", true) ? "暫停接收新請求" : "恢復接收新請求";
    }

    private async Task RequestExit()
    {
        if (exiting || !await LeaveEditor()) return;
        if ((J.I(status, "active_count") > 0 || J.I(status, "queued_count") > 0) && !Confirm("目前仍有請求執行或等待中。完全結束會取消請求並卸載模型。")) return;
        await ((App)Application.Current).ExitManager();
    }

    private static T? FindSmokeControl<T>(DependencyObject node, string name) where T : FrameworkElement
    {
        if (node is T found && found.Name == name) return found;
        foreach (var child in LogicalTreeHelper.GetChildren(node).OfType<DependencyObject>())
            if (FindSmokeControl<T>(child, name) is T match) return match;
        return null;
    }

    private async Task VerifyEditorRefresh()
    {
        // Only the isolated --smoke-test data directory is modified; restore
        // its original values even if an assertion fails.
        var original = Clone(config);
        try
        {
            if (J.A(config, "models").Count < 2 || J.A(config, "profiles").Count < 2)
                throw new InvalidOperationException("編輯器回歸測試需要兩個模型與使用模式。");
            ShowPage("模型庫");
            var modelId = J.S(J.A(config, "models")[0], "id");
            var otherModelId = J.S(J.A(config, "models")[1], "id");
            var input = FindSmokeControl<System.Windows.Controls.TextBox>((DependencyObject)PageContent.Content, "ModelContextInput")
                ?? throw new InvalidOperationException("找不到 Context 輸入欄。");
            int changedContext = input.Text == "512" ? 1024 : 512;
            input.Text = changedContext.ToString();
            await pendingSave!();
            SelectModel(otherModelId); SelectModel(modelId);
            input = FindSmokeControl<System.Windows.Controls.TextBox>((DependencyObject)PageContent.Content, "ModelContextInput");
            if (input?.Text != changedContext.ToString()) throw new InvalidOperationException("模型切回後顯示舊 Context。");

            ShowPage("使用模式");
            var profileId = J.S(J.A(config, "profiles")[0], "id");
            var otherProfileId = J.S(J.A(config, "profiles")[1], "id");
            var budget = FindSmokeControl<System.Windows.Controls.TextBox>((DependencyObject)PageContent.Content, "ProfileBudgetInput")
                ?? throw new InvalidOperationException("找不到思考預算輸入欄。");
            int changedBudget = budget.Text == "64" ? 65 : 64;
            budget.Text = changedBudget.ToString();
            await pendingSave!();
            SelectProfile(otherProfileId); SelectProfile(profileId);
            budget = FindSmokeControl<System.Windows.Controls.TextBox>((DependencyObject)PageContent.Content, "ProfileBudgetInput");
            if (budget?.Text != changedBudget.ToString()) throw new InvalidOperationException("模式切回後顯示舊預算。");
        }
        finally
        {
            config = await api!.Put("/manager/config", original);
            dirty = false; pendingSave = null;
        }
    }

    public async Task RunSmokeTest()
    {
        // Render native WPF pages without making a visible window or touching autostart.
        timer.Stop();
        bool verifyEditors = App.Arguments.Contains("--editor-refresh-test");
        if (verifyEditors)
        {
            if (App.Option("--data-dir") is null)
                throw new InvalidOperationException("編輯器回歸測試必須明確指定隔離資料目錄。");
            await VerifyEditorRefresh();
        }
        var results = new JsonArray();
        string screenshots = Path.Combine(App.DataDir, "screenshots"); Directory.CreateDirectory(screenshots);
        for (int i = 0; i < Pages.Length; i++)
        {
            ShowPage(Pages[i]); await Poll();
            if (Content is not FrameworkElement surface) throw new InvalidOperationException("主視窗缺少可繪製內容。");
            surface.Width = 1240; surface.Height = 820;
            surface.Measure(new System.Windows.Size(1240, 820)); surface.Arrange(new Rect(0, 0, 1240, 820)); surface.UpdateLayout();
            await Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.Render);
            var bitmap = new RenderTargetBitmap(1240, 820, 96, 96, PixelFormats.Pbgra32); bitmap.Render(surface);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            string path = Path.Combine(screenshots, $"{i + 1}-{Pages[i]}.png");
            using (var stream = File.Create(path)) encoder.Save(stream);
            results.Add(new JsonObject { ["page"] = Pages[i], ["rendered"] = true, ["path"] = path });
        }
        var connection = await api!.Get("/manager/connection");
        await File.WriteAllTextAsync(Path.Combine(App.DataDir, "desktop-smoke-test.json"), new JsonObject
        {
            ["ok"] = true, ["editor_refresh_verified"] = verifyEditors, ["pages"] = results, ["config_read"] = true, ["state"] = J.S(status, "state"),
            ["connection_read"] = true, ["model_count"] = J.A(config, "models").Count,
            ["profile_count"] = J.A(config, "profiles").Count,
            ["autostart_changed"] = false, ["model_loaded"] = J.S(status, "state") != "unloaded",
            ["api_port"] = App.Port
        }.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    }
}
