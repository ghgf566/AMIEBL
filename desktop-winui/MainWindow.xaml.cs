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
    private ScrollViewer? editorScroll;
    private FrameworkElement? editorActions;
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
        InitializeComponent(); VersionText.Text="v"+typeof(App).Assembly.GetName().Version!.ToString(3); Root.DataContext = vm; TitleText.Text="正在啟動"; PageHost.Content=Text("正在連接本機服務…",18);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1240,860));
        AppWindow.Closing += async (_, e) =>
        {
            if (exiting) return;
            e.Cancel = true;
            if (J.B(vm.Config,"close_to_tray",true) && tray is not null) AppWindow.Hide();
            else await Run(Exit);
        };
        timer.Tick += async (_, _) => await Poll();
        Root.SizeChanged+=(_,_)=>UpdateNavigationIndicator();
        Root.Loaded+=(_,_)=>UpdateNavigationIndicator();
        Navigation.PaneOpened+=(_,_)=>UpdateNavigationIndicator();
        Navigation.PaneClosed+=(_,_)=>UpdateNavigationIndicator();
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
        if (!vm.Editor.IsDirty) { ShowPage(next); return; }
        await Run(async () => { if (await LeaveEditor()) ShowPage(next); else RestoreNavigation(); });
    }
    private void RestoreNavigation()
    {
        selecting=true; Navigation.SelectedItem=Navigation.MenuItems.OfType<NavigationViewItem>().First(x => x.Tag?.ToString()==page); selecting=false;
    }
    private void ShowPage(string next)
    {
        revealExpander=null;editorExpanders.Clear();page=next; ExitButton.Visibility=next=="系統"?Visibility.Visible:Visibility.Collapsed; TitleText.Text=next; editor=null; entityList=null; entityEditor=null; editorScroll=null; editorActions=null; capabilityText=null; inputs.Clear(); vm.Editor.Discard();
        PageHost.Content=next switch { "模型庫"=>BuildEntities("models"),"使用模式"=>BuildEntities("profiles"),"系統"=>BuildSystem(),"任務與紀錄"=>BuildTasks(),_=>BuildOverview() };
        RestoreNavigation(); UpdateNavigationIndicator(); UpdateFooter();
    }
    private static TextBlock Text(string text, double size=14) => new() { Text=text, IsTextSelectionEnabled=true, Foreground=new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255,240,243,249)), FontSize=size, TextWrapping=TextWrapping.Wrap, Margin=new Thickness(0,0,0,8) };
    private static StackPanel Panel() => new() { Spacing=12 };
    private static ScrollViewer Scroll(UIElement content) => new() { Content=content, VerticalScrollBarVisibility=ScrollBarVisibility.Auto, HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled };
    private Button Action(string label, Func<Task> action)
    {
        var button=new Button { Content=label, Margin=new Thickness(0,0,8,4) };
        button.Command=new AsyncCommand(() => Run(action), Error);
        return button;
    }
    private static StackPanel Row(params UIElement[] controls) { var row=new StackPanel { Orientation=Orientation.Horizontal, Spacing=8 }; foreach (var control in controls) row.Children.Add(control); return row; }
    private ScrollViewer? overviewScroll;
    private TextBlock? overviewModel,overviewState,overviewQueue,overviewMemory,overviewConnection,overviewError;
    private Border? overviewErrorCard;
    private UIElement BuildOverview()
    {
        var panel=Panel();var state=Panel();
        overviewModel=Text("",20);overviewState=Text("");state.Children.Add(overviewModel);state.Children.Add(overviewState);
        state.Children.Add(Text("收到推理請求後，管理器會載入指定模型。",12));
        state.Children.Add(Row(Action("同步至 VS Code",ConnectVSCode),Action("載入預設模型",LoadDefault),Action("卸載模型",Unload)));panel.Children.Add(Card("模型與服務",state));
        var metrics=new Grid { ColumnSpacing=16 };metrics.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});metrics.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});
        overviewQueue=Text("",18);overviewMemory=Text("");metrics.Children.Add(Card("任務佇列",overviewQueue));var memory=Card("記憶體用量",overviewMemory);Grid.SetColumn(memory,1);metrics.Children.Add(memory);panel.Children.Add(metrics);
        panel.Children.Add(Card("服務控制",Row(Action("暫停／恢復接收",ToggleAccepting),Action("保持載入／恢復卸載",ToggleKeep))));
        var connection=Panel();overviewConnection=Text("");connection.Children.Add(overviewConnection);connection.Children.Add(Text("同步模型清單、模式名稱與 Agent 設定，並備份現有內容。",12));panel.Children.Add(Card("VS Code 整合",connection));
        overviewError=Text("");overviewErrorCard=Card("最近錯誤",overviewError);panel.Children.Add(overviewErrorCard);
        UpdateOverview();overviewScroll=Scroll(panel);return overviewScroll;
    }
    private void UpdateOverview()
    {
        if(overviewModel is null)return;
        overviewModel.Text="模型："+J.S(vm.Status,"model_name","尚未載入");overviewState!.Text="狀態："+Phase(J.S(vm.Status,"state","unloaded"))+" · "+vm.ApplicationState;
        overviewQueue!.Text="執行中："+J.I(vm.Status,"active_count")+"\n等待中："+J.I(vm.Status,"queued_count");
        var resource=vm.Status["resources"];overviewMemory!.Text="RAM："+J.Metric(resource,"ram_used_gb"," GB")+" / "+J.Metric(resource,"ram_total_gb"," GB")+"\nGPU："+J.Metric(resource,"gpu_used_mib"," MiB")+" / "+J.Metric(resource,"gpu_total_mib"," MiB");
        overviewConnection!.Text="API："+J.S(vm.Status,"api_url")+"\n引擎版本："+J.S(vm.Status,"engine_version","未取得");
        overviewError!.Text=J.S(vm.Status,"last_error");overviewErrorCard!.Visibility=string.IsNullOrEmpty(overviewError.Text)?Visibility.Collapsed:Visibility.Visible;
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
            if (!working && page=="總覽") UpdateOverview();
            if (!working && page=="任務與紀錄") UpdateTasks();
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
            ShowPage("模型庫");string id=editor!.Id;
            if(vm.Editor.IsDirty)throw new InvalidOperationException("初始化滑桿修改了草稿。");
            Root.UpdateLayout();await Task.Delay(50);
            if(FieldVisible(inputs["mtp_source"])||FieldVisible(inputs["mtp_draft_max"]))throw new InvalidOperationException("未開啟 MTP 時來源／tokens 未隱藏。");
            ((CheckBox)inputs["mtp"]).IsChecked=true;await Task.Delay(30);
            if(!FieldVisible(inputs["mtp_source"])||!FieldVisible(inputs["mtp_draft_max"]))throw new InvalidOperationException("開啟 MTP 時欄位未顯示。");
            ((CheckBox)inputs["mtp"]).IsChecked=false;await Task.Delay(30);
            if(FieldVisible(inputs["mtp_source"]))throw new InvalidOperationException("關閉 MTP 時來源未隱藏。");
            if(inputs.ContainsKey("keep_loaded")||inputs.ContainsKey("idle_minutes"))throw new InvalidOperationException("模型仍包含個別卸載控制。");
            if(modelLocations!.IsExpanded||!entityList!.Items.Cast<ModelSettings>().Any(x=>x.Id==J.S(vm.Config,"default_model_id")&&x.Name.Contains("預設")))throw new InvalidOperationException("位置卡片初始狀態或預設模型標記錯誤。");
            sliders["context"].Value=10;
            if(((TextBox)inputs["context"]).Text!="1024")throw new InvalidOperationException("Context 滑桿沒有同步數值。");
            ((TextBox)inputs["context"]).Text="768";
            if(Math.Abs(sliders["context"].Value-Math.Log2(768))>0.01)throw new InvalidOperationException("精確 Context 輸入沒有同步滑桿。");
            ((TextBox)inputs["context"]).Text="512";await SaveEditor();
            entityList!.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)!=id);await Task.Delay(20);
            entityList.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)==id);await Task.Delay(20);
            if(((TextBox)inputs["context"]).Text!="512")throw new InvalidOperationException("模型切換後 Context 顯示錯誤。");
            ShowPage("使用模式");id=editor!.Id;((TextBox)inputs["thinking_budget"]).Text="64";await SaveEditor();
            entityList!.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)!=id);await Task.Delay(20);
            entityList.SelectedItem=entityList.Items.Cast<object>().First(x=>EntityId(x)==id);await Task.Delay(20);
            if(((TextBox)inputs["thinking_budget"]).Text!="64")throw new InvalidOperationException("Profile 切換後預算顯示錯誤。");
            await SetModelLocation(null,Path.Combine(host.DataDir,"folder-regression"));
            if(!J.A(vm.Config,"model_dirs").Any(x=>x?.ToString()==Path.Combine(host.DataDir,"folder-regression")))throw new InvalidOperationException("模型位置新增失敗。");
            await SetModelLocation(Path.Combine(host.DataDir,"folder-regression"),Path.Combine(host.DataDir,"folder-replaced"));
            await SetModelLocation(Path.Combine(host.DataDir,"folder-replaced"),null);
            if(J.A(vm.Config,"model_dirs").Any(x=>x?.ToString()?.Contains("folder-")==true))throw new InvalidOperationException("模型位置變更／移除失敗。");
            ShowPage("總覽");Root.UpdateLayout();await Task.Delay(50);var overviewContent=PageHost.Content;
            overviewScroll!.ChangeView(null,Math.Min(30,overviewScroll.ScrollableHeight),null,true);await Task.Delay(30);double overviewOffset=overviewScroll.VerticalOffset;
            await Poll();Root.UpdateLayout();await Task.Delay(30);
            if(!ReferenceEquals(overviewContent,PageHost.Content)||Math.Abs(overviewScroll.VerticalOffset-overviewOffset)>1)throw new InvalidOperationException("總覽輪詢重設了捲動位置。");
            for(int i=0;i<15;i++) { Navigation.SelectedItem=Navigation.MenuItems[i%5];await Task.Delay(25); }
            if(page!="系統"||working||!Navigation.IsEnabled)throw new InvalidOperationException("快速切頁狀態不一致。");
            requests=new JsonArray(new JsonObject { ["id"]="ux-fixture",["phase"]="completed",["model_name"]="回歸測試模型",["profile_name"]="程式設計",["elapsed_seconds"]=2.5,["prompt_tokens"]=128,["generated_tokens"]=64,["generation_tps"]=25.6,["decision"]="on",["effort"]="medium",["thinking_budget"]=64 });
            serviceLogs="[測試資料] 任務完成；未啟動推理。";
            string screenshotDir=Path.Combine(host.DataDir,"winui-screenshots");Directory.CreateDirectory(screenshotDir);
            foreach(var next in new[]{"總覽","模型庫","模型庫效能","使用模式","思考預算","任務與紀錄","服務紀錄展開","模型位置展開","系統"})
            {
                ShowPage(next=="模型庫效能"?"模型庫":next=="思考預算"?"使用模式":next=="服務紀錄展開"?"任務與紀錄":next=="模型位置展開"?"模型庫":next);Root.Measure(new Windows.Foundation.Size(1240,820));Root.Arrange(new Windows.Foundation.Rect(0,0,1240,820));Root.UpdateLayout();
                if(next=="模型庫")await VerifyAnchoredExpander(modelLocations!);
                if(next=="任務與紀錄")await VerifyAnchoredExpander(serviceLogExpander!);
                if(next=="模型位置展開")modelLocations!.IsExpanded=true;
                if(next=="服務紀錄展開")serviceLogExpander!.IsExpanded=true;
                if(next=="系統") {await CheckConnection();Root.UpdateLayout();await Task.Delay(50);}
                if(ExitButton.Visibility!=(page=="系統"?Visibility.Visible:Visibility.Collapsed))throw new InvalidOperationException("完全結束出現在錯誤頁面。");
                if(next is "模型庫效能" or "思考預算")
                {
                    var fold=editorExpanders.Single();editorScroll!.ChangeView(null,Math.Max(0,editorScroll.ScrollableHeight-60),null,true);Root.UpdateLayout();await Task.Delay(40);
                    fold.IsExpanded=true;await Task.Delay(70);Root.UpdateLayout();
                    if(motionSettings.AnimationsEnabled&&fold.ContentHeight<=0)throw new InvalidOperationException("編輯卡片缺少中間動畫狀態。");
                    await Task.Delay(300);Root.UpdateLayout();
                    double relativeY=fold.TransformToVisual(editorScroll).TransformPoint(new Windows.Foundation.Point(0,0)).Y;
                    if(relativeY< -20||relativeY>editorScroll.ActualHeight-60)throw new InvalidOperationException("展開後沒有帶到設定內容。");
                    double expandedExtent=editorScroll.ExtentHeight;
                    fold.IsExpanded=false;await Task.Delay(70);Root.UpdateLayout();double middleExtent=editorScroll.ExtentHeight;
                    await Task.Delay(280);Root.UpdateLayout();
                    if(motionSettings.AnimationsEnabled&&expandedExtent-editorScroll.ExtentHeight>2&&(middleExtent>=expandedExtent-0.5||middleExtent<=editorScroll.ExtentHeight+0.5))throw new InvalidOperationException("收合時捲動範圍瞬間改變。");
                }
                if(next=="系統") {if(!VisualStateManager.GoToState(ExitButton,"PointerOver",false))throw new InvalidOperationException("完全結束的 hover 狀態不存在。");}
                double? footerY=editorActions?.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point(0,0)).Y;
                if(next is "模型庫效能" or "思考預算" or "系統" && editorScroll is ScrollViewer editScroll)editScroll.ChangeView(null,next=="模型庫效能"?600:next=="系統"?100000:460,null);
                UpdateNavigationIndicator();await Task.Delay(350);
                if(footerY is double before && editorActions is not null && (Math.Abs(editorActions.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point(0,0)).Y-before)>1 || before+editorActions.ActualHeight>Root.ActualHeight))throw new InvalidOperationException("儲存操作列未固定在可見區域。");
                var bitmap=new RenderTargetBitmap();await bitmap.RenderAsync(Root);
                var pixels=await bitmap.GetPixelsAsync();using var stream=new InMemoryRandomAccessStream();
                var encoder=await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId,stream);
                encoder.SetPixelData(BitmapPixelFormat.Bgra8,BitmapAlphaMode.Premultiplied,(uint)bitmap.PixelWidth,(uint)bitmap.PixelHeight,96,96,pixels.ToArray());
                await encoder.FlushAsync();stream.Seek(0);using var reader=new DataReader(stream.GetInputStreamAt(0));
                await reader.LoadAsync((uint)stream.Size);var bytes=new byte[(int)stream.Size];reader.ReadBytes(bytes);await File.WriteAllBytesAsync(Path.Combine(screenshotDir,next+".png"),bytes);
                pages.Add(new JsonObject { ["page"]=next,["rendered"]=true,["width"]=bitmap.PixelWidth,["height"]=bitmap.PixelHeight });
            }
            await File.WriteAllTextAsync(Path.Combine(host.DataDir,"winui-smoke-test.json"),new JsonObject { ["ok"]=true,["editor_refresh_verified"]=true,["layout_animation_verified"]=true,["overview_scroll_verified"]=true,["editor_reveal_verified"]=true,["exit_visibility_verified"]=true,["anchored_expanders_verified"]=true,["mtp_dependency_verified"]=true,["system_footer_verified"]=true,["fixed_footer_verified"]=true,["slider_sync_verified"]=true,["model_locations_verified"]=true,["rapid_navigation_verified"]=true,["pages"]=pages,["model_loaded"]=J.S(vm.Status,"state")!="unloaded",["autostart_changed"]=false }.ToJsonString(new JsonSerializerOptions {WriteIndented=true}));
        }
        finally {vm.Config=await api!.Put("/manager/config",original);vm.Editor.Discard();}
    }
}
