using System.Diagnostics;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;
using System.Text.Json;
using System.Text.Json.Nodes;
using LocalModelManager;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Data;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace AMIEBL.WinUI;

public sealed partial class MainWindow : Window
{
    private readonly string[] arguments;
    private readonly WorkspaceViewModel vm = new();
    private readonly BackendHost host;
    private SingleInstance? instance;
    private TrayService? tray;
    private ManagerClient? api;
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(1.5) };
    private SettingsEditorViewModel? editor;
    private ListView? entityList;
    private ContentControl? entityEditor;
    private TextBlock? capabilityText;
    private JsonArray requests = new();
    private string page = "總覽";
    private bool working, polling, exiting, selecting;
    private readonly Dictionary<string, Control> inputs = new();
    private string? selectedTask;
    private string serviceLogs="";
    private IntPtr Hwnd => WindowNative.GetWindowHandle(this);
    private bool Smoke => arguments.Contains("--smoke-test");
    private string? Option(string key) { int i = Array.IndexOf(arguments, key); return i >= 0 && i+1 < arguments.Length ? arguments[i+1] : null; }

    public MainWindow(string[] arguments)
    {
        this.arguments = arguments; host = new(arguments);
        InitializeComponent(); Root.DataContext = vm; TitleText.Text="正在啟動"; PageHost.Content=Text("正在連接本機服務…",18);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1240,860));
        AppWindow.Closing += async (_, e) =>
        {
            if (exiting) return;
            e.Cancel = true;
            if (J.B(vm.Config,"close_to_tray",true) && tray is not null) AppWindow.Hide();
            else await Run(Exit);
        };
        timer.Tick += async (_, _) => await Poll();
    }

    public async void Start()
    {
        try
        {
            instance = new(host.DataDir, () => DispatcherQueue.TryEnqueue(Reveal));
            if (!instance.IsPrimary) { Close(); return; }
            Activate();
            if (Smoke) AppWindow.Hide();
            api = await host.Connect(); vm.Api = api;
            vm.Config = await api.Get("/manager/config");
            vm.Status = await api.Get("/manager/status");
            tray = new(() => Queue(() => { Reveal(); return Task.CompletedTask; }), () => Queue(LoadDefault), () => Queue(Unload), () => Queue(ToggleKeep), () => Queue(ToggleAccepting), () => Queue(Exit));
            Navigation.SelectedItem = Navigation.MenuItems[0]; ShowPage("總覽");
            if (Smoke) { await SmokeTest(); await Shutdown(); return; }
            if (arguments.Contains("--background") || J.B(vm.Config,"start_hidden")) AppWindow.Hide();
            timer.Start(); await Poll();
        }
        catch (Exception ex)
        {
            Error(ex);
            if (Smoke)
            {
                await File.WriteAllTextAsync(Path.Combine(host.DataDir,"winui-smoke-test.json"), new JsonObject { ["ok"]=false,["error"]=ex.ToString() }.ToJsonString());
                await Shutdown();
            }
            else Reveal();
        }
    }
    private void Queue(Func<Task> action) => DispatcherQueue.TryEnqueue(async () => await Run(action));
    private void Reveal() { AppWindow.Show(); Activate(); }
    private void Error(Exception ex) { Notice.Severity=InfoBarSeverity.Error; Notice.Message=ex.Message; Notice.IsOpen=true; }
    private void Message(string text) { Notice.Severity=InfoBarSeverity.Informational; Notice.Message=text; Notice.IsOpen=true; }
    private async Task Run(Func<Task> action)
    {
        if (working || exiting) return;
        working=true; Navigation.IsEnabled=false; PageHost.IsEnabled=false;
        try { await action(); } catch (Exception ex) { Error(ex); }
        finally { working=false; Navigation.IsEnabled=true; PageHost.IsEnabled=true; }
    }
    private async Task<bool> Confirm(string text, string title="確認操作") => await new ContentDialog { XamlRoot=Root.XamlRoot, Title=title, Content=text, PrimaryButtonText="確認", CloseButtonText="取消", DefaultButton=ContentDialogButton.Close }.ShowAsync() == ContentDialogResult.Primary;
    private async Task<bool> LeaveEditor()
    {
        if (!vm.Editor.IsDirty) return true;
        var result=await new ContentDialog { XamlRoot=Root.XamlRoot, Title="尚未儲存的修改", Content="要先儲存目前的修改嗎？", PrimaryButtonText="儲存", SecondaryButtonText="捨棄修改", CloseButtonText="取消", DefaultButton=ContentDialogButton.Close }.ShowAsync();
        if (result==ContentDialogResult.None) return false;
        if (result==ContentDialogResult.Primary) await SaveEditor();
        else vm.Editor.Discard();
        return true;
    }
    private async void Navigate(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (api is null || selecting || args.SelectedItem is not NavigationViewItem item) return;
        string next=item.Tag?.ToString() ?? "總覽";
        if (next==page) return;
        if (working) { RestoreNavigation(); return; }
        await Run(async () => { if (await LeaveEditor()) ShowPage(next); else RestoreNavigation(); });
    }
    private void RestoreNavigation()
    {
        selecting=true; Navigation.SelectedItem=Navigation.MenuItems.OfType<NavigationViewItem>().First(x => x.Tag?.ToString()==page); selecting=false;
    }
    private void ShowPage(string next)
    {
        page=next; TitleText.Text=next; editor=null; entityList=null; entityEditor=null; capabilityText=null; inputs.Clear(); vm.Editor.Discard();
        PageHost.Content=next switch { "模型庫"=>BuildEntities("models"),"使用模式"=>BuildEntities("profiles"),"系統"=>BuildSystem(),"任務與紀錄"=>BuildTasks(),_=>BuildOverview() };
        UpdateFooter();
    }
    private static TextBlock Text(string text, double size=14) => new() { Text=text, FontSize=size, TextWrapping=TextWrapping.Wrap, Margin=new Thickness(0,0,0,8) };
    private static StackPanel Panel() => new() { Spacing=12 };
    private static ScrollViewer Scroll(UIElement content) => new() { Content=content, VerticalScrollBarVisibility=ScrollBarVisibility.Auto, HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled };
    private Button Action(string label, Func<Task> action)
    {
        var button=new Button { Content=label, Margin=new Thickness(0,0,8,4) };
        button.Command=new AsyncCommand(() => Run(action), Error);
        return button;
    }
    private static StackPanel Row(params UIElement[] controls) { var row=new StackPanel { Orientation=Orientation.Horizontal, Spacing=8 }; foreach (var control in controls) row.Children.Add(control); return row; }
    private UIElement BuildOverview()
    {
        var panel=Panel();
        panel.Children.Add(Text("收到推理請求後，管理器會載入指定模型。",18));
        panel.Children.Add(Text("模型狀態："+J.S(vm.Status,"state","unloaded")+"\n模型："+J.S(vm.Status,"model_name","尚未載入")+"\n"+vm.ApplicationState,18));
        panel.Children.Add(Text("執行中："+J.I(vm.Status,"active_count")+"　等待中："+J.I(vm.Status,"queued_count")));
        var resource=vm.Status["resources"];
        panel.Children.Add(Text("RAM："+J.Metric(resource,"ram_used_gb"," GB")+" / "+J.Metric(resource,"ram_total_gb"," GB")+"\nGPU："+J.Metric(resource,"gpu_used_mib"," MiB")+" / "+J.Metric(resource,"gpu_total_mib"," MiB")));
        panel.Children.Add(Row(Action("載入預設模型",LoadDefault),Action("卸載模型",Unload),Action("暫停／恢復接收",ToggleAccepting),Action("保持載入／恢復卸載",ToggleKeep)));
        panel.Children.Add(Text("API："+J.S(vm.Status,"api_url")+"\n引擎版本："+J.S(vm.Status,"engine_version","未取得")));
        if (!string.IsNullOrEmpty(J.S(vm.Status,"last_error"))) panel.Children.Add(Text(J.S(vm.Status,"last_error")));
        return Scroll(panel);
    }
    private UIElement BuildTasks()
    {
        var panel=Panel(); var list=new ListView { MaxHeight=330, SelectionMode=ListViewSelectionMode.Single };
        foreach(var request in requests) list.Items.Add(new ListViewItem { Tag=J.S(request,"id"),Content=J.S(request,"phase")+" · "+J.S(request,"profile_name")+" · "+J.S(request,"elapsed_seconds")+" 秒" });
        var detail=new TextBox { IsReadOnly=true, AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,MinHeight=220 };
        list.SelectionChanged += (_,_)=> { selectedTask=(list.SelectedItem as ListViewItem)?.Tag?.ToString(); var request=requests.FirstOrDefault(x=>J.S(x,"id")==selectedTask); detail.Text=request is null ? "" : "狀態："+J.S(request,"phase")+"\n模型："+J.S(request,"model_name")+"\n使用模式："+J.S(request,"profile_name")+"\n耗時："+J.Metric(request,"elapsed_seconds"," 秒")+"\n首 token："+J.Metric(request,"first_token_seconds"," 秒")+"\n輸入："+J.Metric(request,"prompt_tokens"," tokens","0")+"\n生成："+J.Metric(request,"generated_tokens"," tokens","0")+"\n速度："+J.Metric(request,"generation_tps"," tok/s")+"\n思考預算："+J.Metric(request,"thinking_budget"," tokens","0")+"\n決策："+J.S(request,"decision")+"\n錯誤："+J.S(request,"error"); };
        list.SelectedItem=list.Items.Cast<ListViewItem>().FirstOrDefault(x=>x.Tag?.ToString()==selectedTask) ?? list.Items.Cast<ListViewItem>().FirstOrDefault();
        panel.Children.Add(list); panel.Children.Add(Row(Action("停止選取任務",async ()=> { if(selectedTask is not null) await api!.Post("/manager/requests/"+Uri.EscapeDataString(selectedTask)+"/cancel"); await Poll(); }),Action("清除紀錄",async ()=> { if(await Confirm("清除已完成的任務與服務紀錄？")) await api!.Post("/manager/records/clear"); await Poll(); })));
        panel.Children.Add(detail); panel.Children.Add(Text("服務紀錄")); panel.Children.Add(new TextBox { Text=serviceLogs,IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,MaxHeight=220 }); return Scroll(panel);
    }
    private async Task Poll()
    {
        if (api is null || polling || exiting) return;
        polling=true;
        try
        {
            var results=await Task.WhenAll(api.Get("/manager/status"),api.Get("/manager/requests"));
            vm.Status=results[0]; requests=J.A(results[1],"requests");
            if(page=="任務與紀錄")serviceLogs=string.Join("\n",J.A(await api.Get("/manager/logs"),"lines").Select(x=>x?.ToString()));
            tray?.Update("AMIEBL · "+J.S(vm.Status,"state")); UpdateFooter();
            if (!working && page=="總覽") PageHost.Content=BuildOverview();
            if (!working && page=="任務與紀錄") PageHost.Content=BuildTasks();
            if (!working && !vm.IsBusy)
            {
                await vm.RefreshConfig();
                if (capabilityText is not null && vm.SelectedModelId is not null) capabilityText.Text=ModelCapability(vm.SelectedModelId);
            }
        }
        catch(Exception ex) { Error(ex); }
        finally { polling=false; }
    }
    private void UpdateFooter() => Footer.Text=(vm.Editor.IsDirty ? "編輯中，尚未儲存　·　" : "")+vm.ApplicationState+"　·　http://127.0.0.1:"+host.Port;
    private async Task Exit()
    {
        if(!await LeaveEditor())return;
        if((J.I(vm.Status,"active_count")>0||J.I(vm.Status,"queued_count")>0)&&!await Confirm("完全結束會取消目前任務並停止此程式啟動的服務。"))return;
        await Shutdown();
    }
    private async Task Shutdown()
    {
        if(exiting)return;exiting=true;timer.Stop();tray?.Dispose();await host.DisposeAsync();api?.Dispose();instance?.Dispose();Close();
    }
    private async Task SmokeTest()
    {
        if(Option("--data-dir") is null)throw new InvalidOperationException("WinUI 測試必須指定隔離資料目錄。");
        var original=vm.Config.DeepClone().AsObject();var pages=new JsonArray();
        try
        {
            if(vm.Models.Count<2||vm.Profiles.Count<2)throw new InvalidOperationException("回歸測試需要至少兩個模型與模式。");
            ShowPage("模型庫");string id=editor!.Id;((TextBox)inputs["context"]).Text="512";await SaveEditor();
            entityList!.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)!=id);await Task.Delay(20);
            entityList.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)==id);await Task.Delay(20);
            if(((TextBox)inputs["context"]).Text!="512")throw new InvalidOperationException("模型切換後 Context 顯示錯誤。");
            ShowPage("使用模式");id=editor!.Id;((TextBox)inputs["thinking_budget"]).Text="64";await SaveEditor();
            entityList!.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)!=id);await Task.Delay(20);
            entityList.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)==id);await Task.Delay(20);
            if(((TextBox)inputs["thinking_budget"]).Text!="64")throw new InvalidOperationException("Profile 切換後預算顯示錯誤。");
            string screenshotDir=Path.Combine(host.DataDir,"winui-screenshots");Directory.CreateDirectory(screenshotDir);
            foreach(var next in new[]{"總覽","模型庫","使用模式","任務與紀錄","系統"})
            {
                ShowPage(next);Root.Measure(new Windows.Foundation.Size(1240,820));Root.Arrange(new Windows.Foundation.Rect(0,0,1240,820));Root.UpdateLayout();await Task.Delay(50);
                var bitmap=new RenderTargetBitmap();await bitmap.RenderAsync(Root);
                var pixels=await bitmap.GetPixelsAsync();using var stream=new InMemoryRandomAccessStream();
                var encoder=await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId,stream);
                encoder.SetPixelData(BitmapPixelFormat.Bgra8,BitmapAlphaMode.Premultiplied,(uint)bitmap.PixelWidth,(uint)bitmap.PixelHeight,96,96,pixels.ToArray());
                await encoder.FlushAsync();stream.Seek(0);using var reader=new DataReader(stream.GetInputStreamAt(0));
                await reader.LoadAsync((uint)stream.Size);var bytes=new byte[(int)stream.Size];reader.ReadBytes(bytes);await File.WriteAllBytesAsync(Path.Combine(screenshotDir,next+".png"),bytes);
                pages.Add(new JsonObject { ["page"]=next,["rendered"]=true,["width"]=bitmap.PixelWidth,["height"]=bitmap.PixelHeight });
            }
            await File.WriteAllTextAsync(Path.Combine(host.DataDir,"winui-smoke-test.json"),new JsonObject { ["ok"]=true,["editor_refresh_verified"]=true,["pages"]=pages,["model_loaded"]=J.S(vm.Status,"state")!="unloaded",["autostart_changed"]=false }.ToJsonString(new JsonSerializerOptions {WriteIndented=true}));
        }
        finally {vm.Config=await api!.Put("/manager/config",original);vm.Editor.Discard();}
    }
}
