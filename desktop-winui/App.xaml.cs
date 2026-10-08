using Microsoft.UI.Xaml;

namespace AMIEBL.WinUI;
public partial class App : Application
{
    private MainWindow? window;
    private static void LogStartup(Exception ex)
    {
        var args=Environment.GetCommandLineArgs();int i=Array.IndexOf(args,"--data-dir");
        string dir=i>=0 && i+1<args.Length ? args[i+1] : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"LocalModelManager");
        Directory.CreateDirectory(dir);File.WriteAllText(Path.Combine(dir,"winui-startup-error.log"),ex.ToString());
        if(args.Contains("--smoke-test")) File.WriteAllText(Path.Combine(dir,"winui-smoke-test.json"),new System.Text.Json.Nodes.JsonObject { ["ok"]=false,["error"]=ex.ToString() }.ToJsonString());
    }
    public App()
    {
        UnhandledException+=(_,e)=>LogStartup(e.Exception);
        try { InitializeComponent(); } catch(Exception ex){LogStartup(ex);throw;}
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            window = new MainWindow(Environment.GetCommandLineArgs().Skip(1).ToArray());
            window.Start();
        }
        catch(Exception ex){LogStartup(ex);Exit();}
    }
}
