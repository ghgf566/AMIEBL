using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;
using Forms = System.Windows.Forms;

namespace LocalModelManager;

public sealed class SingleInstance : IDisposable
{
    private readonly Mutex mutex;
    private readonly EventWaitHandle signal;
    private readonly RegisteredWaitHandle? listener;
    public bool IsPrimary { get; }
    public SingleInstance(string dataDir, Action reveal)
    {
        string hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(dataDir.ToUpperInvariant())))[..20];
        mutex = new(true, @"Local\LocalModelManager_" + hash, out bool first); IsPrimary = first;
        signal = new(false, EventResetMode.AutoReset, @"Local\LocalModelManager_Show_" + hash);
        if (!first) signal.Set();
        else listener = ThreadPool.RegisterWaitForSingleObject(signal, (_, _) => reveal(), null, Timeout.Infinite, false);
    }
    public void Dispose() { listener?.Unregister(null); signal.Dispose(); if (IsPrimary) { try { mutex.ReleaseMutex(); } catch (ApplicationException) { } } mutex.Dispose(); }
}
public sealed class TrayService : IDisposable
{
    private readonly Forms.NotifyIcon icon;
    public TrayService(Action reveal, Action load, Action unload, Action keep, Action pause, Action exit)
    {
        string path = Path.Combine(AppContext.BaseDirectory,"assets","manager.ico");
        icon = new() { Icon = File.Exists(path) ? new System.Drawing.Icon(path) : System.Drawing.SystemIcons.Application, Text="AMIEBL", Visible=true };
        icon.DoubleClick += (_, _) => reveal();
        var menu = new Forms.ContextMenuStrip();
        foreach (var (text, action) in new[] { ("開啟主視窗",reveal),("載入預設模型",load),("卸載模型",unload),("保持載入／恢復卸載",keep),("暫停／恢復接收請求",pause),("完全結束",exit) })
            menu.Items.Add(text, null, (_, _) => action());
        icon.ContextMenuStrip = menu;
    }
    public void Update(string text) => icon.Text = text[..Math.Min(63,text.Length)];
    public void Dispose() { icon.Visible=false; icon.ContextMenuStrip?.Dispose(); icon.Icon?.Dispose(); icon.Dispose(); }
}
public static class StartupService
{
    public static void Set(bool enabled, string dataDir)
    {
        using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run",true) ?? throw new InvalidOperationException("無法開啟自動啟動設定。");
        if (enabled) key.SetValue("LocalModelManager", "\"" + Environment.ProcessPath + "\" --data-dir \"" + dataDir + "\"");
        else key.DeleteValue("LocalModelManager",false);
    }
}
