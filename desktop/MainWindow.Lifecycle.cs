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

    public async Task RunSmokeTest()
    {
        // Render native WPF pages without making a visible window or touching autostart.
        timer.Stop();
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
            ["ok"] = true, ["pages"] = results, ["config_read"] = true, ["state"] = J.S(status, "state"),
            ["connection_read"] = true, ["model_count"] = J.A(config, "models").Count,
            ["profile_count"] = J.A(config, "profiles").Count,
            ["autostart_changed"] = false, ["model_loaded"] = J.S(status, "state") != "unloaded",
            ["api_port"] = App.Port
        }.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    }
}
