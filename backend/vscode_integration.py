"""Reversible VS Code integration for physical models and AMIEBL profile agents."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def locations():
    return (Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Code/User/chatLanguageModels.json",
            Path.home() / ".copilot/agents")


def read_jsonc(text):
    """Strip comments outside strings. Preserve URLs and credential strings exactly."""
    result = []
    i = 0
    quoted = False
    while i < len(text):
        char = text[i]
        if quoted:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                i += 1
                result.append(text[i])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif text[i:i+2] == "//":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text[i:i+2] == "/*":
            end = text.find("*/", i+2)
            if end < 0:
                raise ValueError("VS Code 設定含有未結束的註解。")
            i = end + 2
            continue
        else:
            result.append(char)
        i += 1
    text = "".join(result)
    # Remove trailing commas only while outside a JSON string.
    result = []
    quoted = False
    i = 0
    while i < len(text):
        char = text[i]
        if quoted:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                i += 1
                result.append(text[i])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif char == "," and text[i+1:].lstrip().startswith(("]", "}")):
            pass
        else:
            result.append(char)
        i += 1
    return json.loads("".join(result).lstrip("\ufeff"))


def model_entries(config):
    """Expose only physical models to VS Code's model picker.

    Profile selection is carried by generated .agent.md files instead of
    multiplying every model into model::profile aliases. The aliases remain
    supported by the HTTP API for other clients that need them.
    """
    url = f"http://127.0.0.1:{config['api_port']}/v1/chat/completions"
    entries = []
    for model in config["models"]:
        capability = model.get("reasoning_capability")
        thinking = capability in ("toggle", "always") if capability is not None else bool(model.get("reasoning_supported", False))
        entry = {"id": model["id"], "name": model["name"], "url": url,
                 "toolCalling": model.get("tool_calling", True),
                 "vision": bool(model.get("vision") and model.get("mmproj")),
                 "thinking": thinking, "streaming": True,
                 "contextWindow": model["context"]}
        # Profiles are per-request ceilings, not a reason to reserve almost
        # the entire window before Copilot assembles its Agent prompt.
        # Keep at least three quarters for instructions, tools and history.
        entry["maxOutputTokens"] = min(max((p["max_tokens"] for p in config["profiles"]), default=4096),
                                       max(1, model["context"] // 4))
        # VS Code uses maxInputTokens/maxOutputTokens for BYOK capability
        # negotiation. Keep contextWindow for older clients, but also expose
        # the standard field so Agent Host does not guess an invalid budget.
        entry["maxInputTokens"] = max(1, model["context"] - entry["maxOutputTokens"])
        entries.append(entry)
    return entries


def preview(config, data_dir):
    models_file, agents_dir = locations()
    entries = model_entries(config)
    model_count = len(entries)
    limits = "\n".join(f"{m['name']}: input {m['maxInputTokens']:,} / output {m['maxOutputTokens']:,} tokens" for m in entries)
    agent_count = len(config["profiles"])
    return {"ok": True, "model_count": model_count, "agent_count": agent_count,
            "token_limits": [{"id": m["id"], "name": m["name"], "context": m["contextWindow"], "input": m["maxInputTokens"], "output": m["maxOutputTokens"]} for m in entries],
            "api_url": f"http://127.0.0.1:{config['api_port']}/v1/chat/completions",
            "models_file": str(models_file), "agents_dir": str(agents_dir),
            "default_model_id": config["default_model_id"],
            "profiles": [{"id": p["id"], "name": p["name"], "agent_name": agent_display_name(p), "agent_sync_mode": p.get("agent_sync_mode", "preserve"), "max_tokens": p["max_tokens"]}
                         for p in config["profiles"]],
            "summary": f"將在 VS Code 登錄 {model_count} 個實體模型，並處理 {agent_count} 個 Agent。預設同步模式名稱與 AMIEBL 模式標記並保留 VS Code 手動修改；設為完整管理的 Agent 才會由 GUI 覆寫。原始設定會先備份；完成後需要重新載入 VS Code 視窗。\nVS Code 輸出預留最多為 Context 的四分之一；不修改 Profile 上限。\n{limits}",
            "message": "VS Code 模型清單只會顯示實體模型；使用模式由 .agent.md 中的 AMIEBL profile 標記選擇。Agent 不固定 customendpoint model，請在 Agent 視窗的模型選擇器選取本機模型。"}


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_frontmatter(source, model_name=None):
    if not source.lstrip("\ufeff").startswith("---"):
        raise ValueError("agent 檔案缺少有效的 YAML 標頭，為保留現有內容已停止更新。")
    source = source.lstrip("\ufeff")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---(?=\r?\n|$)", source, re.S)
    if not match:
        raise ValueError("agent 檔案標頭格式不正確，已停止更新。")
    header = match.group(1)
    if re.search(r"^model:\s*$", header, re.M):
        # Preserve unfamiliar multiline YAML instead of corrupting it.
        raise ValueError("agent 使用多行 model 設定；請先在 VS Code 將它改為單行再連接。")
    if model_name is None:
        # A customendpoint model can be selected in normal Chat, but current
        # VS Code Agent Host versions reject a custom agent whose frontmatter
        # pins that model. Remove only the single-line field and preserve all
        # other user-authored frontmatter.
        header = re.sub(r"^model:.*(?:\r?\n|$)", "", header, flags=re.M)
    else:
        line = "model: " + json.dumps(model_name, ensure_ascii=False)
        if re.search(r"^model:", header, re.M):
            header = re.sub(r"^model:.*$", lambda _: line, header, flags=re.M)
        else:
            header += "\n" + line
    return "---\n" + header.replace("\r\n", "\n") + "\n---" + source[match.end():]


def agent_display_name(profile):
    return profile.get("name") or profile.get("agent_name") or profile["id"]


def sync_agent_name(source, display_name):
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---(?=\r?\n|$)", source, re.S)
    if not match:
        raise ValueError("既有 Agent 缺少有效 YAML 標頭，已停止同步。")
    header = match.group(1)
    if re.search(r"^name:[ \t]*(?:[|>].*|)\r?$", header, re.M):
        raise ValueError("既有 Agent 名稱使用多行 YAML，請先改為單行再同步。")
    line = "name: " + json.dumps(display_name, ensure_ascii=False)
    if re.search(r"^name:", header, re.M):
        header = re.sub(r"^name:[^\r\n]*", lambda _: line, header, flags=re.M)
    else:
        header = line + ("\r\n" if "\r\n" in source else "\n") + header
    return source[:match.start(1)] + header + source[match.end(1):]


def preserve_agent_profile_marker(source, profile_id, display_name=None):
    """Sync display name and profile marker, preserving tools and instructions."""
    source = source.lstrip("\ufeff")
    if display_name is not None:
        source = sync_agent_name(source, display_name)
    marker = f"AMIEBL_PROFILE:{profile_id}"
    pattern = r"(?mi)^[ \t]*AMIEBL_PROFILE\s*:\s*[A-Za-z0-9_-]+[ \t]*(?=\r?$)"
    if re.search(pattern, source):
        return re.sub(pattern, marker, source, count=1)
    match = re.match(r"^---\s*\r?\n.*?\r?\n---(?=\r?\n|$)", source, re.S)
    if not match:
        raise ValueError("既有 VS Code Agent 缺少有效 YAML 標頭；為避免覆蓋手動設定，已停止同步。")
    newline = "\r\n" if "\r\n" in source else "\n"
    return source[:match.end()] + newline * 2 + marker + source[match.end():]

def render_agent(profile):
    tools = json.dumps(profile.get("agent_tools", []), ensure_ascii=False)
    header = [
        "---",
        "name: " + json.dumps(agent_display_name(profile), ensure_ascii=False),
        "description: " + json.dumps(profile.get("agent_description", ""), ensure_ascii=False),
        "tools: " + tools,
        "agents: []",
        "user-invocable: true",
        "disable-model-invocation: true",
        "---",
        "",
        f"AMIEBL_PROFILE:{profile['id']}",
        "",
        (profile.get("agent_instructions") or "Answer the user's request clearly and use the available tools only as needed.").strip(),
        "",
    ]
    return "\n".join(header)

def apply(config, data_dir):
    models_file, agents_dir = locations()
    original = models_file.read_text(encoding="utf-8-sig") if models_file.exists() else "[]"
    providers = read_jsonc(original)
    if not isinstance(providers, list):
        raise ValueError("VS Code 模型設定不是陣列，已停止更新以保留原始內容。")
    providers = copy.deepcopy(providers)
    provider = next((p for p in providers if p.get("vendor") == "customendpoint" and p.get("name") == "llama.cpp"), None)
    if provider is None:
        provider = {"name": "llama.cpp", "vendor": "customendpoint", "apiType": "chat-completions", "apiKey": "local"}
        providers.append(provider)
    provider["apiType"] = "chat-completions"
    entries = model_entries(config)
    owned_file = Path(data_dir) / "vscode-owned-models.json"
    previous = set(json.loads(owned_file.read_text(encoding="utf-8"))) if owned_file.exists() else set()
    owned = {entry["id"] for entry in entries}
    legacy_aliases = {f"{model['id']}::{profile['id']}" for model in config["models"] for profile in config["profiles"]}
    provider["models"] = [m for m in provider.get("models", []) if m.get("id") not in owned | previous | legacy_aliases] + entries
    default = next((m for m in config["models"] if m["id"] == config["default_model_id"]), None)
    if default is None:
        raise ValueError("請先指定有效的預設模型。")
    outputs = [(models_file, json.dumps(providers, ensure_ascii=False, indent=4)+"\n")]
    for profile in config["profiles"]:
        profile_id = profile["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", profile_id):
            raise ValueError("模式 ID 只能含英文字母、數字、連字號與底線。")
        # Keep the historical filenames for the three built-in profiles so
        # existing installations are updated in place. Custom profiles use
        # an AMIEBL-owned filename derived from their immutable profile ID.
        filename = f"local-{profile_id}.agent.md" if profile_id in {"quick-chat", "coding", "deep-coding"} else f"lmm-{profile_id}.agent.md"
        agent_path = agents_dir / filename
        if agent_path.exists() and profile.get("agent_sync_mode", "preserve") == "preserve":
            source = agent_path.read_bytes().decode("utf-8-sig")
            content = preserve_agent_profile_marker(source, profile_id, agent_display_name(profile))
        else:
            content = render_agent(profile)
        outputs.append((agent_path, content))
    backup_dir = Path(data_dir) / "backups" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")+"-vscode")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = []
    originals = {}
    for path, _ in outputs:
        originals[path] = path.read_bytes() if path.exists() else None
        if path.exists():
            backup = backup_dir / path.name
            shutil.copy2(path, backup)
            backups.append(str(backup))
    try:
        for path, text in outputs:
            atomic_write(path, text)
        atomic_write(owned_file, json.dumps(sorted(owned), ensure_ascii=False, indent=2))
    except Exception:
        for path, previous_bytes in originals.items():
            if previous_bytes is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous_bytes.decode("utf-8-sig"))
        raise
    return {"ok": True, "backups": backups, "model_count": len(entries),
            "agent_count": len(outputs) - 1,
            "agents": [str(p) for p, _ in outputs[1:]], "requires_reload": True,
            "message": "已備份並更新 VS Code 設定。模型選單只保留實體模型；預設同步模式同步模式名稱與 AMIEBL_PROFILE 標記並保留既有 Agent 的 tools、prompt 與其他手動設定。只有設為「由 AMIEBL 完整管理」的 Agent 會被 GUI 覆寫。請重新載入 VS Code。"}
