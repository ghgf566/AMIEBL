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
Console.WriteLine("PASS: row refresh, unknown fields, failed save, saved/refresh failure, serialized saves, edit revision.");

sealed class FakeApi : IManagerApi
{
    private JsonObject config = JsonNode.Parse("""{"future_setting":"keep","models":[{"id":"a","name":"A","context":2048,"path":"fixture"}],"profiles":[]}""")!.AsObject();
    public bool FailSave, FailStatus;
    private int writes;
    public int MaxWrites;
    public Task<JsonObject> Get(string path) => path.EndsWith("status") ? FailStatus ? throw new IOException("fixture offline") : Task.FromResult(new JsonObject()) : Task.FromResult(config.DeepClone().AsObject());
    public async Task<JsonObject> Put(string path, JsonObject body)
    {
        if (FailSave) throw new InvalidOperationException("fixture rejected");
        writes++; MaxWrites = Math.Max(MaxWrites, writes);
        try { await Task.Delay(20); config = body.DeepClone().AsObject(); return config.DeepClone().AsObject(); }
        finally { writes--; }
    }
    public Task<JsonObject> Post(string path, JsonObject? body = null) => Task.FromResult(new JsonObject());
}
