using System.Globalization;
using System.Diagnostics;
using Microsoft.UI.Xaml.Automation;
using System.Text.Json.Nodes;
using LocalModelManager;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;

namespace AMIEBL.WinUI;

public sealed partial class MainWindow
{
    private bool indicatorInitialized;
    private readonly Windows.UI.ViewManagement.UISettings motionSettings=new();
    private void UpdateNavigationIndicator()
    {
        if(Navigation.SelectedItem is not NavigationViewItem item||item.ActualHeight<=0)return;
        var point=item.TransformToVisual(Root).TransformPoint(new Windows.Foundation.Point(0,0));
        var visual=Microsoft.UI.Xaml.Hosting.ElementCompositionPreview.GetElementVisual(NavIndicator);
        if(!indicatorInitialized)
        {
            indicatorInitialized=true;
            visual.Offset=new System.Numerics.Vector3((float)point.X,(float)(point.Y+(item.ActualHeight-16)/2),0);
        }
        if(motionSettings.AnimationsEnabled && visual.ImplicitAnimations is null)
        {
            var animation=visual.Compositor.CreateVector3KeyFrameAnimation();animation.Target="Offset";animation.Duration=TimeSpan.FromMilliseconds(220);
            animation.InsertExpressionKeyFrame(0,"this.StartingValue");animation.InsertExpressionKeyFrame(1,"this.FinalValue");
            var animations=visual.Compositor.CreateImplicitAnimationCollection();animations["Offset"]=animation;visual.ImplicitAnimations=animations;
        }
        else if(!motionSettings.AnimationsEnabled) visual.ImplicitAnimations=null;
        NavIndicator.Opacity=1;
        visual.Offset=new System.Numerics.Vector3((float)point.X,(float)(point.Y+(item.ActualHeight-16)/2),0);
    }
    private readonly Dictionary<string,Slider> sliders=new();
    private static Border Card(string title, UIElement content)
    {
        var body=new Grid { RowSpacing=12 };body.RowDefinitions.Add(new(){Height=GridLength.Auto});body.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});body.Children.Add(Text(title,18));Grid.SetRow((FrameworkElement)content,1);body.Children.Add(content);
        return new Border { Child=body, Padding=new Thickness(20), CornerRadius=new CornerRadius(12), BorderThickness=new Thickness(1), BorderBrush=new SolidColorBrush(Windows.UI.Color.FromArgb(255,57,65,80)),Background=new SolidColorBrush(Windows.UI.Color.FromArgb(255,29,35,46)) };
    }
    private static string FieldSection(string key,string collection) => collection switch
    {
        "models"=>key switch
        {
            "name" or "path"=>"模型檔案",
            "context" or "cpu_threads" or "auto_fit" or "gpu_layers" or "fit_target_enabled" or "fit_target_mib" or "cache_type"=>"效能與記憶體",
            "mtp" or "mtp_source" or "mtp_draft_path" or "mtp_draft_max" or "vision" or "mmproj"=>"MTP 與視覺",
            "keep_loaded" or "idle_minutes" or "default_profile_id"=>"模型使用方式",
            _=>"進階採樣"
        },
        "profiles"=>key switch { "name"=>"使用模式", "thinking_mode" or "reasoning_level" or "budget_mode" or "thinking_budget" or "max_tokens"=>"思考策略與生成上限", _=>"VS Code Agent" },
        _=>key switch { "auto_start" or "start_hidden" or "close_to_tray" or "preload"=>"啟動與背景執行", "idle_minutes" or "default_profile_id"=>"預設模型行為", "log_request_bodies" or "log_retention_days"=>"紀錄與除錯", _=>"引擎與連線" }
    };
    private static string FieldHelp(string key,string collection) => key switch
    {
        "context"=>"可記住的輸入與生成總長度。越大通常占用越多 KV 記憶體；滑桿以倍增調整，也可輸入精確值。重新載入模型後生效。",
        "cpu_threads"=>"0 由引擎自動決定；滑桿依本機邏輯處理器數設定常用範圍，仍可輸入進階值。",
        "auto_fit"=>"載入時依 Context、KV Cache 與預留記憶體估算 GPU 層數。關閉後使用手動 GPU 層數。",
        "fit_target_enabled"=>"未自訂時沿用 llama.cpp 的預留記憶體預設值。視覺 projector 與外部 Draft 額外用量未包含於獨立估算。",
        "cache_type"=>"f16 精度較高；q8_0／q4_0 減少 KV 記憶體用量，實際支援依引擎與模型而定。",
        "mtp"=>"推測解碼可能加快生成；需模型內建 NextN／MTP 或相容的外部 Draft，並非每個 GGUF 都支援。",
        "vision"=>"需搭配相容的 projector GGUF；勾選並指定路徑後，重新載入才會套用。",
        "idle_minutes"=>"0 不自動卸載；模型欄位留白時使用系統預設。",
        "temperature"=>"控制抽樣隨機性；較低偏穩定，較高偏多樣。空白交由客戶端／引擎；明確填值會覆蓋請求採樣值，下次請求生效。",
        "top_p"=>"候選詞累積機率範圍（0～1）；較低更聚焦。留白使用客戶端／引擎預設。",
        "top_k"=>"只保留機率最高的 K 個候選詞；0 不設限。留白使用客戶端／引擎預設。",
        "min_p"=>"相對最高機率的最低門檻（0～1）；較高會排除更多低機率候選詞，0 不啟用。",
        "thinking_mode"=>"自動：先判斷任務是否需要思考；固定開啟／關閉仍受模型模板能力限制；跟隨模型預設保留模型原有策略。",
        "reasoning_level"=>"AMIEBL 將強度轉成模型實際支援的原生 Effort 或預算策略。各模型的控制方式與效果不同。",
        "budget_mode"=>"自動預算依偵測能力決定；自訂會送出明確 token 上限。請同時確認模型庫的 Thinking 支援說明。",
        "thinking_budget"=>"0 嘗試立即結束思考。上限能否真正截斷取決於 llama.cpp 的思考 parser；原生 Effort 可與此上限並用。",
        "max_tokens"=>"整次生成的 token 上限，包含思考與最終回答；與 Context 容量不同。",
        "name" when collection=="profiles"=>"此名稱也會同步成 VS Code Agent 的顯示名稱；改名後請從總覽按「同步至 VS Code」。",
        "agent_sync_mode"=>"保留模式保護手動工具與指令；管理模式由 AMIEBL 同步。同步至 VS Code 前仍會預覽變更。",
        "model_dirs"=>"掃描模型時搜尋的資料夾，也可從模型庫管理。取消登錄不會移動或刪除實體 GGUF。",
        "log_request_bodies"=>"開啟後可能記錄提示詞與請求內容，只建議需要除錯時使用。",
        _=>""
    };
    private void AddSlider(StackPanel block,SettingFieldViewModel setting,SettingsEditorViewModel draft)
    {
        bool logarithmic=setting.Spec.Key!="cpu_threads";
        double min=setting.Spec.Key=="context"?512:setting.Spec.Key=="max_tokens"?256:0;
        int nativeContext=J.I(vm.Model(draft.Id)?.Data,"native_context");
        double max=setting.Spec.Key switch { "context"=>nativeContext>0?Math.Clamp(nativeContext,512,2097152):2097152, "cpu_threads"=>Math.Max(1,Environment.ProcessorCount), _=>1048576 };
        double Position(double value)=>logarithmic?Math.Log2(Math.Max(1,value)):value;
        var slider=new Slider { Minimum=Position(min),Maximum=Position(max),StepFrequency=1,HorizontalAlignment=HorizontalAlignment.Stretch,Header="快速調整" };
        sliders[setting.Spec.Key]=slider;
        AutomationProperties.SetName(slider,setting.Spec.Label+"滑桿");
        bool syncing=false;
        void Sync()
        {
            if(!double.TryParse(setting.Value,NumberStyles.Float,CultureInfo.InvariantCulture,out var value))return;
            syncing=true;slider.Value=Math.Clamp(Position(value),slider.Minimum,slider.Maximum);syncing=false;
        }
        Sync();
        slider.ValueChanged+=(_,_)=> { if(!syncing)setting.Value=(logarithmic?(slider.Value==0&&min==0?0:Math.Round(Math.Pow(2,slider.Value))):Math.Round(slider.Value)).ToString(CultureInfo.InvariantCulture); };
        setting.PropertyChanged+=(_,e)=> { if(e.PropertyName==nameof(setting.Value))Sync(); };
        block.Children.Add(slider);
    }
    private UIElement BuildModelLocations()
    {
        var content=Panel();content.Children.Add(Text("掃描來源；更改登錄不會移動或刪除 GGUF。",12));
        foreach(var node in J.A(vm.Config,"model_dirs"))
        {
            string path=node?.ToString()??"";content.Children.Add(Text(path,12));
            content.Children.Add(Row(Action("變更位置",async()=> { if(!await LeaveEditor())return;var replacement=await PickFolder();if(replacement is null)return;await SetModelLocation(path,replacement);ShowPage("模型庫"); }),Action("移除",async()=> { if(!await LeaveEditor())return;await SetModelLocation(path,null);ShowPage("模型庫"); })));
        }
        content.Children.Add(Action("＋ 新增模型資料夾",async()=> { if(!await LeaveEditor())return;var path=await PickFolder();if(path is null)return;await SetModelLocation(null,path);ShowPage("模型庫"); }));
        return new Expander { Header="模型存放位置",Content=content,IsExpanded=true,HorizontalAlignment=HorizontalAlignment.Stretch };
    }
    private Task SetModelLocation(string? previous,string? replacement)=>vm.SaveConfig(c=>
    {
        var dirs=J.A(c,"model_dirs");var found=dirs.FirstOrDefault(x=>string.Equals(x?.ToString(),previous,StringComparison.OrdinalIgnoreCase));
        if(previous is not null&&found is not null)dirs.Remove(found);
        if(replacement is not null&&!dirs.Any(x=>string.Equals(x?.ToString(),replacement,StringComparison.OrdinalIgnoreCase)))dirs.Add(replacement);
        c["model_dirs"]=dirs;
    });
    private ListView? taskList;
    private ContentControl? taskDetail;
    private TextBox? logBox;
    private UIElement BuildTasks()
    {
        var root=new Grid { RowSpacing=16 };root.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});root.RowDefinitions.Add(new(){Height=GridLength.Auto});
        var split=new Grid { ColumnSpacing=20 };split.ColumnDefinitions.Add(new(){Width=new GridLength(260)});split.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});
        taskList=new ListView { SelectionMode=ListViewSelectionMode.Single };taskList.SelectionChanged+=(_,_)=> { if(selecting)return;selectedTask=(taskList.SelectedItem as ListViewItem)?.Tag?.ToString();UpdateTaskDetail(); };
        var left=Card("最近任務",taskList);split.Children.Add(left);
        taskDetail=new ContentControl { HorizontalContentAlignment=HorizontalAlignment.Stretch };var detailScroll=Scroll(taskDetail);Grid.SetColumn(detailScroll,1);split.Children.Add(detailScroll);root.Children.Add(split);
        logBox=new TextBox { IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.NoWrap,Height=150,FontFamily=new FontFamily("Consolas") };
        var logs=Panel();logs.Children.Add(Text("預設只保留狀態與錯誤；完整請求記錄可在系統頁開啟。",12));logs.Children.Add(logBox);
        logs.Children.Add(Row(Action("複製紀錄",()=> { var data=new DataPackage();data.SetText(logBox.Text);Clipboard.SetContent(data);Message("紀錄已複製。");return Task.CompletedTask; }),Action("開啟紀錄資料夾",()=> { Process.Start(new ProcessStartInfo(host.DataDir){UseShellExecute=true});return Task.CompletedTask; }),Action("清除紀錄",async()=> { if(await Confirm("清除已完成的任務與服務紀錄？"))await api!.Post("/manager/records/clear");await Poll(); })));
        var expander=new Expander { Header="服務執行紀錄",Content=logs,HorizontalAlignment=HorizontalAlignment.Stretch };Grid.SetRow(expander,1);root.Children.Add(expander);UpdateTasks();return root;
    }
    private void UpdateTasks()
    {
        if(taskList is null)return;
        selecting=true;
        var ids=requests.Select(x=>J.S(x,"id")).ToArray();
        if(!taskList.Items.Cast<ListViewItem>().Select(x=>x.Tag?.ToString()).SequenceEqual(ids))
        {
            taskList.Items.Clear();foreach(var request in requests)taskList.Items.Add(new ListViewItem { Tag=J.S(request,"id") });
        }
        foreach(var item in taskList.Items.Cast<ListViewItem>()) {var request=requests.First(x=>J.S(x,"id")==item.Tag?.ToString());item.Content=Phase(J.S(request,"phase"))+"\n"+J.S(request,"profile_name")+" · "+J.Metric(request,"elapsed_seconds"," 秒");}
        taskList.SelectedItem=taskList.Items.Cast<ListViewItem>().FirstOrDefault(x=>x.Tag?.ToString()==selectedTask)??taskList.Items.Cast<ListViewItem>().FirstOrDefault();selectedTask=(taskList.SelectedItem as ListViewItem)?.Tag?.ToString();selecting=false;
        if(logBox is not null&&logBox.Text!=serviceLogs)logBox.Text=serviceLogs;UpdateTaskDetail();
    }
    private static string Phase(string phase)=>phase switch { "completed"=>"已完成", "cancelled"=>"已取消", "error"=>"錯誤", "queued"=>"等待中", "generating"=>"生成中", "thinking"=>"思考中", "processing"=>"處理上下文", _=>phase };
    private void UpdateTaskDetail()
    {
        if(taskDetail is null)return;var request=requests.FirstOrDefault(x=>J.S(x,"id")==selectedTask);var panel=Panel();
        if(request is null) {panel.Children.Add(Card("尚未收到任務",Text("從 VS Code 送出請求後，這裡會顯示上下文處理、思考與生成狀態。")));taskDetail.Content=panel;return;}
        panel.Children.Add(Card("任務狀態 · "+Phase(J.S(request,"phase")),Text("模型："+J.S(request,"model_name")+"\n使用模式："+J.S(request,"profile_name")+"\n耗時："+J.Metric(request,"elapsed_seconds"," 秒")+" · 首 token："+J.Metric(request,"first_token_seconds"," 秒"))));
        panel.Children.Add(Card("輸入與生成",Text("輸入："+J.Metric(request,"prompt_tokens"," tokens","0")+" · 快取："+J.Metric(request,"cached_tokens"," tokens","0")+"\n輸入處理："+J.Metric(request,"prompt_tps"," tok/s")+"\n生成："+J.Metric(request,"generated_tokens"," tokens","0")+" · 速度："+J.Metric(request,"generation_tps"," tok/s"))));
        panel.Children.Add(Card("本次思考策略",Text("決策："+J.S(request,"decision","未提供")+" · 判斷耗時："+J.Metric(request,"classifier_seconds"," 秒")+"\n強度："+J.S(request,"reasoning_level","未指定")+" · 原生 Effort："+J.S(request,"effort","未使用")+"\n思考預算："+J.Metric(request,"thinking_budget"," tokens","0")+" · 已用："+J.Metric(request,"thinking_tokens"," tokens","0")+"\n總生成上限："+J.Metric(request,"max_tokens"," tokens","0"))));
        if(!string.IsNullOrEmpty(J.S(request,"error")))panel.Children.Add(Card("錯誤原因",Text(J.S(request,"error"))));
        if(J.S(request,"phase")=="cancelled")panel.Children.Add(Text(J.B(request,"cancel_confirmed")?"已確認停止":"等待引擎確認停止"));
        var cancel=Action("停止選取任務",async()=> { if(selectedTask is null)return;await api!.Post("/manager/requests/"+Uri.EscapeDataString(selectedTask)+"/cancel");await Poll(); });cancel.IsEnabled=J.S(request,"phase") is not ("completed" or "cancelled" or "error");panel.Children.Add(cancel);
        panel.Children.Add(Text("請求識別："+J.S(request,"id"),12));taskDetail.Content=panel;
    }
}


