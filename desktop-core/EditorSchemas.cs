namespace LocalModelManager;

public static class EditorSchemas
{
    private static FieldSpec Int(string key, string label, double min, double max, bool optional = false, Func<System.Text.Json.Nodes.JsonObject, bool>? enabled = null) => new(key, label, FieldKind.Integer, optional, min, max, Enabled: enabled);
    private static FieldSpec Bool(string key, string label) => new(key, label, FieldKind.Boolean);
    private static FieldSpec Choice(string key, string label, params string[] options) => new(key, label, FieldKind.Choice, Options: options);
    public static FieldSpec[] Model(string[] profiles) => [
        new("name", "顯示名稱"), new("path", "主模型 GGUF 路徑"),
        Int("context", "Context（tokens）",512,2097152),
        Int("cpu_threads", "CPU 執行緒（0 = 自動）",0,4096),
        Bool("auto_fit", "自動估算 GPU 層數"), Int("gpu_layers", "GPU 層數（-1 = 全部）",-1,10000, enabled: d => !J.B(d,"auto_fit")),
        Bool("fit_target_enabled", "自訂每張 GPU 的預留記憶體"), Int("fit_target_mib", "預留顯示記憶體（MiB）",0,1048576, enabled: d => J.B(d,"auto_fit") && J.B(d,"fit_target_enabled")),
        Choice("cache_type", "KV Cache", "f16", "q8_0", "q4_0"),
        Bool("mtp", "啟用 MTP"), new("mtp_source", "MTP 來源", FieldKind.Choice, Options: ["native","external"], Enabled: d => J.B(d,"mtp")),
        new("mtp_draft_path", "外部 Draft GGUF 路徑", Enabled: d => J.B(d,"mtp") && J.S(d,"mtp_source")=="external"),
        Int("mtp_draft_max", "MTP 猜測 tokens（空白 = 預設）",1,64,true, d => J.B(d,"mtp")),
        Bool("vision", "啟用視覺"), new("mmproj", "視覺 projector 路徑", Enabled: d => J.B(d,"vision")),
        Choice("default_profile_id", "預設使用模式", profiles),
        new("temperature", "Temperature（空白 = 客戶端/引擎預設）", FieldKind.Number,true,0,5),
        new("top_p", "Top P", FieldKind.Number,true,0,1), Int("top_k", "Top K",0,100000,true), new("min_p", "Min P", FieldKind.Number,true,0,1)
    ];
    public static FieldSpec[] Profile => [
        new("name", "名稱"), Choice("thinking_mode", "思考策略", "auto","on","off","model"),
        Choice("reasoning_level", "思考強度", "light","balanced","deep","extreme"),
        Choice("budget_mode", "思考預算", "auto","custom"),
        Int("thinking_budget", "自訂思考 token 上限（需引擎支援）",0,1048576, enabled: d => J.S(d,"budget_mode")=="custom" && J.S(d,"thinking_mode") is "auto" or "on"),
        Int("max_tokens", "整次生成上限（思考＋回答）",256,1048576),
        Choice("agent_sync_mode", "VS Code Agent 同步", "preserve","managed"),
        new("agent_description", "Agent 描述"), new("agent_tools", "Agent 工具（逗號分隔）"), new("agent_instructions", "Agent 指令", FieldKind.Multiline)
    ];
    public static FieldSpec[] System(string[] profiles) => [
        Bool("auto_start", "登入 Windows 後自動啟動"), Bool("start_hidden", "啟動時在背景待命"), Bool("close_to_tray", "關閉視窗後留在系統匣"), Bool("preload", "啟動時預載模型"),
        Int("idle_minutes", "預設閒置卸載分鐘（0 = 不卸載）",0,10080), Choice("default_profile_id", "預設使用模式", profiles),
        new("engine_dir", "llama.cpp 資料夾"), Int("api_port", "Agent 連接埠（重啟生效）",1024,65535), Int("engine_port", "引擎連接埠（重新載入生效）",1024,65535),
        new("model_dirs", "模型搜尋資料夾（每行一個）", FieldKind.Multiline), Bool("log_request_bodies", "除錯時保留完整請求內容"), Int("log_retention_days", "紀錄保留天數",1,365)
    ];
}
