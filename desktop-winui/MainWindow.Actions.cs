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
    private async void ExitClicked(object sender,RoutedEventArgs args)=>await Run(Exit);
    private static string ConnectionSummary(JsonObject result)
    {
        return (J.B(result,"ok")?"連線檢查通過":"連線檢查發現問題")+"\nAPI："+J.S(result,"api_url")+
            "\nllama.cpp："+(J.B(result,"engine_exists")?"已找到":"找不到引擎，請確認系統頁的引擎資料夾")+
            "\nPython："+(J.B(result,"python_ok")?"可用":"不可用")+"\n引擎連接埠："+J.S(result,"port_status","未取得狀態")+
            "\n本機模型登錄："+J.A(result,"models").Count(x=>!J.S(x,"id").Contains("::"))+" 個";
    }
    private async Task CheckConnection()
    {
        var result=await api!.Get("/manager/connection");Notice.Severity=J.B(result,"ok")?InfoBarSeverity.Success:InfoBarSeverity.Warning;Notice.Message=ConnectionSummary(result);Notice.IsOpen=true;
    }
    private async Task LoadDefault() { await api!.Post("/manager/load",new JsonObject { ["model_id"]=J.S(vm.Config,"default_model_id") }); await Poll(); }
    private async Task Unload() { await api!.Post("/manager/unload"); await Poll(); }
    private async Task ToggleAccepting() { await api!.Post("/manager/accepting",new JsonObject { ["accepting"]=!J.B(vm.Status,"accepting",true) }); await Poll(); }
    private async Task ToggleKeep()
    {
        string id=J.S(vm.Status,"model_id",J.S(vm.Config,"default_model_id")); var model=vm.Model(id);
        await api!.Post("/manager/keep-loaded",new JsonObject { ["model_id"]=id,["keep_loaded"]=!J.B(model?.Data,"keep_loaded") }); vm.Config=await api.Get("/manager/config");
    }
    private async Task<string?> PickFile(string extension)
    {
        var picker=new FileOpenPicker(); InitializeWithWindow.Initialize(picker,Hwnd); picker.FileTypeFilter.Add(extension); return (await picker.PickSingleFileAsync())?.Path;
    }
    private async Task<string?> PickFolder()
    {
        var picker=new FolderPicker(); InitializeWithWindow.Initialize(picker,Hwnd); picker.FileTypeFilter.Add("*"); return (await picker.PickSingleFolderAsync())?.Path;
    }
    private async Task AddModel()
    {
        if(!await LeaveEditor())return; var path=await PickFile(".gguf"); if(path is null)return;
        await AddModelPath(path); ShowPage("模型庫");
    }
    private async Task AddModelPath(string path)
    {
        string id="model-"+Guid.NewGuid().ToString("N")[..8];
        var model=new JsonObject { ["id"]=id,["name"]=Path.GetFileNameWithoutExtension(path),["path"]=path,["context"]=32768,["gpu_layers"]=-1,["auto_fit"]=true,["fit_target_enabled"]=false,["mtp"]=false,["cache_type"]="q8_0" };
        await vm.SaveConfig(c=> { J.A(c,"models").Add(model); if(J.A(c,"models").Count==1)c["default_model_id"]=id; }); vm.SelectedModelId=id;
    }
    private async Task ScanModels()
    {
        if(!await LeaveEditor())return; var found=await api!.Post("/manager/scan");
        var choices=Panel(); var checks=new List<(CheckBox check,string path)>();
        foreach(var model in J.A(found,"models")) { string path=J.S(model,"path");var check=new CheckBox { Content=path }; choices.Children.Add(check);checks.Add((check,path)); }
        if(await new ContentDialog { XamlRoot=Root.XamlRoot,Title="加入掃描到的模型",Content=Scroll(choices),PrimaryButtonText="加入選取模型",CloseButtonText="取消" }.ShowAsync()!=ContentDialogResult.Primary)return;
        foreach(var (check,path) in checks)if(check.IsChecked==true)await AddModelPath(path); ShowPage("模型庫");
    }
    private async Task AddProfile()
    {
        if(!await LeaveEditor())return; string id="profile-"+Guid.NewGuid().ToString("N")[..8];
        await vm.SaveConfig(c=>J.A(c,"profiles").Add(new JsonObject { ["id"]=id,["name"]="新的使用模式",["thinking_mode"]="auto",["reasoning_level"]="balanced",["budget_mode"]="auto",["thinking_budget"]=1536,["max_tokens"]=8192 }));vm.SelectedProfileId=id;ShowPage("使用模式");
    }
    private async Task DuplicateProfile()
    {
        if(!await LeaveEditor())return; string? source=vm.SelectedProfileId;if(source is null)return;string id="profile-"+Guid.NewGuid().ToString("N")[..8];
        await vm.SaveConfig(c=> {var copy=J.A(c,"profiles").First(x=>J.S(x,"id")==source)!.DeepClone().AsObject();copy["id"]=id;copy["name"]=J.S(copy,"name")+" 副本";J.A(c,"profiles").Add(copy);});vm.SelectedProfileId=id;ShowPage("使用模式");
    }
    private async Task DeleteEntity()
    {
        var draft=editor;if(draft is null || !await Confirm("移除此登錄？模型檔案不會刪除。"))return;
        await vm.SaveConfig(c=>
        {
            var rows=J.A(c,draft.Collection);var target=rows.First(x=>J.S(x,"id")==draft.Id);
            if(draft.Collection=="profiles")
            {
                if(rows.Count==1)throw new InvalidOperationException("至少保留一個使用模式。");
                string fallback=J.S(rows.First(x=>J.S(x,"id")!=draft.Id),"id");
                if(J.S(c,"default_profile_id")==draft.Id)c["default_profile_id"]=fallback;
                foreach(var model in J.A(c,"models").OfType<JsonObject>())if(J.S(model,"default_profile_id")==draft.Id)model["default_profile_id"]=fallback;
            }
            rows.Remove(target);if(draft.Collection=="models"&&J.S(c,"default_model_id")==draft.Id)c["default_model_id"]=J.S(rows.FirstOrDefault(),"id");
        });ShowPage(page);
    }
    private async Task ConnectVSCode()
    {
        if(!await LeaveEditor())return;var preview=await api!.Get("/manager/vscode/preview");
        if(await Confirm(J.S(preview,"summary","更新 VS Code 設定並備份既有內容？"),"同步至 VS Code")){var result=await api.Post("/manager/vscode/apply");Message(J.S(result,"message","已更新，請重新載入 VS Code 視窗。"));}
    }
    private async Task Export()
    {
        if(!await LeaveEditor())return;var picker=new FileSavePicker { SuggestedFileName="AMIEBL-settings" };InitializeWithWindow.Initialize(picker,Hwnd);picker.FileTypeChoices.Add("JSON",new List<string> { ".json" });var file=await picker.PickSaveFileAsync();if(file is null)return;
        await File.WriteAllTextAsync(file.Path,(await api!.Get("/manager/export")).ToJsonString(new JsonSerializerOptions { WriteIndented=true }));Message("設定已匯出。");
    }
    private async Task Import()
    {
        if(!await LeaveEditor())return;string? path=await PickFile(".json");if(path is null)return;var imported=JsonNode.Parse(await File.ReadAllTextAsync(path))!.AsObject();
        if(!await Confirm("匯入會替換目前設定，後端會先備份。"+(J.B(imported,"auto_start")?"此設定會啟用登入自動啟動。":"")))return;
        bool prior=J.B(vm.Config,"auto_start");vm.Config=await api!.Post("/manager/import",imported);
        try{StartupService.Set(J.B(vm.Config,"auto_start"),host.DataDir);}catch{await vm.SaveConfig(c=>c["auto_start"]=prior);throw;}
        vm.Config=await api.Get("/manager/config");await Poll();ShowPage("系統");Message(vm.ApplicationState);
    }
}
