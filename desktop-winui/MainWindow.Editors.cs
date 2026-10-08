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

public sealed partial class MainWindow
{
    private UIElement BuildEntities(string collection)
    {
        var grid=new Grid { ColumnSpacing=24 };
        grid.ColumnDefinitions.Add(new() { Width=new GridLength(240) }); grid.ColumnDefinitions.Add(new() { Width=new GridLength(1,GridUnitType.Star) });
        var left=new Grid { RowSpacing=12 };foreach(var height in new[]{GridLength.Auto,new GridLength(1,GridUnitType.Star),GridLength.Auto,GridLength.Auto})left.RowDefinitions.Add(new(){Height=height});
        entityList=new ListView { Foreground=new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255,240,243,249)),SelectionMode=ListViewSelectionMode.Single, DisplayMemberPath="Name" };
        PopulateList(collection);
        var search=new TextBox { PlaceholderText="搜尋名稱／ID" };
        search.TextChanged += (_, _) =>
        {
            if (working) return;
            selecting=true;
            if (collection=="models") entityList.ItemsSource=ModelRows(search.Text);
            else entityList.ItemsSource=vm.Profiles.Where(x=>(x.Name+" "+x.Id).Contains(search.Text,StringComparison.OrdinalIgnoreCase)).ToArray();
            selecting=false;
        };
        left.Children.Add(search);Grid.SetRow(entityList,1);left.Children.Add(entityList);
        entityEditor=new ContentControl { HorizontalContentAlignment=HorizontalAlignment.Stretch };
        Grid.SetColumn(entityEditor,1); grid.Children.Add(left); grid.Children.Add(entityEditor);
        entityList.SelectionChanged += async (_, _) =>
        {
            if (selecting) return;
            string? id=EntityId(entityList.SelectedItem);
            if (id is null) return;
            if (working) { RestoreEntitySelection(collection); return; }
            await Run(async () =>
            {
                if (await LeaveEditor()) OpenEditor(collection,id);
                else RestoreEntitySelection(collection);
            });
        };
        if (collection=="models") {
            var actions=Row(Action("加入 GGUF",AddModel),Action("掃描",ScanModels));modelActionRow=actions;Grid.SetRow(actions,2);left.Children.Add(actions);
            var locations=BuildModelLocations();Grid.SetRow((FrameworkElement)locations,3);left.Children.Add(locations);
        }
        else {var actions=Row(Action("新增",AddProfile),Action("複製",DuplicateProfile));Grid.SetRow(actions,2);left.Children.Add(actions);}
        string? selected=collection=="models" ? vm.SelectedModelId : vm.SelectedProfileId;
        var first=J.A(vm.Config,collection).FirstOrDefault(x=>J.S(x,"id")==selected) ?? J.A(vm.Config,collection).FirstOrDefault();
        if (first is not null) { OpenEditor(collection,J.S(first,"id")); RestoreEntitySelection(collection); }
        else entityEditor.Content=Text("請先新增項目。",18);
        return grid;
    }
    private static string? EntityId(object? item) => item switch { ModelSettings m=>m.Id,ProfileSettings p=>p.Id,_=>null };
    private ModelSettings[] ModelRows(string search)=>vm.Models.Where(x=>(x.Name+" "+x.Id+" "+x.Path).Contains(search,StringComparison.OrdinalIgnoreCase)).Select(x=>x with {Name=x.Name+(x.Id==J.S(vm.Config,"default_model_id")?" · 預設":"")}).ToArray();
    private void PopulateList(string collection)
    {
        if (entityList is null) return;
        selecting=true; entityList.ItemsSource=collection=="models" ? ModelRows("") : vm.Profiles.Cast<object>().ToArray(); selecting=false;
    }
    private void RestoreEntitySelection(string collection)
    {
        if (entityList is null) return;
        string? id=collection=="models" ? vm.SelectedModelId : vm.SelectedProfileId;
        selecting=true; entityList.SelectedItem=entityList.Items.Cast<object>().FirstOrDefault(x=>EntityId(x)==id); selecting=false;
    }
    private void OpenEditor(string collection,string id)
    {
        if (collection=="models") vm.SelectedModelId=id; else vm.SelectedProfileId=id;
        var data=J.A(vm.Config,collection).OfType<JsonObject>().First(x=>J.S(x,"id")==id);
        editor=new(vm,collection,data,collection=="models" ? EditorSchemas.Model(vm.Profiles.Select(x=>x.Id).ToArray()) : EditorSchemas.Profile);
        if (entityEditor is not null) entityEditor.Content=BuildForm(editor);
    }
    private sealed record ChoiceOption(string Id,string Label);
    private ChoiceOption[] ChoiceOptions(FieldSpec spec)
    {
        var labels=spec.Key switch
        {
            "thinking_mode" => new Dictionary<string,string>{{"auto","自動判斷"},{"on","固定開啟"},{"off","固定關閉"},{"model","跟隨模型預設"}},
            "reasoning_level" => new Dictionary<string,string>{{"light","輕量"},{"balanced","均衡"},{"deep","深入"},{"extreme","極深"}},
            "budget_mode" => new Dictionary<string,string>{{"auto","自動"},{"custom","自訂（需引擎支援）"}},
            "mtp_source" => new Dictionary<string,string>{{"native","模型內建"},{"external","外部 Draft"}},
            "agent_sync_mode" => new Dictionary<string,string>{{"preserve","保留手動工具與指令"},{"managed","由 AMIEBL 管理"}},
            "default_profile_id" => vm.Profiles.ToDictionary(x=>x.Id,x=>x.Name),
            _ => new Dictionary<string,string>()
        };
        return (spec.Options ?? []).Select(id=>new ChoiceOption(id,labels.GetValueOrDefault(id,id))).ToArray();
    }
    private UIElement BuildForm(SettingsEditorViewModel draft)
    {
        inputs.Clear(); sliders.Clear(); editorExpanders.Clear(); var panel=Panel();
        if(draft.Collection=="system") {panel.Children.Add(Action("檢查連線",CheckConnection));panel.Children.Add(Text("資料位置："+host.DataDir,12));}
        var sections=new Dictionary<string,StackPanel>();
        StackPanel Section(string title)
        {
            if(sections.TryGetValue(title,out var existing))return existing;
            var content=Panel(); sections[title]=content;
            if(title is "進階採樣" or "VS Code Agent")
            {
                var expander=new SmoothExpander { Header=title,Content=content,HorizontalAlignment=HorizontalAlignment.Stretch,HorizontalContentAlignment=HorizontalAlignment.Stretch };
                AttachEditorReveal(expander);panel.Children.Add(expander);editorExpanders.Add(expander);
            }
            else panel.Children.Add(Card(title,content));
            return content;
        }
        if(draft.Collection=="models")
        {
            capabilityText=Text(ModelCapability(draft.Id));
            panel.Children.Add(Card("Thinking 支援 · 自動偵測",capabilityText));
        }
        panel.Children.Add(Text(draft.Collection=="models" ? "載入參數需重新載入；採樣與預設模式供後續請求使用。" : draft.Collection=="profiles" ? "客戶端明確 reasoning 設定優先；自訂上限需引擎辨識思考結束標記。" : "連線埠與啟動設定可能需重啟才能生效。"));
        if (draft.Collection!="system") panel.Children.Add(Text("識別："+draft.Id));
        var blocks=new List<(FieldSpec spec,UIElement block)>();
        foreach (var field in draft.Fields)
        {
            var block=Panel(); Control control;
            if (field.Spec.Kind==FieldKind.Boolean)
            {
                var check=new CheckBox { Content=field.Spec.Label, DataContext=field };
                check.SetBinding(CheckBox.IsCheckedProperty,new Binding { Path=new PropertyPath(nameof(field.Checked)),Mode=BindingMode.TwoWay }); control=check;
            }
            else if (field.Spec.Kind==FieldKind.Choice)
            {
                var choice=new ComboBox { Header=field.Spec.Label, ItemsSource=ChoiceOptions(field.Spec), DisplayMemberPath="Label", SelectedValuePath="Id", HorizontalAlignment=HorizontalAlignment.Stretch, DataContext=field };
                choice.SetBinding(ComboBox.SelectedValueProperty,new Binding { Path=new PropertyPath(nameof(field.Value)),Mode=BindingMode.TwoWay }); control=choice;
            }
            else
            {
                var text=new TextBox { Header=field.Spec.Label, DataContext=field, AcceptsReturn=field.Spec.Kind==FieldKind.Multiline, TextWrapping=TextWrapping.Wrap, PlaceholderText=field.Spec.Optional ? "空白 = 自動／預設" : "" };
                text.SetBinding(TextBox.TextProperty,new Binding { Path=new PropertyPath(nameof(field.Value)),Mode=BindingMode.TwoWay,UpdateSourceTrigger=UpdateSourceTrigger.PropertyChanged }); control=text;
            }
            inputs[field.Spec.Key]=control; block.Children.Add(control);
            if (field.Spec.Key is "path" or "mmproj" or "mtp_draft_path") block.Children.Add(Action("選擇 GGUF",async ()=> { var path=await PickFile(".gguf"); if(path is not null)field.Value=path; }));
            if (field.Spec.Key=="engine_dir") block.Children.Add(Action("選擇資料夾",async ()=> { var path=await PickFolder(); if(path is not null)field.Value=path; }));
            if(field.Spec.Key is "context" or "cpu_threads" or "thinking_budget" or "max_tokens") AddSlider(block,field,draft);
            var hint=FieldHelp(field.Spec.Key,draft.Collection);
            if(!string.IsNullOrEmpty(hint)) { var help=Text(hint,12);help.Opacity=0.72;block.Children.Add(help); }
            Section(FieldSection(field.Spec.Key,draft.Collection)).Children.Add(block); blocks.Add((field.Spec,block));
        }
        void Dependencies()
        {
            var preview=draft.Preview;
            foreach (var (spec,block) in blocks) if (spec.Enabled is not null) ((FrameworkElement)block).Visibility=spec.Enabled(preview) ? Visibility.Visible : Visibility.Collapsed;
        }
        draft.PropertyChanged += (_, e)=> { if(e.PropertyName==nameof(draft.Preview)) Dependencies(); }; Dependencies();
        var saveActions=Row(Action("儲存設定",SaveEditor),Action("重新讀取",async ()=>
        {
            if(vm.Editor.IsDirty && !await Confirm("捨棄尚未儲存的修改，重新讀取已保存設定？"))return;
            await vm.RefreshConfig();
            if(draft.Collection=="system")ShowPage("系統");
            else {PopulateList(draft.Collection);OpenEditor(draft.Collection,draft.Id);RestoreEntitySelection(draft.Collection);}
        }));
        if (draft.Collection=="models")
        {
            panel.Children.Add(Row(Action("載入／重新載入",async ()=> { await SaveEditor(); await api!.Post("/manager/load",new JsonObject { ["model_id"]=draft.Id }); await Poll(); }),Action("設為預設",async ()=> { await SaveEditor(); await vm.SaveConfig(c=>c["default_model_id"]=draft.Id); PopulateList("models");RestoreEntitySelection("models");Message("已設為預設模型。"); }),Action("移除登錄",DeleteEntity)));

        }
        else if (draft.Collection=="profiles") panel.Children.Add(Action("刪除此模式",DeleteEntity));
        if(draft.Collection=="system")panel.Children.Add(Card("設定備份與還原",Row(Action("匯出設定",Export),Action("匯入設定",Import))));
        var layout=new Grid { RowSpacing=12 };layout.RowDefinitions.Add(new(){Height=new GridLength(1,GridUnitType.Star)});layout.RowDefinitions.Add(new(){Height=GridLength.Auto});
        editorScroll=Scroll(panel);
        editorScroll.PointerWheelChanged+=(_,_)=>revealExpander=null;
        editorScroll.PointerPressed+=(_,_)=>revealExpander=null;
        layout.Children.Add(editorScroll);
        editorActions=saveActions;Grid.SetRow(saveActions,1);layout.Children.Add(saveActions);return layout;
    }
    private string ModelCapability(string id)
    {
        var model=vm.Model(id)?.Data;
        string capability=J.S(model,"reasoning_capability","unknown");
        string supported=capability switch { "toggle"=>"可切換 Thinking / Non-Thinking", "always"=>"具有思考能力；模板未提供可靠的關閉方式", "none"=>"未偵測到可控制的思考能力", _=>"尚未確認思考能力" };
        var efforts=J.A(model,"reasoning_efforts");
        return supported+"\n原生 Effort："+(efforts.Count>0?string.Join(" / ",efforts):"未提供")+
            "\n模型預設 Effort："+(string.IsNullOrWhiteSpace(J.S(model,"reasoning_default_effort"))?"未提供":J.S(model,"reasoning_default_effort"))+
            "\n"+(J.B(model,"reasoning_budget_supported")?"模板含思考標記，可嘗試自動 Budget。":"尚未確認自動 Budget；自訂上限仍會傳給引擎。")+
            "\n原生 Effort 控制思考深度；自訂 token 預算控制上限。是否能截斷仍取決於 llama.cpp parser，傳送參數不代表已生效。"+
            "\n偵測來源："+(J.S(model,"reasoning_detection")=="runtime"?"llama.cpp /props 驗證":J.S(model,"reasoning_detection")=="gguf"?"GGUF Chat Template":"尚未完成偵測")+
            "\n模板開關："+(J.A(model,"reasoning_toggle_keys").Count>0?string.Join(" / ",J.A(model,"reasoning_toggle_keys")):"未提供")+
            "\n原生 Context："+(J.I(model,"native_context")>0?J.S(model,"native_context"):"未知")+" · MTP："+J.S(model,"mtp_capability","未知")+
            "\n已儲存 Context："+J.S(model,"context")+" · 引擎使用中："+(J.S(vm.Status,"model_id")==id?J.S(vm.Status["loaded_model_settings"],"context","尚未載入"):"此模型未載入");
    }
    private async Task SaveEditor()
    {
        var draft=editor ?? throw new InvalidOperationException("沒有可儲存的編輯器。");
        bool oldStartup=J.B(vm.Config,"auto_start");
        var requested=draft.Preview;
        string? refreshWarning=null;
        try { await draft.Save(); }
        catch (SavedButRefreshFailedException ex) { refreshWarning=ex.Message; }
        var persisted=draft.Collection=="system" ? vm.Config : J.A(vm.Config,draft.Collection).First(x=>J.S(x,"id")==draft.Id);
        var normalized=draft.Fields.Where(f=>f.Spec.Key is not "agent_tools" and not "model_dirs" && !JsonNode.DeepEquals(requested[f.Spec.Key],persisted?[f.Spec.Key])).Select(f=>f.Spec.Label).ToArray();
        if (draft.Collection=="system")
        {
            try { StartupService.Set(J.B(vm.Config,"auto_start"),host.DataDir); }
            catch { await vm.SaveConfig(c=>c["auto_start"]=oldStartup); throw; }
            editor=new(vm,"system",vm.Config,EditorSchemas.System(vm.Profiles.Select(x=>x.Id).ToArray()));
            ShowPage("系統");
        }
        else { PopulateList(draft.Collection); OpenEditor(draft.Collection,draft.Id); RestoreEntitySelection(draft.Collection); }
        Message(refreshWarning ?? (vm.ApplicationState+(normalized.Length>0 ? "。後端調整了："+string.Join("、",normalized)+"；已顯示實際儲存值。" : ""))); UpdateFooter();
    }
    private UIElement BuildSystem()
    {
        editor=new(vm,"system",vm.Config,EditorSchemas.System(vm.Profiles.Select(x=>x.Id).ToArray()));
        return BuildForm(editor);
    }
}
