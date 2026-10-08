using System.Text.Json.Nodes;
using LocalModelManager;

static void Check(bool value, string message) { if (!value) throw new Exception(message); }
var fake = new FakeApi();
var vm = new WorkspaceViewModel { Api = fake, Config = await fake.Get("/manager/config") };
var rows = vm.Models;
vm.Editor.IsDirty = true;
await vm.SaveConfig(c => c["models"]![0]!["context"] = 4096);
Check(vm.Model("a")!.Context == 4096 && ReferenceEquals(rows, vm.Models), "Observable rows did not refresh");
Check(J.S(vm.Config, "future_setting") == "keep" && !vm.Editor.IsDirty, "Unknown field or dirty state lost");
fake.FailSave = true; vm.Editor.IsDirty = true;
try { await vm.SaveConfig(c => c["models"]![0]!["context"] = 8192); throw new Exception("Failed save accepted"); }
catch (InvalidOperationException) { }
Check(vm.Model("a")!.Context == 4096 && vm.Editor.IsDirty && !vm.IsBusy, "Failure discarded draft");
fake.FailSave = false; fake.FailStatus = true;
try { await vm.SaveConfig(c => c["models"]![0]!["context"] = 8192); throw new Exception("Refresh failure swallowed"); }
catch (SavedButRefreshFailedException) { }
Check(vm.Model("a")!.Context == 8192 && !vm.Editor.IsDirty, "Persisted value not published on status failure");
fake.FailStatus = false;
await Task.WhenAll(vm.SaveConfig(c => c["first"] = 1), vm.SaveConfig(c => c["second"] = 2));
Check(J.I(vm.Config, "first") == 1 && J.I(vm.Config, "second") == 2 && fake.MaxWrites == 1, "Concurrent saves lost updates");
vm.Editor.IsDirty = true; long revision = vm.Editor.Revision; vm.Editor.IsDirty = true; vm.Editor.Saved(revision);
Check(vm.Editor.IsDirty, "Save cleared edits made while request was pending");
var draft = new SettingsEditorViewModel(vm, "models", vm.Model("a")!.Data,
    new[] { new FieldSpec("name", "Name"), new FieldSpec("context", "Context", FieldKind.Integer, Minimum:512, Maximum:2097152) });
draft.Field("context").Value = "4096";
fake.External(c => c["models"]![0]!["name"] = "External name");
await draft.Save();
Check(vm.Model("a")!.Name == "External name" && vm.Model("a")!.Context == 4096, "Draft overwrote untouched external setting");
draft = new SettingsEditorViewModel(vm, "models", vm.Model("a")!.Data,
    new[] { new FieldSpec("context", "Context", FieldKind.Integer, Minimum:512, Maximum:2097152) });
draft.Field("context").Value = "2048";
fake.External(c => c["models"]![0]!["context"] = 1024);
try { await draft.Save(); throw new Exception("External edit conflict silently overwritten"); }
catch (InvalidOperationException) { }
Check(vm.Editor.IsDirty, "Conflict discarded draft");
var arrayField = new SettingFieldViewModel(new("agent_tools", "Tools"), JsonNode.Parse("""{"agent_tools":["read","edit"]}""")!.AsObject());
Check(arrayField.Value == "read,edit", "Array editor displayed raw JSON");
fake.DelayNextGet = true;
var staleRefresh = vm.RefreshConfig();
await vm.SaveConfig(c => c["models"]![0]!["name"] = "Fresh saved name");
fake.PendingRead!.SetResult(fake.OldReadSnapshot!);
await staleRefresh;
Check(vm.Model("a")!.Name == "Fresh saved name", "Late refresh overwrote newer save");
Console.WriteLine("PASS: row refresh, unknown fields, failed save, saved/refresh failure, serialized saves, edit revision, external merge/conflict, array editor, stale refresh.");

sealed class FakeApi : IManagerApi
{
    private JsonObject config = JsonNode.Parse("""{"future_setting":"keep","models":[{"id":"a","name":"A","context":2048,"path":"fixture"}],"profiles":[]}""")!.AsObject();
    public void External(Action<JsonObject> edit) => edit(config);
    public bool FailSave, FailStatus;
    private int writes;
    public int MaxWrites;
    public bool DelayNextGet;
    public TaskCompletionSource<JsonObject>? PendingRead;
    public JsonObject? OldReadSnapshot;
    public Task<JsonObject> Get(string path)
    {
        if (path.EndsWith("status")) return FailStatus ? throw new IOException("fixture offline") : Task.FromResult(new JsonObject());
        if (DelayNextGet) { DelayNextGet=false; OldReadSnapshot=config.DeepClone().AsObject(); PendingRead=new(); return PendingRead.Task; }
        return Task.FromResult(config.DeepClone().AsObject());
    }
    public async Task<JsonObject> Put(string path, JsonObject body)
    {
        if (FailSave) throw new InvalidOperationException("fixture rejected");
        writes++; MaxWrites = Math.Max(MaxWrites, writes);
        try { await Task.Delay(20); config = body.DeepClone().AsObject(); return config.DeepClone().AsObject(); }
        finally { writes--; }
    }
    public Task<JsonObject> Post(string path, JsonObject? body = null) => Task.FromResult(new JsonObject());
}
