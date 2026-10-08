using LocalModelManager;

static void Check(bool condition, string why)
{
    if (!condition) throw new Exception(why);
}

string root = Path.Combine(Path.GetTempPath(), "amiebl-install-mode-" + Guid.NewGuid().ToString("N"));
string app = Path.Combine(root, "app");
string local = Path.Combine(root, "local");
Directory.CreateDirectory(app);
Directory.CreateDirectory(local);
try
{
    string portableFlag = Path.Combine(app, "portable.flag");
    string installedFlag = Path.Combine(app, "installed.flag");
    File.WriteAllText(portableFlag, "portable");
    Check(BackendHost.ResolveDataDirectory(app, local, null) == Path.Combine(app, "data"),
        "Standalone portable did not use local bundle data");

    string old = Path.Combine(app, "data");
    Directory.CreateDirectory(Path.Combine(old, "nested"));
    File.WriteAllText(Path.Combine(old, "config.json"), "legacy config");
    File.WriteAllText(Path.Combine(old, "nested", "history.json"), "legacy history");
    File.WriteAllText(installedFlag, "installed");

    string expected = Path.Combine(local, "LocalModelManager");
    Check(BackendHost.ResolveDataDirectory(app, local, null) == expected,
        "Installed marker did not override obsolete portable marker");
    Check(File.ReadAllText(Path.Combine(expected, "config.json")) == "legacy config",
        "Old config not migrated on first installed launch");
    Check(File.ReadAllText(Path.Combine(expected, "nested", "history.json")) == "legacy history",
        "Old nested history not migrated");
    Check(File.ReadAllText(Path.Combine(old, "config.json")) == "legacy config",
        "Migration deleted original data");

    File.WriteAllText(Path.Combine(expected, "config.json"), "new config");
    Check(BackendHost.ResolveDataDirectory(app, local, null) == expected, "Installed data path changed");
    Check(File.ReadAllText(Path.Combine(expected, "config.json")) == "new config",
        "Existing data overwritten by old installation");

    string explicitPath = Path.Combine(root, "explicit");
    Check(BackendHost.ResolveDataDirectory(app, local, explicitPath) == explicitPath,
        "Explicit data-dir must take precedence over installation markers");

    File.Delete(installedFlag);
    Check(BackendHost.ResolveDataDirectory(app, local, null) == Path.Combine(app, "data"),
        "Removing installed marker did not restore portable mode");

    Console.WriteLine("PASS: portable path, installed marker, legacy migration, existing data protection, explicit override.");
}
finally
{
    Directory.Delete(root, recursive: true);
}
