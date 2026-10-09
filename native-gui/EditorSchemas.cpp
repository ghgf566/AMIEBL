#include "pch.h"
#include "EditorState.h"
namespace amiebl {
using namespace winrt;
std::vector<FieldSpec> EditorSchema(hstring collection, JsonObject const &config) {
    std::vector<FieldSpec> result;
    auto add = [&](hstring key, hstring label, FieldKind kind = FieldKind::Text,
                   bool optional = false, double minimum = 0, double maximum = 1048576,
                   std::vector<hstring> options = {},
                   std::function<bool(JsonObject const &)> enabled = {}) {
        result.push_back({key, label, kind, optional, minimum, maximum, options, enabled});
    };
    std::vector<hstring> profiles;
    for (auto v : array(config, L"profiles"))
        profiles.push_back(str(v.GetObject(), L"id"));
    if (collection == L"models") {
        add(L"name", L"顯示名稱", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"path", L"主模型 GGUF 路徑", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"context", L"Context（tokens）", FieldKind::Integer, false, 512, 2097152, {}, {});
        add(L"cpu_threads", L"CPU 執行緒（0 = 自動）", FieldKind::Integer, false, 0, 4096, {}, {});
        add(L"auto_fit", L"自動估算 GPU 層數", FieldKind::Boolean, false, 0, 1048576, {}, {});
        add(L"gpu_layers", L"GPU 層數（-1 = 全部）", FieldKind::Integer, false, -1, 10000, {},
            [](JsonObject const &d) { return !flag(d, L"auto_fit"); });
        add(L"fit_target_enabled", L"自訂每張 GPU 的預留記憶體", FieldKind::Boolean, false, 0,
            1048576, {}, {});
        add(L"fit_target_mib", L"預留顯示記憶體（MiB）", FieldKind::Integer, false, 0, 1048576, {},
            [](JsonObject const &d) {
                return flag(d, L"auto_fit") && flag(d, L"fit_target_enabled");
            });
        add(L"cache_type", L"KV Cache", FieldKind::Choice, false, 0, 1048576,
            {L"f16", L"q8_0", L"q4_0"}, {});
        add(L"mtp", L"啟用 MTP", FieldKind::Boolean, false, 0, 1048576, {}, {});
        add(L"mtp_source", L"MTP 來源", FieldKind::Choice, false, 0, 1048576,
            {L"native", L"external"}, [](JsonObject const &d) { return flag(d, L"mtp"); });
        add(L"mtp_draft_path", L"外部 Draft GGUF 路徑", FieldKind::Text, false, 0, 1048576, {},
            [](JsonObject const &d) {
                return flag(d, L"mtp") && str(d, L"mtp_source") == L"external";
            });
        add(L"mtp_draft_max", L"MTP 猜測 tokens（空白 = 預設）", FieldKind::Integer, true, 1, 64,
            {}, [](JsonObject const &d) { return flag(d, L"mtp"); });
        add(L"vision", L"啟用視覺", FieldKind::Boolean, false, 0, 1048576, {}, {});
        add(L"mmproj", L"視覺 projector 路徑", FieldKind::Text, false, 0, 1048576, {},
            [](JsonObject const &d) { return flag(d, L"vision"); });
        add(L"default_profile_id", L"預設使用模式", FieldKind::Choice, false, 0, 1048576, profiles,
            {});
        add(L"temperature", L"Temperature（空白 = 客戶端/引擎預設）", FieldKind::Number, true, 0, 5,
            {}, {});
        add(L"top_p", L"Top P", FieldKind::Number, true, 0, 1, {}, {});
        add(L"top_k", L"Top K", FieldKind::Integer, true, 0, 100000, {}, {});
        add(L"min_p", L"Min P", FieldKind::Number, true, 0, 1, {}, {});
    } else if (collection == L"profiles") {
        add(L"name", L"名稱", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"thinking_mode", L"思考策略", FieldKind::Choice, false, 0, 1048576,
            {L"auto", L"on", L"off", L"model"}, {});
        add(L"reasoning_level", L"思考強度", FieldKind::Choice, false, 0, 1048576,
            {L"light", L"balanced", L"deep", L"extreme"}, {});
        add(L"budget_mode", L"思考預算", FieldKind::Choice, false, 0, 1048576, {L"auto", L"custom"},
            {});
        add(L"thinking_budget", L"自訂思考 token 上限（需引擎支援）", FieldKind::Integer, false, 0,
            1048576, {}, [](JsonObject const &d) {
                return str(d, L"budget_mode") == L"custom" &&
                       (str(d, L"thinking_mode") == L"auto" || str(d, L"thinking_mode") == L"on");
            });
        add(L"max_tokens", L"整次生成上限（思考＋回答）", FieldKind::Integer, false, 256, 1048576,
            {}, {});
        add(L"agent_sync_mode", L"VS Code Agent 同步", FieldKind::Choice, false, 0, 1048576,
            {L"preserve", L"managed"}, {});
        add(L"agent_description", L"Agent 描述", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"agent_tools", L"Agent 工具（逗號分隔）", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"agent_instructions", L"Agent 指令", FieldKind::Multiline, false, 0, 1048576, {}, {});
    } else if (collection == L"system") {
        add(L"auto_start", L"登入 Windows 後自動啟動", FieldKind::Boolean, false, 0, 1048576, {},
            {});
        add(L"start_hidden", L"啟動時在背景待命", FieldKind::Boolean, false, 0, 1048576, {}, {});
        add(L"close_to_tray", L"關閉視窗後留在系統匣", FieldKind::Boolean, false, 0, 1048576, {},
            {});
        add(L"preload", L"啟動時預載模型", FieldKind::Boolean, false, 0, 1048576, {}, {});
        add(L"idle_minutes", L"預設閒置卸載分鐘（0 = 不卸載）", FieldKind::Integer, false, 0, 10080,
            {}, {});
        add(L"default_profile_id", L"預設使用模式", FieldKind::Choice, false, 0, 1048576, profiles,
            {});
        add(L"engine_dir", L"llama.cpp 資料夾", FieldKind::Text, false, 0, 1048576, {}, {});
        add(L"api_port", L"Agent 連接埠（重啟生效）", FieldKind::Integer, false, 1024, 65535, {},
            {});
        add(L"engine_port", L"引擎連接埠（重新載入生效）", FieldKind::Integer, false, 1024, 65535,
            {}, {});
        add(L"model_dirs", L"模型搜尋資料夾（每行一個）", FieldKind::Multiline, false, 0, 1048576,
            {}, {});
        add(L"log_request_bodies", L"除錯時保留完整請求內容", FieldKind::Boolean, false, 0, 1048576,
            {}, {});
        add(L"log_retention_days", L"紀錄保留天數", FieldKind::Integer, false, 1, 365, {}, {});
    }
    return result;
}
hstring FieldSection(hstring key, hstring collection) {
    if (collection == L"models") {
        if (key == L"name" || key == L"path")
            return L"模型檔案";
        if (key == L"context" || key == L"cpu_threads" || key == L"auto_fit" ||
            key == L"gpu_layers" || key == L"fit_target_enabled" || key == L"fit_target_mib" ||
            key == L"cache_type")
            return L"效能與記憶體";
        if (key == L"mtp" || key == L"mtp_source" || key == L"mtp_draft_path" ||
            key == L"mtp_draft_max" || key == L"vision" || key == L"mmproj")
            return L"MTP 與視覺";
        if (key == L"keep_loaded" || key == L"idle_minutes" || key == L"default_profile_id")
            return L"模型使用方式";
        return L"進階採樣";
    } else if (collection == L"profiles") {
        if (key == L"name")
            return L"使用模式";
        if (key == L"thinking_mode" || key == L"reasoning_level" || key == L"budget_mode" ||
            key == L"thinking_budget" || key == L"max_tokens")
            return L"思考策略與生成上限";
        return L"VS Code Agent";
    } else if (collection == L"system") {
        if (key == L"auto_start" || key == L"start_hidden" || key == L"close_to_tray" ||
            key == L"preload")
            return L"啟動與背景執行";
        if (key == L"idle_minutes" || key == L"default_profile_id")
            return L"預設模型行為";
        if (key == L"log_request_bodies" || key == L"log_retention_days")
            return L"紀錄與除錯";
        return L"引擎與連線";
    }
    return L"";
}
hstring FieldHelp(hstring key, hstring collection) {
    if (key == L"context")
        return L"可記住的輸入與生成總長度。越大通常占用越多 KV "
               L"記憶體；滑桿以倍增調整，也可輸入精確值。重新載入模型後生效。";
    if (key == L"cpu_threads")
        return L"0 "
               L"由引擎自動決定；滑桿依本機邏輯處理器數設定常用範圍，仍可輸入進階值"
               L"。";
    if (key == L"auto_fit")
        return L"載入時依 Context、KV Cache 與預留記憶體估算 GPU "
               L"層數。關閉後使用手動 GPU 層數。";
    if (key == L"fit_target_enabled")
        return L"未自訂時沿用 llama.cpp 的預留記憶體預設值。視覺 projector 與外部 "
               L"Draft 額外用量未包含於獨立估算。";
    if (key == L"cache_type")
        return L"f16 精度較高；q8_0／q4_0 減少 KV "
               L"記憶體用量，實際支援依引擎與模型而定。";
    if (key == L"mtp")
        return L"推測解碼可能加快生成；需模型內建 NextN／MTP 或相容的外部 "
               L"Draft，並非每個 GGUF 都支援。";
    if (key == L"vision")
        return L"需搭配相容的 projector GGUF；勾選並指定路徑後，重新載入才會套用。";
    if (key == L"idle_minutes")
        return L"所有模型共用此閒置時間。0 "
               L"不自動卸載；手動保持載入時會暫停自動卸載。";
    if (key == L"temperature")
        return L"控制抽樣隨機性；較低偏穩定，較高偏多樣。空白交由客戶端／引擎；明確"
               L"填值會覆蓋請求採樣值，下次請求生效。";
    if (key == L"top_p")
        return L"候選詞累積機率範圍（0～1）；較低更聚焦。留白使用客戶端／引擎預設"
               L"。";
    if (key == L"top_k")
        return L"只保留機率最高的 K 個候選詞；0 不設限。留白使用客戶端／引擎預設。";
    if (key == L"min_p")
        return L"相對最高機率的最低門檻（0～1）；較高會排除更多低機率候選詞，0 "
               L"不啟用。";
    if (key == L"thinking_mode")
        return L"自動：先判斷任務是否需要思考；固定開啟／關閉仍受模型模板能力限制；"
               L"跟隨模型預設保留模型原有策略。";
    if (key == L"reasoning_level")
        return L"AMIEBL 將強度轉成模型實際支援的原生 Effort "
               L"或預算策略。各模型的控制方式與效果不同。";
    if (key == L"budget_mode")
        return L"自動預算依偵測能力決定；自訂會送出明確 token "
               L"上限。請同時確認模型庫的 Thinking 支援說明。";
    if (key == L"thinking_budget")
        return L"0 嘗試立即結束思考。上限能否真正截斷取決於 llama.cpp 的思考 "
               L"parser；原生 Effort 可與此上限並用。";
    if (key == L"max_tokens")
        return L"整次生成的 token 上限，包含思考與最終回答；與 Context 容量不同。";
    if (key == L"name" && collection == L"profiles")
        return L"此名稱也會同步成 VS Code Agent "
               L"的顯示名稱；改名後請從總覽按「同步至 VS Code」。";
    if (key == L"agent_sync_mode")
        return L"保留模式保護手動工具與指令；管理模式由 AMIEBL 同步。同步至 VS "
               L"Code 前仍會預覽變更。";
    if (key == L"model_dirs")
        return L"掃描模型時搜尋的資料夾，也可從模型庫管理。取消登錄不會移動或刪除實"
               L"體 GGUF。";
    if (key == L"log_request_bodies")
        return L"開啟後可能記錄提示詞與請求內容，只建議需要除錯時使用。";
    return L"";
}
} // namespace amiebl
