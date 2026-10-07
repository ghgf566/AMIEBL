using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace LocalModelManager;

public partial class App : Application
{
    public static string DataDir { get; private set; } = "";
    public static string[] Arguments { get; private set; } = [];
    public static int Port { get; private set; }
    public static bool Background { get; private set; }
    public static bool SmokeTest { get; private set; }
    public static string? Option(string name)
    {
        int i = Array.IndexOf(Arguments, name);
        return i >= 0 && i + 1 < Arguments.Length ? Arguments[i + 1] : null;
    }
    private Mutex? mutex;
    private EventWaitHandle? showEvent;
    private RegisteredWaitHandle? waitHandle;
    private Process? backend;
    private OwnedProcessJob? processJob;
    private readonly object logLock = new();
    private ManagerClient? api;
    private bool shuttingDown;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Arguments = e.Args;
        string defaultDataDir = File.Exists(Path.Combine(AppContext.BaseDirectory, "portable.flag"))
            ? Path.Combine(AppContext.BaseDirectory, "data")
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalModelManager");
        DataDir = Path.GetFullPath(Option("--data-dir") ?? defaultDataDir);
        Directory.CreateDirectory(DataDir);
        string hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(DataDir.ToUpperInvariant())))[..20];
        mutex = new Mutex(true, @"Local\LocalModelManager_" + hash, out bool first);
        showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\LocalModelManager_Show_" + hash);
        if (!first) { showEvent.Set(); Shutdown(); return; }
        JsonObject local = ReadLocalConfig();
        Port = int.TryParse(Option("--port"), out int p) ? p : J.I(local, "api_port", 8080);
        Background = Arguments.Contains("--background") || J.B(local, "start_hidden");
        SmokeTest = Arguments.Contains("--smoke-test");
        if (Arguments.Contains("--self-test"))
        {
            File.WriteAllText(Path.Combine(DataDir, "desktop-self-test.json"), new JsonObject { ["ok"] = true, ["framework"] = Environment.Version.ToString(), ["data_dir"] = DataDir, ["port"] = Port, ["backend"] = FindBackend() }.ToJsonString());
            Shutdown(); return;
        }
        var window = new MainWindow();
        MainWindow = window;
        if (!Background && !SmokeTest) window.Show();
        waitHandle = ThreadPool.RegisterWaitForSingleObject(showEvent, (_, _) => Dispatcher.BeginInvoke(window.Reveal), null, Timeout.Infinite, false);
        DispatcherUnhandledException += (_, args) => { window.ShowError(args.Exception.Message); args.Handled = true; };
        try
        {
            await EnsureBackend();
            string token = (await File.ReadAllTextAsync(Path.Combine(DataDir, "admin-token"))).Trim();
            api = new ManagerClient(Port, token);
            await window.Connect(api);
            if (SmokeTest)
            {
                await window.RunSmokeTest();
                await ExitManager();
            }
        }
        catch (Exception ex)
        {
            if (!SmokeTest) window.Reveal();
            window.ShowStartupFailure(ex.Message);
            if (SmokeTest)
            {
                File.WriteAllText(Path.Combine(DataDir, "desktop-smoke-test.json"), new JsonObject { ["ok"] = false, ["error"] = ex.ToString() }.ToJsonString());
                await ExitManager();
            }
        }
    }
    private static JsonObject ReadLocalConfig()
    {
        try { return JsonNode.Parse(File.ReadAllText(Path.Combine(DataDir, "config.json")))?.AsObject() ?? new(); }
        catch { return new(); }
    }
    private static string FindBackend()
    {
        if (Option("--backend") is string configured) return Path.GetFullPath(configured);
        for (DirectoryInfo? dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            string path = Path.Combine(dir.FullName, "backend", "manager.py");
            if (File.Exists(path)) return path;
        }
        throw new FileNotFoundException("找不到背景服務 backend\\manager.py。請將完整程式資料夾解壓縮後再啟動。");
    }
    private async Task EnsureBackend()
    {
        if (await ManagerClient.IsManager(Port))
        {
            if (!File.Exists(Path.Combine(DataDir, "admin-token"))) throw new InvalidOperationException($"連接埠 {Port} 已有另一個管理器執行，但此資料夾沒有相符的存取憑證。請使用原資料夾，或改用另一個連接埠。");
            return;
        }
        string python = await ResolvePythonAsync();
        var start = new ProcessStartInfo(python) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = DataDir };
        start.ArgumentList.Add(FindBackend());
        start.ArgumentList.Add("--data-dir"); start.ArgumentList.Add(DataDir);
        start.ArgumentList.Add("--port"); start.ArgumentList.Add(Port.ToString());
        start.Environment["PYTHONUTF8"] = "1";
        // A portable bundle keeps llama.cpp and (optionally) models beside the
        // desktop executable. These environment values are only consumed when
        // the backend creates its first config, so a user's existing choices
        // always win on later launches.
        if (!File.Exists(Path.Combine(DataDir, "config.json")))
        {
            string bundledEngine = Path.Combine(AppContext.BaseDirectory, "llama.cpp");
            if (File.Exists(Path.Combine(bundledEngine, "llama-server.exe"))) start.Environment["LMM_ENGINE_DIR"] = bundledEngine;
            string bundledModels = Path.Combine(AppContext.BaseDirectory, "models");
            if (Directory.Exists(bundledModels)) start.Environment["LMM_MODEL_DIR"] = bundledModels;
        }
        backend = new Process { StartInfo = start, EnableRaisingEvents = true };
        backend.OutputDataReceived += (_, e) => Log(e.Data);
        backend.ErrorDataReceived += (_, e) => Log(e.Data);
        backend.Start(); backend.BeginOutputReadLine(); backend.BeginErrorReadLine();
        processJob = OwnedProcessJob.TryCreate(backend, out string? jobError);
        if (jobError is not null) Log("Process ownership job unavailable; graceful tree cleanup remains active: " + jobError);
        for (int i = 0; i < 80; i++)
        {
            if (backend.HasExited) throw new InvalidOperationException($"背景服務無法啟動（代碼 {backend.ExitCode}）。可能是連接埠 {Port} 被占用、Python 套件缺少，或設定不正確。詳情：{Path.Combine(DataDir, "desktop-runtime.log")}");
            if (await ManagerClient.IsManager(Port) && File.Exists(Path.Combine(DataDir, "admin-token"))) return;
            await Task.Delay(250);
        }
        throw new TimeoutException("背景服務啟動超時。請從資料資料夾的 desktop-runtime.log 檢查原因。");
    }
    private async Task<string> ResolvePythonAsync()
    {
        string candidate = Option("--python") is string explicitPython
            ? Path.GetFullPath(explicitPython)
            : DiscoverPython();

        // The smoke fixture intentionally uses a stdlib-only Python process.
        // Real installations get a private venv so the app never mutates a
        // user's global Python environment.
        if (IsFixtureBackend()) return candidate;
        if (await PythonHasDependenciesAsync(candidate)) return candidate;

        string venvRoot = Path.Combine(DataDir, "runtime");
        string venvPython = Path.Combine(venvRoot, "Scripts", "python.exe");
        if (!File.Exists(venvPython))
        {
            (int Code, string Output) created;
            try { created = await RunProcessAsync(candidate, new[] { "-m", "venv", venvRoot }); }
            catch (Exception ex)
            {
                throw new InvalidOperationException("找不到可用的 Python 執行環境。請安裝 Python 3.11 以上，或將完整的 Python runtime 放入 runtime\\python。", ex);
            }
            if (created.Code != 0 || !File.Exists(venvPython))
                throw new InvalidOperationException("找不到可用的 Python 執行環境。請安裝 Python 3.11 以上，或將完整的 Python runtime 放入 runtime\\python。" + Environment.NewLine + created.Output.Trim());
        }
        if (!await PythonHasDependenciesAsync(venvPython))
        {
            string requirements = Path.Combine(Path.GetDirectoryName(FindBackend())!, "requirements.txt");
            if (!File.Exists(requirements)) throw new FileNotFoundException("找不到 backend\\requirements.txt，無法準備背景服務套件。", requirements);
            Log("正在準備背景服務套件；第一次啟動可能需要幾分鐘。 ");
            var installed = await RunProcessAsync(venvPython, new[] { "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", requirements });
            if (installed.Code != 0 || !await PythonHasDependenciesAsync(venvPython))
            {
                throw new InvalidOperationException("背景服務需要的 Python 套件安裝失敗。請確認網路可用，或將已安裝套件的 Python runtime 放入 runtime\\python。" + Environment.NewLine + installed.Output.Trim());
            }
        }
        return venvPython;
    }

    private bool IsFixtureBackend()
    {
        try { return string.Equals(Path.GetFileName(FindBackend()), "fake_manager.py", StringComparison.OrdinalIgnoreCase); }
        catch { return false; }
    }

    private static async Task<bool> PythonHasDependenciesAsync(string python)
    {
        if (string.IsNullOrWhiteSpace(python)) return false;
        try
        {
            var result = await RunProcessAsync(python, new[] { "-c", "import fastapi,httpx,uvicorn" });
            return result.Code == 0;
        }
        catch { return false; }
    }

    private static async Task<(int Code, string Output)> RunProcessAsync(string fileName, IEnumerable<string> arguments)
    {
        var start = new ProcessStartInfo(fileName)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (string argument in arguments) start.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = start };
        process.Start();
        Task<string> stdout = process.StandardOutput.ReadToEndAsync();
        Task<string> stderr = process.StandardError.ReadToEndAsync();
        try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromMinutes(20)); }
        catch
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
            throw;
        }
        string output = (await stdout) + Environment.NewLine + (await stderr);
        if (output.Length > 12_000) output = output[^12_000..];
        return (process.ExitCode, output);
    }

    private static string DiscoverPython()
    {
        string[] portableCandidates =
        [
            Path.Combine(AppContext.BaseDirectory, "runtime", "venv", "Scripts", "python.exe"),
            Path.Combine(AppContext.BaseDirectory, "runtime", "python", "python.exe"),
            Path.Combine(DataDir, "runtime", "Scripts", "python.exe"),
        ];
        string? portable = portableCandidates.FirstOrDefault(File.Exists);
        if (portable is not null) return portable;
        string pinned = Path.Combine(AppContext.BaseDirectory, "python-path.txt");
        if (File.Exists(pinned)) { string path = File.ReadAllText(pinned).Trim(); if (File.Exists(path)) return path; }
        string installed = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python");
        if (Directory.Exists(installed))
        {
            var candidate = Directory.EnumerateDirectories(installed).OrderByDescending(x => x).Select(x => Path.Combine(x, "python.exe")).FirstOrDefault(File.Exists);
            if (candidate is not null) return candidate;
        }
        return "python.exe";
    }
    private void Log(string? line)
    {
        if (line is null) return;
        lock (logLock)
        {
            try
            {
                string path = Path.Combine(DataDir, "desktop-runtime.log");
                if (File.Exists(path) && new FileInfo(path).Length > 2_000_000) File.Move(path, path + ".previous", true);
                File.AppendAllText(path, DateTimeOffset.Now.ToString("u") + " " + line + Environment.NewLine);
            }
            catch { }
        }
    }
    public async Task ExitManager()
    {
        if (shuttingDown) return;
        shuttingDown = true;
        if (MainWindow is MainWindow window) window.PrepareExit();
        if (backend is not null && !backend.HasExited)
        {
            try { if (api is not null) await api.Post("/manager/shutdown"); } catch (Exception ex) { Log(ex.Message); }
            try { await backend.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15)); }
            catch { try { if (!backend.HasExited) backend.Kill(entireProcessTree: true); } catch (Exception ex) { Log(ex.Message); } }
        }
        processJob?.Dispose(); processJob = null;
        api?.Dispose(); backend?.Dispose(); waitHandle?.Unregister(null); showEvent?.Dispose();
        try { mutex?.ReleaseMutex(); } catch { }
        mutex?.Dispose(); Shutdown();
    }

    protected override void OnSessionEnding(SessionEndingCancelEventArgs e)
    {
        // Windows may not allow asynchronous cleanup while signing out. Only owned children are affected.
        processJob?.Dispose(); processJob = null;
        try { if (backend is not null && !backend.HasExited) backend.Kill(entireProcessTree: true); } catch { }
        base.OnSessionEnding(e);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        processJob?.Dispose();
        base.OnExit(e);
    }
}
