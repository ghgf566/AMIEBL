using System.Globalization;
using System.Text.Json.Nodes;
using System.Windows.Input;

namespace LocalModelManager;

public enum FieldKind { Text, Integer, Number, Boolean, Choice, Multiline }
public sealed record FieldSpec(string Key, string Label, FieldKind Kind = FieldKind.Text, bool Optional = false, double Minimum = 0, double Maximum = 1048576, string[]? Options = null, string? Hint = null, Func<JsonObject, bool>? Enabled = null);
public sealed class SettingFieldViewModel : ObservableObject
{
    private string value;
    public FieldSpec Spec { get; }
    public string Value { get => value; set { if (this.value != value) { this.value = value; Changed(); Changed(nameof(Checked)); Edited?.Invoke(); } } }
    public bool Checked { get => Value == "true"; set => Value = value ? "true" : "false"; }
    public event Action? Edited;
    public SettingFieldViewModel(FieldSpec spec, JsonObject source) { Spec = spec; value = source[spec.Key] is JsonArray array ? string.Join(spec.Key == "model_dirs" ? "\n" : ",", array.Select(x => x?.ToString() ?? "")) : source[spec.Key]?.ToString() ?? ""; }
    public JsonNode? Parse()
    {
        if (Spec.Optional && string.IsNullOrWhiteSpace(Value)) return null;
        switch (Spec.Kind)
        {
            case FieldKind.Boolean: return JsonValue.Create(Checked);
            case FieldKind.Integer:
                if (!int.TryParse(Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int integer) || integer < Spec.Minimum || integer > Spec.Maximum) throw Invalid();
                return JsonValue.Create(integer);
            case FieldKind.Number:
                if (!double.TryParse(Value, NumberStyles.Float, CultureInfo.InvariantCulture, out double number) || !double.IsFinite(number) || number < Spec.Minimum || number > Spec.Maximum) throw Invalid();
                return JsonValue.Create(number);
            case FieldKind.Choice:
                if (Spec.Options is { Length: > 0 } && !Spec.Options.Contains(Value)) throw Invalid();
                return JsonValue.Create(Value);
            default: return JsonValue.Create(Value.Trim());
        }
    }
    private InvalidOperationException Invalid() => new($"「{Spec.Label}」格式或範圍不正確（{Spec.Minimum}～{Spec.Maximum}）。");
}

public sealed class SettingsEditorViewModel : ObservableObject
{
    private readonly WorkspaceViewModel workspace;
    private JsonObject original;
    public string Collection { get; }
    public string Id { get; }
    public IReadOnlyList<SettingFieldViewModel> Fields { get; }
    public SettingsEditorViewModel(WorkspaceViewModel workspace, string collection, JsonObject source, IEnumerable<FieldSpec> specs)
    {
        this.workspace = workspace; Collection = collection; Id = J.S(source, "id"); original = source.DeepClone().AsObject();
        Fields = specs.Select(s => new SettingFieldViewModel(s, source)).ToArray();
        foreach (var settingField in Fields) settingField.Edited += () => { workspace.Editor.IsDirty = true; Changed(nameof(Preview)); };
        workspace.Editor.Discard(); workspace.Editor.Save = Save;
    }
    public JsonObject Preview
    {
        get
        {
            var data = original.DeepClone().AsObject();
            foreach (var settingField in Fields)
            {
                try { data[settingField.Spec.Key] = settingField.Parse(); }
                catch (InvalidOperationException) { data[settingField.Spec.Key] = settingField.Value; }
            }
            return data;
        }
    }
    public SettingFieldViewModel Field(string key) => Fields.First(f => f.Spec.Key == key);
    public async Task Save()
    {
        var changed = original.DeepClone().AsObject();
        foreach (var settingField in Fields) changed[settingField.Spec.Key] = settingField.Parse();
        if (Collection is "models" or "profiles" && string.IsNullOrWhiteSpace(J.S(changed, "name"))) throw new InvalidOperationException("名稱不可留白。");
        // List editing uses comma/newline text, but the API retains array types.
        foreach (var key in new[] { "agent_tools", "model_dirs" })
            if (changed[key] is JsonValue && Fields.Any(f => f.Spec.Key == key)) changed[key] = new JsonArray(J.S(changed, key).Split(key == "agent_tools" ? ',' : '\n', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).Select(x => (JsonNode?)JsonValue.Create(x)).ToArray());
        try
        {
        await workspace.SaveConfig(config =>
        {
            JsonObject target = Collection == "system" ? config : J.A(config, Collection).OfType<JsonObject>().FirstOrDefault(x => J.S(x, "id") == Id) ?? throw new InvalidOperationException("此項目已被移除，請重新整理。");
            foreach (var settingField in Fields)
            {
                string key=settingField.Spec.Key;
                if (JsonNode.DeepEquals(original[key],changed[key])) continue;
                if (!JsonNode.DeepEquals(original[key],target[key]) && !JsonNode.DeepEquals(changed[key],target[key]))
                    throw new InvalidOperationException($"「{settingField.Spec.Label}」已被其他操作修改，請重新整理後再編輯；目前草稿仍保留。");
                target[key] = changed[key]?.DeepClone();
            }
        });
        }
        catch (SavedButRefreshFailedException)
        {
            original = (Collection == "system" ? workspace.Config : J.A(workspace.Config, Collection).OfType<JsonObject>().First(x => J.S(x, "id") == Id)).DeepClone().AsObject();
            throw;
        }
        original = (Collection == "system" ? workspace.Config : J.A(workspace.Config, Collection).OfType<JsonObject>().First(x => J.S(x, "id") == Id)).DeepClone().AsObject();
    }
}

public sealed class AsyncCommand(Func<Task> execute, Action<Exception> error, Func<bool>? canExecute = null) : ObservableObject, ICommand
{
    private bool running;
    public event EventHandler? CanExecuteChanged;
    public bool CanExecute(object? parameter) => !running && (canExecute?.Invoke() ?? true);
    public async void Execute(object? parameter)
    {
        if (!CanExecute(parameter)) return;
        running = true; CanExecuteChanged?.Invoke(this, EventArgs.Empty);
        try { await execute(); } catch (Exception ex) { error(ex); }
        finally { running = false; CanExecuteChanged?.Invoke(this, EventArgs.Empty); }
    }
}
