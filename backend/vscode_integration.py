"""Explicit profile aliases and reversible VS Code integration, without inference."""
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
    url = f"http://127.0.0.1:{config['api_port']}/v1/chat/completions"
    entries = []
    for model in config["models"]:
        base = {"id": model["id"], "name": model["name"], "url": url,
                "toolCalling": model.get("tool_calling", True),
                "vision": bool(model.get("vision") and model.get("mmproj")),
                "thinking": bool(model.get("reasoning_supported", True)), "streaming": True,
                "contextWindow": model["context"]}
        base["maxOutputTokens"] = min(max((p["max_tokens"] for p in config["profiles"]), default=4096),
                                      max(1, model["context"]-1024))
        # VS Code uses maxInputTokens/maxOutputTokens for BYOK capability
        # negotiation. Keep contextWindow for older clients, but also expose
        # the standard field so Agent Host does not guess an invalid budget.
        base["maxInputTokens"] = max(1, model["context"] - base["maxOutputTokens"])
        entries.append(base)
        for profile in config["profiles"]:
            entry = copy.deepcopy(base)
            entry.update(id=f"{model['id']}::{profile['id']}",
                         name=f"{model['name']} · {profile['name']}",
                         maxOutputTokens=min(profile["max_tokens"], max(1, model["context"]-1024)))
            entry["maxInputTokens"] = max(1, model["context"] - entry["maxOutputTokens"])
            entries.append(entry)
    return entries


def preview(config, data_dir):
    models_file, agents_dir = locations()
    return {"ok": True, "model_count": len(model_entries(config)),
            "api_url": f"http://127.0.0.1:{config['api_port']}/v1/chat/completions",
            "models_file": str(models_file), "agents_dir": str(agents_dir),
            "default_model_id": config["default_model_id"],
            "profiles": [{"id": p["id"], "name": p["name"], "max_tokens": p["max_tokens"]}
                         for p in config["profiles"]],
            "message": "更新本地模型及模式別名。由於 VS Code Agent Host 目前不穩定支援 customendpoint 的 agent 固定模型，agent 檔案不寫入 model 欄位，請在 Agent 視窗選取本機模型；原始檔案會先備份。"}


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
    provider["models"] = [m for m in provider.get("models", []) if m.get("id") not in owned | previous] + entries
    default = next((m for m in config["models"] if m["id"] == config["default_model_id"]), None)
    if default is None:
        raise ValueError("請先指定有效的預設模型。")
    outputs = [(models_file, json.dumps(providers, ensure_ascii=False, indent=4)+"\n")]
    descriptions = {
        "quick-chat": ("Local Quick Chat", "Fast local-model chat for questions and explanations.", "[web]",
                       "Answer the user's question directly and concisely. Do not claim to inspect, change, or run anything."),
        "coding": ("Local Coding", "Focused local coding assistant.", "[execute, read, edit, search, web]",
                   "Work directly on the user's requested coding task. Make focused changes and verify the result."),
        "deep-coding": ("Local Deep Coding", "Deep analysis for difficult coding tasks.", "[execute, read, edit, search, web]",
                        "Perform deep analysis before acting on difficult coding tasks. Investigate root causes and verify conclusions.")}
    for profile in config["profiles"]:
        profile_id = profile["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", profile_id):
            raise ValueError("模式 ID 只能含英文字母、數字、連字號與底線。")
        filename = f"local-{profile_id}.agent.md" if profile_id in descriptions else f"lmm-{profile_id}.agent.md"
        agent_path = agents_dir / filename
        if agent_path.exists():
            source = agent_path.read_text(encoding="utf-8-sig")
        else:
            name, description, tool_list, instruction = descriptions.get(profile_id,
                (profile["name"], "Local Model Manager custom profile.", "[read, search, web]", "Answer the user's request clearly and use the available tools only as needed."))
            source = f"---\nname: {json.dumps(name, ensure_ascii=False)}\ndescription: {json.dumps(description)}\ntools: {tool_list}\nagents: []\nuser-invocable: true\ndisable-model-invocation: true\n---\n\n{instruction}\n"
        # Do not pin `model:` here. VS Code Agent Host currently rejects
        # customendpoint models referenced from .agent.md. The profile prompt
        # remains in the file and the manager can infer quick/coding/deep when
        # the user selects the local model in the Agent Host model picker.
        outputs.append((agent_path, update_frontmatter(source, None)))
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
            "agents": [str(p) for p, _ in outputs[1:]], "requires_reload": True,
            "message": "已備份並更新 VS Code 設定。Agent 檔案未固定 customendpoint model，請完整重新啟動 VS Code，並在 Agent 視窗的模型選擇器選取本機模型後再執行 Local agent。"}
