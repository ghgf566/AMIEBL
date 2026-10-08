using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;

namespace LocalModelManager;

public abstract class ObservableObject : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    protected void Changed([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
}

// A typed projection with a lossless JSON payload for schema upgrades and
// extensions. Always resolve a row by ID, never keep a mutable server object.
public sealed record ModelSettings(string Id, string Name, int Context, string Path, JsonObject Data)
{
    public static ModelSettings From(JsonObject data) => new(J.S(data, "id"), J.S(data, "name"), J.I(data, "context"), J.S(data, "path"), data.DeepClone().AsObject());
}
public sealed record ProfileSettings(string Id, string Name, string ThinkingMode, string BudgetMode, int ThinkingBudget, JsonObject Data)
{
    public static ProfileSettings From(JsonObject data) => new(J.S(data, "id"), J.S(data, "name"), J.S(data, "thinking_mode"), J.S(data, "budget_mode"), J.I(data, "thinking_budget"), data.DeepClone().AsObject());
}

public sealed class EditorState : ObservableObject
{
    private bool dirty;
    private long revision;
    public bool IsDirty { get => dirty; set { if (value) revision++; if (dirty != value) { dirty = value; Changed(); } } }
    public long Revision => revision;
    public Func<Task>? Save { get; set; }
    public void Saved(long savedRevision) { if (revision == savedRevision) IsDirty = false; }
    public void Discard() { revision++; IsDirty = false; Save = null; }
}

public interface IManagerApi
{
    Task<JsonObject> Get(string path);
    Task<JsonObject> Put(string path, JsonObject body);
    Task<JsonObject> Post(string path, JsonObject? body = null);
}

public sealed class WorkspaceViewModel : ObservableObject
{
    private readonly SemaphoreSlim mutations = new(1, 1);
    private JsonObject config = new(), status = new();
    private bool busy;
    private long configRevision;
    public IManagerApi? Api { get; set; }
    public EditorState Editor { get; } = new();
    public ObservableCollection<ModelSettings> Models { get; } = new();
    public ObservableCollection<ProfileSettings> Profiles { get; } = new();
    public JsonObject Config { get => config; set { configRevision++; config = value.DeepClone().AsObject(); UpdateRows(); Changed(); } }
    public JsonObject Status { get => status; set { status = value.DeepClone().AsObject(); Changed(); Changed(nameof(ApplicationState)); } }
    public bool IsBusy { get => busy; private set { busy = value; Changed(); } }
    public string ApplicationState => J.B(Status, "pending_restart") ? "已儲存，等待管理器重啟" : J.B(Status, "pending_model_reload") ? "已儲存，等待模型重新載入" : "已儲存；引擎載入設定無待處理差異";
    public string? SelectedModelId { get; set; }
    public string? SelectedProfileId { get; set; }
    public ModelSettings? Model(string id) => Models.FirstOrDefault(m => m.Id == id);
    public ProfileSettings? Profile(string id) => Profiles.FirstOrDefault(p => p.Id == id);
    private void UpdateRows()
    {
        Synchronize(Models, J.A(config, "models").OfType<JsonObject>().Select(ModelSettings.From).ToArray());
        Synchronize(Profiles, J.A(config, "profiles").OfType<JsonObject>().Select(ProfileSettings.From).ToArray());
    }
    private static void Synchronize<T>(ObservableCollection<T> rows, IReadOnlyList<T> next)
    {
        for (int i = 0; i < next.Count; i++) { if (i < rows.Count) rows[i] = next[i]; else rows.Add(next[i]); }
        while (rows.Count > next.Count) rows.RemoveAt(rows.Count - 1);
    }
    public async Task RefreshConfig()
    {
        if (Api is null || IsBusy) return;
        long revision=configRevision;
        var refreshed=await Api.Get("/manager/config");
        if (!IsBusy && configRevision==revision) Config=refreshed;
    }
    public async Task SaveConfig(Action<JsonObject> edit)
    {
        var api = Api ?? throw new InvalidOperationException("背景服務尚未連線。");
        await mutations.WaitAsync();
        IsBusy = true;
        long revision = Editor.Revision;
        try
        {
            var latest = await api.Get("/manager/config");
            edit(latest);
            // PUT returns validated/persisted values. Publish those even when
            // a later status read fails: persistence and refresh are distinct.
            Config = await api.Put("/manager/config", latest);
            Editor.Saved(revision);
            try { Status = await api.Get("/manager/status"); }
            catch (Exception ex) { throw new SavedButRefreshFailedException(ex); }
        }
        finally { IsBusy = false; mutations.Release(); }
    }
}
public sealed class SavedButRefreshFailedException(Exception inner) : Exception("設定已儲存，但暫時無法刷新服務狀態。請稍後重新整理。", inner);
