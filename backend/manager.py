"""Loopback-only llama.cpp supervisor and cancellation-aware OpenAI gateway.

Only children created by this process are ever stopped. Configuration changes
never silently reload a busy engine. Request summaries contain no message text.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import uvicorn

VERSION = "1.0.0"
TERMINAL = {"completed", "cancelled", "error"}
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_QUEUE = 32
MAX_HISTORY = 200
MAX_BODY = 32 * 1024 * 1024


def utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows antivirus, indexers and sync clients (notably OneDrive) can
        # briefly hold the destination between close() and ReplaceFile/rename.
        # Retry only transient sharing/access-denied cases; persistent or
        # unrelated filesystem errors still surface immediately.
        for attempt in range(8):
            try:
                os.replace(temp, path)
                break
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)
                if not transient or attempt == 7:
                    raise
                time.sleep(0.015 * (attempt + 1))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def default_config() -> dict:
    legacy_model_path = Path(r"D:\model\unsloth\Qwen3.8-27B-GGUF\Qwen3.8-27B-UD-IQ4_XS.gguf")
    legacy_projector = Path(r"D:\model\unsloth\Qwen3.8-27B-GGUF\mmproj-F16.gguf")
    model_root = Path(os.environ.get("LMM_MODEL_DIR", r"D:\model")).expanduser()
    model_candidates = sorted(
        (p for p in model_root.rglob("*.gguf") if "mmproj" not in p.name.lower() and "draft" not in p.name.lower()),
        key=lambda p: str(p).lower(),
    ) if model_root.is_dir() else []
    model_env = os.environ.get("LMM_DEFAULT_MODEL_PATH", "").strip()
    has_packaged_model_root = bool(os.environ.get("LMM_MODEL_DIR", "").strip())
    model_path = Path(model_env) if model_env else (legacy_model_path if legacy_model_path.is_file() else (model_candidates[0] if model_candidates else (model_root / "model.gguf" if has_packaged_model_root else legacy_model_path)))
    projector_env = os.environ.get("LMM_DEFAULT_PROJECTOR", "").strip()
    projector = Path(projector_env) if projector_env else (legacy_projector if not has_packaged_model_root else Path(""))
    if not projector.is_file() and model_candidates:
        same_dir = model_candidates[0].parent / "mmproj-F16.gguf"
        projector = same_dir if same_dir.is_file() else Path("")
    projector_value = "" if projector == Path("") else str(projector)
    return {"schema_version": 1, "model_dirs": [str(model_root)],
            "engine_dir": os.environ.get("LMM_ENGINE_DIR", str(Path.home() / "llama.cpp")), "api_port": 8080, "engine_port": 8081,
            "default_model_id": "qwen3.8-27b-local", "default_profile_id": "coding",
            "idle_minutes": 15, "auto_start": False, "start_hidden": False,
            "close_to_tray": True, "preload": False, "log_request_bodies": False,
            "log_retention_days": 7, "vscode_abort_watch": True,
            "models": [{"id": "qwen3.8-27b-local", "name": "Qwen3.8 27B", "path": str(model_path),
                        "mmproj": projector_value, "vision": bool(projector_value and Path(projector_value).is_file()), "context": 65536, "gpu_layers": 17,
                        "auto_fit": True, "fit_target_enabled": False, "fit_target_mib": 2048, "cache_type": "q4_0", "cpu_threads": 0, "native_context": 0, "mtp": True, "mtp_source": "native", "mtp_draft_path": "", "mtp_draft_max": None, "mtp_capability": "unknown", "mtp_layers": 0,
                        "keep_loaded": False, "idle_minutes": None, "default_profile_id": "coding",
                        "temperature": None, "top_p": None, "top_k": None, "min_p": None,
                        "reasoning_supported": True, "reasoning_capability": "unknown", "reasoning_efforts": [], "reasoning_default_effort": "", "reasoning_budget_supported": False, "reasoning_toggle_keys": [], "reasoning_detection": "pending"}],
            "profiles": [{"id": ident, "name": name, "thinking_mode": "auto", "reasoning_level": level,
                          "budget_mode": "auto", "thinking_budget": budget, "max_tokens": cap}
                         for ident, name, level, budget, cap in
                         [("quick-chat", "Quick Chat", "light", 512, 4096),
                          ("coding", "Coding", "balanced", 1536, 8192),
                          ("deep-coding", "Deep Coding", "extreme", 4096, 12288)]]}


def number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} 必須是有效數字。")
    if not minimum <= value <= maximum or (integer and int(value) != value):
        raise ValueError(f"{name} 必須介於 {minimum} 與 {maximum}。")


def profile_agent_defaults(profile_id: str, name: str) -> dict:
    presets = {
        "quick-chat": {
            "agent_name": "Local Quick Chat",
            "agent_sync_mode": "preserve",
            "agent_description": "Fast local-model chat for questions and explanations.",
            "agent_tools": ["web"],
            "agent_instructions": "Answer the user's question directly and concisely. Do not claim to inspect, change, or run anything.",
        },
        "coding": {
            "agent_name": "Local Coding",
            "agent_sync_mode": "preserve",
            "agent_description": "Focused local coding assistant.",
            "agent_tools": ["execute", "read", "edit", "search", "web"],
            "agent_instructions": "Work directly on the user's requested coding task. Make focused changes and verify the result.",
        },
        "deep-coding": {
            "agent_name": "Local Deep Coding",
            "agent_sync_mode": "preserve",
            "agent_description": "Deep analysis for difficult coding tasks.",
            "agent_tools": ["execute", "read", "edit", "search", "web"],
            "agent_instructions": "Perform deep analysis before acting on difficult coding tasks. Investigate root causes and verify conclusions.",
        },
    }
    return copy.deepcopy(presets.get(profile_id, {
        "agent_name": name,
        "agent_sync_mode": "preserve",
        "agent_description": "AMIEBL local-model agent.",
        "agent_tools": ["read", "search", "web"],
        "agent_instructions": "Answer the user's request clearly and use the available tools only as needed.",
    }))

def validate_config(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("設定必須是 JSON 物件。")
    c = copy.deepcopy(value)
    defaults = default_config()
    for k, v in defaults.items():
        c.setdefault(k, copy.deepcopy(v))
    if c["schema_version"] != 1:
        raise ValueError("不支援此設定版本。")
    for key in ("api_port", "engine_port"):
        number(c[key], key, 1024, 65535, True)
    if c["api_port"] == c["engine_port"]:
        raise ValueError("API 與模型引擎必須使用不同連接埠。")
    number(c["idle_minutes"], "閒置時間", 0, 10080)
    number(c["log_retention_days"], "紀錄保留天數", 1, 365, True)
    if not isinstance(c["model_dirs"], list) or any(not isinstance(p, str) or not p.strip() for p in c["model_dirs"]):
        raise ValueError("模型資料夾必須是路徑清單。")
    if not isinstance(c["engine_dir"], str) or not c["engine_dir"].strip():
        raise ValueError("請指定 llama.cpp 資料夾。")
    for key in ("auto_start", "start_hidden", "close_to_tray", "preload", "log_request_bodies", "vscode_abort_watch"):
        if not isinstance(c[key], bool):
            raise ValueError(f"{key} 必須是開啟或關閉。")
    ids = {}
    for collection in ("models", "profiles"):
        if not isinstance(c[collection], list):
            raise ValueError(f"{collection} 必須是清單。")
        ids[collection] = set()
        for item in c[collection]:
            if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", str(item.get("id", ""))):
                raise ValueError("ID 只能使用英文字母、數字、點、連字號及底線。")
            if collection == "profiles" and "." in item["id"]:
                raise ValueError("模式 ID 只能使用英文字母、數字、連字號及底線。")
            if item["id"] in ids[collection]:
                raise ValueError("模型或模式 ID 不可重複。")
            ids[collection].add(item["id"])
            if not isinstance(item.get("name"), str) or not item["name"].strip():
                raise ValueError("請填寫模型及模式名稱。")
            if len(item["name"]) > 200:
                raise ValueError("名稱最多 200 字。")
    if not c["profiles"]:
        raise ValueError("至少保留一個使用模式。")
    for p in c["profiles"]:
        for key, val in profile_agent_defaults(p["id"], p["name"]).items():
            p.setdefault(key, val)
        if p.get("thinking_mode") not in ("auto", "on", "off", "model"):
            raise ValueError("無效的思考策略。")
        # Migrate the old model-specific effort field into AMIEBL's abstract
        # reasoning level. Keep old fields readable so existing configs and
        # external tooling do not break during the transition.
        if "reasoning_level" not in p:
            legacy = p.get("effort", "medium")
            p["reasoning_level"] = {"low": "light", "medium": "balanced", "high": "deep", "xhigh": "extreme"}.get(legacy, "balanced")
        if p.get("reasoning_level") not in ("light", "balanced", "deep", "extreme"):
            raise ValueError("無效的 AMIEBL 思考強度。")
        if "budget_mode" not in p:
            p["budget_mode"] = "custom" if "thinking_budget" in p else "auto"
        if p.get("budget_mode") not in ("auto", "custom"):
            raise ValueError("無效的思考預算模式。")
        p.setdefault("thinking_budget", 1536)
        number(p.get("max_tokens"), "總生成上限", 1, 1048576, True)
        number(p.get("thinking_budget"), "思考預算", 0, 1048576, True)
        if p["budget_mode"] == "custom" and p["thinking_budget"] >= p["max_tokens"] and p["thinking_mode"] != "off":
            raise ValueError("自訂思考預算必須小於總生成上限，為回答保留空間。")
        if p.get("agent_sync_mode") not in ("preserve", "managed"):
            raise ValueError("無效的 VS Code Agent 同步方式。")
        if not isinstance(p.get("agent_name"), str) or not p["agent_name"].strip():
            raise ValueError("VS Code Agent 名稱不可留白。")
        if len(p["agent_name"]) > 200:
            raise ValueError("VS Code Agent 名稱最多 200 字。")
        if not isinstance(p.get("agent_description"), str) or len(p["agent_description"]) > 1000:
            raise ValueError("VS Code Agent 說明必須是 1000 字以內的文字。")
        if not isinstance(p.get("agent_instructions"), str) or not p["agent_instructions"].strip():
            raise ValueError("VS Code Agent 行為指令不可留白。")
        if len(p["agent_instructions"]) > 20000:
            raise ValueError("VS Code Agent 行為指令最多 20000 字。")
        if not isinstance(p.get("agent_tools"), list) or any(
            not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", tool) for tool in p["agent_tools"]
        ):
            raise ValueError("VS Code Agent 工具名稱格式不正確。")
    if c["default_profile_id"] not in ids["profiles"]:
        raise ValueError("預設使用模式不存在。")
    if c["models"] and c["default_model_id"] not in ids["models"]:
        raise ValueError("預設模型不存在。")
    for m in c["models"]:
        # Older configs always passed --fit-target explicitly. Preserve that
        # behavior during migration; newly created models default to llama.cpp.
        if "fit_target_enabled" not in m:
            m["fit_target_enabled"] = True
        if "reasoning_capability" not in m:
            legacy_supported = bool(m.get("reasoning_supported", False))
            m["reasoning_capability"] = "toggle" if legacy_supported else "unknown"
            m["reasoning_detection"] = "legacy"
            m.setdefault("reasoning_default_effort", "")
            m.setdefault("reasoning_budget_supported", legacy_supported)
            m.setdefault("reasoning_toggle_keys", ["enable_thinking"] if legacy_supported else [])
        conservative = {"mmproj": "", "vision": False, "context": 8192, "gpu_layers": 0,
                        "auto_fit": True, "fit_target_enabled": False, "fit_target_mib": 2048, "cache_type": "f16", "cpu_threads": 0, "native_context": 0, "mtp": False, "mtp_source": "native", "mtp_draft_path": "", "mtp_draft_max": None, "mtp_capability": "unknown", "mtp_layers": 0,
                        "keep_loaded": False, "idle_minutes": None, "default_profile_id": c["default_profile_id"],
                        "temperature": None, "top_p": None, "top_k": None, "min_p": None,
                        "reasoning_supported": False, "reasoning_capability": "unknown", "reasoning_efforts": [],
                        "reasoning_default_effort": "", "reasoning_budget_supported": False, "reasoning_toggle_keys": [], "reasoning_detection": "pending"}
        for key, val in conservative.items():
            m.setdefault(key, val)
        if not isinstance(m.get("path"), str) or not m["path"].strip():
            raise ValueError("請指定模型檔案。")
        if not isinstance(m["mmproj"], str):
            raise ValueError("視覺模型路徑必須是文字。")
        for key in ("vision", "auto_fit", "fit_target_enabled", "mtp", "keep_loaded", "reasoning_supported"):
            if not isinstance(m[key], bool):
                raise ValueError(f"{key} 必須是開啟或關閉。")
        if m["vision"] and not m["mmproj"]:
            raise ValueError("啟用視覺時請指定視覺模型。")
        number(m["context"], "上下文容量", 512, 2097152, True)
        number(m.get("native_context", 0), "模型原生上下文", 0, 2097152, True)
        if m.get("native_context", 0) > 0 and m["context"] > m["native_context"]:
            raise ValueError(f"上下文容量不可超過此 GGUF 宣告的原生上限 {m['native_context']} tokens。")
        number(m.get("cpu_threads", 0), "CPU 執行緒上限", 0, 4096, True)
        number(m["gpu_layers"], "GPU 層數", -1, 999, True)
        number(m["fit_target_mib"], "GPU 預留記憶體", 0, 1048576, True)
        if m.get("temperature") is not None:
            number(m["temperature"], "temperature", 0, 5)
        if m.get("top_p") is not None:
            number(m["top_p"], "top_p", 0, 1)
        if m.get("top_k") is not None:
            number(m["top_k"], "top_k", 0, 100000, True)
        if m.get("min_p") is not None:
            number(m["min_p"], "min_p", 0, 1)
        if m.get("mtp_source") not in ("native", "external"):
            raise ValueError("MTP 來源只能是模型內建或外部 Draft。")
        if not isinstance(m.get("mtp_draft_path"), str):
            raise ValueError("MTP Draft 路徑必須是文字。")
        # Do not reject native MTP here based on cached capability metadata.
        # Existing configs may still say "unknown" until startup/save inspects
        # the GGUF header. Real capability is enforced immediately before load.
        if m.get("mtp") and m.get("mtp_source") == "external" and not m.get("mtp_draft_path", "").strip():
            raise ValueError("使用外部 MTP Draft 時請指定 GGUF 路徑。")
        if m.get("mtp_capability") not in ("available", "unavailable", "incomplete", "unknown"):
            raise ValueError("無效的 MTP 能力偵測狀態。")
        number(m.get("mtp_layers", 0), "MTP 層數", 0, 1024, True)
        if m.get("mtp_draft_max") is not None:
            number(m["mtp_draft_max"], "MTP 最大猜測 Token", 1, 64, True)
        if m["idle_minutes"] is not None:
            number(m["idle_minutes"], "模型閒置時間", 0, 10080)
        if m["cache_type"] not in ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "q5_0", "q5_1", "iq4_nl"):
            raise ValueError("不支援此 KV cache 精度。")
        if m["default_profile_id"] not in ids["profiles"]:
            raise ValueError("模型指定的使用模式不存在。")
        if m.get("reasoning_capability") not in ("unknown", "none", "always", "toggle"):
            raise ValueError("無效的 reasoning 能力狀態。")
        allowed_efforts = ("minimal", "low", "medium", "high", "xhigh", "max")
        if not isinstance(m["reasoning_efforts"], list) or any(e not in allowed_efforts for e in m["reasoning_efforts"]):
            raise ValueError("不支援的模型原生 reasoning effort 清單。")
        if not isinstance(m.get("reasoning_default_effort"), str):
            raise ValueError("模型預設 reasoning effort 必須是文字。")
        if m["reasoning_default_effort"] and m["reasoning_default_effort"] not in allowed_efforts:
            raise ValueError("模型預設 reasoning effort 不在支援清單。")
        if not isinstance(m.get("reasoning_budget_supported"), bool):
            raise ValueError("reasoning_budget_supported 必須是布林值。")
        if not isinstance(m.get("reasoning_toggle_keys"), list) or any(
            key not in ("enable_thinking", "thinking", "add_nothink_token") for key in m["reasoning_toggle_keys"]
        ):
            raise ValueError("無效的 reasoning toggle key。")
        if m.get("reasoning_detection") not in ("pending", "legacy", "gguf", "runtime"):
            raise ValueError("無效的 reasoning 能力來源。")
    return c


_GGUF_FIXED_TYPES = {
    0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
    4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("B", 1),
    10: ("Q", 8), 11: ("q", 8), 12: ("d", 8),
}


def _gguf_read_exact(handle, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("GGUF 檔案標頭不完整。")
    return data


def _gguf_u32(handle) -> int:
    return struct.unpack("<I", _gguf_read_exact(handle, 4))[0]


def _gguf_u64(handle) -> int:
    return struct.unpack("<Q", _gguf_read_exact(handle, 8))[0]


def _gguf_string(handle, max_length: int = 1024 * 1024 * 1024) -> str:
    length = _gguf_u64(handle)
    if length > max_length:
        raise ValueError("GGUF 字串長度異常。")
    return _gguf_read_exact(handle, length).decode("utf-8", errors="replace")


def _gguf_skip_string(handle) -> None:
    length = _gguf_u64(handle)
    if length > 1024 * 1024 * 1024:
        raise ValueError("GGUF 字串長度異常。")
    handle.seek(length, os.SEEK_CUR)


def _gguf_skip_value(handle, value_type: int) -> None:
    if value_type in _GGUF_FIXED_TYPES:
        handle.seek(_GGUF_FIXED_TYPES[value_type][1], os.SEEK_CUR)
        return
    if value_type == 8:
        _gguf_skip_string(handle)
        return
    if value_type == 9:
        element_type = _gguf_u32(handle)
        count = _gguf_u64(handle)
        if count > 1024 * 1024 * 1024:
            raise ValueError("GGUF 陣列元素數量異常。")
        if element_type in _GGUF_FIXED_TYPES:
            handle.seek(_GGUF_FIXED_TYPES[element_type][1] * count, os.SEEK_CUR)
        elif element_type == 8:
            for _ in range(count):
                _gguf_skip_string(handle)
        else:
            raise ValueError("GGUF 含有不支援的巢狀 metadata 類型。")
        return
    raise ValueError("GGUF metadata 類型無法辨識。")


def _gguf_read_scalar(handle, value_type: int):
    if value_type == 8:
        return _gguf_string(handle)
    spec = _GGUF_FIXED_TYPES.get(value_type)
    if spec is None:
        raise ValueError("GGUF metadata 不是可讀取的純量。")
    return struct.unpack("<" + spec[0], _gguf_read_exact(handle, spec[1]))[0]


REASONING_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")


def analyze_reasoning_template(template: str) -> dict:
    """Infer reasoning controls from the model's own Jinja chat template.

    This is capability-driven rather than model-family-driven: Qwen,
    DeepSeek, GLM, Gemma and future open models are handled by the same
    signals whenever their templates expose them.
    """
    result = {
        "reasoning_capability": "unknown",
        "reasoning_efforts": [],
        "reasoning_default_effort": "",
        "reasoning_budget_supported": False,
        "reasoning_toggle_keys": [],
    }
    if not isinstance(template, str) or not template.strip():
        return result

    lower = template.lower()
    toggle_keys = []
    if re.search(r"\benable_thinking\b", template):
        toggle_keys.append("enable_thinking")
    if re.search(r"\badd_nothink_token\b", template):
        toggle_keys.append("add_nothink_token")
    # DeepSeek V3.x and some other templates use a plain "thinking" kwarg.
    if re.search(r"(?i)(?:if|set|default|defined|not)\s+[^\n{}]{0,80}\bthinking\b|\bthinking\s+is\s+(?:not\s+)?defined", template):
        toggle_keys.append("thinking")

    has_reasoning_effort = bool(re.search(r"\breasoning_effort\b", template))
    has_reasoning_markers = any(token in lower for token in (
        "<think>", "</think>", "reasoning_content", "thinking_start_token", "thinking_end_token"
    ))

    # Some templates explicitly reject disabling thinking (for example some
    # always-reasoning Qwen variants). Do not advertise a toggle in that case.
    rejects_disable = bool(re.search(
        r"(?i)(disabl(?:e|ing)\s+thinking\s+is\s+not\s+supported|enable_thinking[^\n]{0,120}false[^\n]{0,120}raise_exception)",
        template,
    ))
    can_disable = bool(toggle_keys) and not rejects_disable

    efforts = []
    if has_reasoning_effort:
        # Extract only llama.cpp's known effort vocabulary, but derive which
        # members are accepted from the template itself. This covers templates
        # using tuple/list validation as well as named effort branches.
        for effort in REASONING_EFFORT_ORDER:
            if re.search(rf"(?i)['\"]{re.escape(effort)}['\"]", template):
                efforts.append(effort)

    default_effort = ""
    for pattern in (
        r"(?i)reasoning_effort\s*\|\s*default\(\s*['\"](minimal|low|medium|high|xhigh|max)['\"]",
        r"(?i)default\(\s*['\"](minimal|low|medium|high|xhigh|max)['\"]\s*\)[^\n]{0,80}reasoning_effort",
    ):
        match = re.search(pattern, template)
        if match:
            default_effort = match.group(1).lower()
            break
    if default_effort and default_effort not in efforts:
        efforts.append(default_effort)
    efforts.sort(key=lambda x: REASONING_EFFORT_ORDER.index(x))

    if can_disable:
        capability = "toggle"
    elif has_reasoning_markers or has_reasoning_effort:
        capability = "always"
    else:
        capability = "none"

    result.update(
        reasoning_capability=capability,
        reasoning_efforts=efforts,
        reasoning_default_effort=default_effort,
        reasoning_budget_supported=has_reasoning_markers,
        reasoning_toggle_keys=toggle_keys if can_disable else [],
    )
    return result

def inspect_gguf_capabilities(path_value: str) -> dict:
    """Inspect only the GGUF header/tensor directory; model weights are never loaded."""
    path = Path(path_value)
    result = {"mtp_capability": "unknown", "mtp_layers": 0, "gguf_architecture": "", "native_context": 0, "mtp_tensor_count": 0,
              "reasoning_capability": "unknown", "reasoning_efforts": [], "reasoning_default_effort": "",
              "reasoning_budget_supported": False, "reasoning_toggle_keys": [], "reasoning_detection": "pending"}
    if not path.is_file():
        return result
    try:
        with path.open("rb") as handle:
            if _gguf_read_exact(handle, 4) != b"GGUF":
                return result
            version = _gguf_u32(handle)
            if version not in (2, 3):
                return result
            tensor_count = _gguf_u64(handle)
            kv_count = _gguf_u64(handle)
            if tensor_count > 10_000_000 or kv_count > 10_000_000:
                return result

            nextn_layers = 0
            native_context = 0
            architecture = ""
            chat_template = ""
            for _ in range(kv_count):
                key = _gguf_string(handle, 65535)
                value_type = _gguf_u32(handle)
                if key == "general.architecture":
                    architecture = str(_gguf_read_scalar(handle, value_type))
                elif key.endswith(".nextn_predict_layers") and value_type in _GGUF_FIXED_TYPES:
                    raw = _gguf_read_scalar(handle, value_type)
                    nextn_layers = max(nextn_layers, int(raw))
                elif key.endswith(".context_length") and value_type in _GGUF_FIXED_TYPES:
                    raw = int(_gguf_read_scalar(handle, value_type))
                    if 512 <= raw <= 2097152:
                        native_context = max(native_context, raw)
                elif key.startswith("tokenizer.chat_template") and value_type == 8:
                    candidate = _gguf_read_scalar(handle, value_type)
                    if isinstance(candidate, str) and len(candidate) > len(chat_template):
                        chat_template = candidate
                else:
                    _gguf_skip_value(handle, value_type)

            nextn_tensors = 0
            for _ in range(tensor_count):
                name = _gguf_string(handle, 65535)
                dimensions = _gguf_u32(handle)
                if dimensions > 16:
                    raise ValueError("GGUF tensor 維度數異常。")
                handle.seek(8 * dimensions + 4 + 8, os.SEEK_CUR)
                if ".nextn." in name or name.startswith("nextn."):
                    nextn_tensors += 1

            result["gguf_architecture"] = architecture
            result["native_context"] = native_context
            result["mtp_layers"] = nextn_layers
            result["mtp_tensor_count"] = nextn_tensors
            reasoning = analyze_reasoning_template(chat_template)
            result.update(reasoning)
            if chat_template:
                result["reasoning_detection"] = "gguf"
            if nextn_layers > 0 and nextn_tensors > 0:
                result["mtp_capability"] = "available"
            elif nextn_layers > 0:
                result["mtp_capability"] = "incomplete"
            elif nextn_tensors > 0:
                result["mtp_capability"] = "unknown"
            else:
                result["mtp_capability"] = "unavailable"
    except (OSError, ValueError, OverflowError, struct.error):
        pass
    return result

def parse_fit_gpu_layers(output_text: str) -> int | None:
    match = re.search(r"(?:^|\s)-ngl\s+(-?\d+)", output_text)
    return int(match.group(1)) if match else None

def free_port(port: int) -> bool:
    with socket.socket() as s:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def message_text(message):
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


ABSTRACT_REASONING_LEVELS = ("light", "balanced", "deep", "extreme")
ABSTRACT_REASONING_TARGET = {"light": 1, "balanced": 2, "deep": 3, "extreme": 5}
AUTO_REASONING_BUDGET_RATIO = {"light": 0.15, "balanced": 0.30, "deep": 0.50, "extreme": 0.70}


def map_native_reasoning_effort(level: str, supported: list[str]) -> str | None:
    supported = [x for x in REASONING_EFFORT_ORDER if x in supported]
    if not supported:
        return None
    target = ABSTRACT_REASONING_TARGET.get(level, 2)
    # Prefer the deeper option on an equal-distance tie. This makes
    # "deep" map to xhigh on models exposing low / medium / xhigh.
    return min(
        supported,
        key=lambda x: (abs(REASONING_EFFORT_ORDER.index(x) - target), -REASONING_EFFORT_ORDER.index(x)),
    )


def auto_reasoning_budget(level: str, max_budget: int) -> int:
    ratio = AUTO_REASONING_BUDGET_RATIO.get(level, 0.30)
    return max(0, min(max_budget, int(round(max_budget * ratio))))


def apply_reasoning_toggle(kwargs: dict, toggle_keys: list[str], enabled: bool) -> dict:
    result = dict(kwargs)
    for key in toggle_keys:
        if key in ("enable_thinking", "thinking"):
            result[key] = enabled
        elif key == "add_nothink_token":
            result[key] = not enabled
    return result

class CancelledRequest(Exception):
    pass


@dataclass
class Ticket:
    body: dict
    model: dict
    profile: dict
    headers: dict
    record: dict
    started: float = field(default_factory=time.monotonic)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    output: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    response: asyncio.Future | None = None
    upstream_id: str | None = None
    upstream_sent: bool = False
    upstream_headers: bool = False
    cancel_task: asyncio.Task | None = None


class Manager:
    def __init__(self, data: Path, port=None, engine_port=None):
        self.data = data
        self.data.mkdir(parents=True, exist_ok=True)
        self.config_path = data / "config.json"
        if self.config_path.exists():
            raw_config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            self.config = validate_config(raw_config)
            # Persist newly introduced defaults immediately. This keeps the
            # configuration returned by GET and the on-disk snapshot identical
            # after an upgrade, so a rejected edit is truly atomic.
            if self.config != raw_config:
                atomic_json(self.config_path, self.config)
        else:
            self.config = validate_config(default_config())
            atomic_json(self.config_path, self.config)
        capability_changed = False
        for model in self.config["models"]:
            if (model.get("mtp_capability", "unknown") == "unknown" or model.get("native_context", 0) <= 0 or model.get("reasoning_detection") not in ("gguf", "runtime")) and Path(model["path"]).is_file():
                detected = inspect_gguf_capabilities(model["path"])
                model["mtp_capability"] = detected["mtp_capability"]
                model["mtp_layers"] = detected["mtp_layers"]
                model["native_context"] = detected["native_context"]
                if detected.get("reasoning_detection") == "gguf":
                    model["reasoning_capability"] = detected["reasoning_capability"]
                    model["reasoning_efforts"] = detected["reasoning_efforts"]
                    model["reasoning_default_effort"] = detected["reasoning_default_effort"]
                    model["reasoning_budget_supported"] = detected["reasoning_budget_supported"]
                    model["reasoning_toggle_keys"] = detected["reasoning_toggle_keys"]
                    model["reasoning_detection"] = "gguf"
                    model["reasoning_supported"] = detected["reasoning_capability"] in ("toggle", "always")
                if detected["mtp_capability"] != "available" and model.get("mtp") and model.get("mtp_source", "native") == "native":
                    model["mtp"] = False
                    model["mtp_draft_max"] = None
                capability_changed = True
        if capability_changed:
            atomic_json(self.config_path, self.config)
        self.port = port or self.config["api_port"]
        self.engine_port_override = engine_port
        self.token_path = data / "admin-token"
        if not self.token_path.exists():
            atomic_text(self.token_path, secrets.token_urlsafe(48))
        self.token = self.token_path.read_text(encoding="utf-8").strip()
        if len(self.token) < 24:
            raise ValueError("管理權杖檔案無效，請移除損毀的 admin-token 後重新啟動。")
        self.started = time.monotonic()
        self.last_used = self.started
        self.state = "unloaded"
        self.last_error = None
        self.model = None
        self.loaded_config = None
        self.engine_base = None
        self.engine_version = None
        self.process = None
        self.fit_process = None
        self.accepting = True
        self.deferred_unload = False
        self.manual_loading = False
        self.load_task = None
        self.lifecycle = asyncio.Lock()
        self.queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self.tickets: dict[str, Ticket] = {}
        self.active: Ticket | None = None
        self.history = deque(maxlen=MAX_HISTORY)
        self.lines = deque(maxlen=300)
        self.slots = []
        self.resources = {"ram_used_gb": None, "ram_total_gb": None, "gpu_used_mib": None, "gpu_total_mib": None}
        self.stopping = asyncio.Event()
        self.tasks = []
        self.http = None
        self.exit_callback = None
        self._load_history()

    def clean(self, value):
        text = str(value).replace(self.token, "[已隱藏]")
        text = re.sub(r"(?i)(authorization|api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+", r"\1=[已隱藏]", text)
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)[:800]

    def log(self, message):
        self.lines.append(f"{utc()}  {self.clean(message)}")

    def _load_history(self):
        path = self.data / "history.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            cutoff = time.time() - self.config["log_retention_days"] * 86400
            for row in rows[-MAX_HISTORY:]:
                if isinstance(row, dict) and row.get("phase") in TERMINAL and dt.datetime.fromisoformat(row["started_at"]).timestamp() >= cutoff:
                    self.history.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            self.log("先前任務紀錄無法讀取；服務仍可使用。")

    def save_config(self, value):
        incoming = copy.deepcopy(value)
        if isinstance(incoming, dict) and isinstance(incoming.get("models"), list):
            previous = {m["id"]: m for m in self.config.get("models", []) if isinstance(m, dict) and isinstance(m.get("id"), str)}
            for model in incoming["models"]:
                if not isinstance(model, dict) or not isinstance(model.get("path"), str):
                    continue
                old = previous.get(model.get("id"))
                path_changed = old is None or old.get("path") != model.get("path")
                if path_changed or model.get("mtp_capability", "unknown") == "unknown" or model.get("native_context", 0) <= 0 or model.get("reasoning_detection") not in ("gguf", "runtime"):
                    detected = inspect_gguf_capabilities(model["path"])
                    model["mtp_capability"] = detected["mtp_capability"]
                    model["mtp_layers"] = detected["mtp_layers"]
                    model["native_context"] = detected["native_context"]
                    if detected.get("reasoning_detection") == "gguf":
                        model["reasoning_capability"] = detected["reasoning_capability"]
                        model["reasoning_efforts"] = detected["reasoning_efforts"]
                        model["reasoning_default_effort"] = detected["reasoning_default_effort"]
                        model["reasoning_budget_supported"] = detected["reasoning_budget_supported"]
                        model["reasoning_toggle_keys"] = detected["reasoning_toggle_keys"]
                        model["reasoning_detection"] = "gguf"
                        model["reasoning_supported"] = detected["reasoning_capability"] in ("toggle", "always")
                    if detected["mtp_capability"] != "available" and model.get("mtp_source", "native") == "native":
                        model["mtp"] = False
                        model["mtp_draft_max"] = None
        c = validate_config(incoming)
        backup = self.data / "backups"
        backup.mkdir(exist_ok=True)
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup / (dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-config.json"))
        atomic_json(self.config_path, c)
        self.config = c
        files = sorted(backup.glob("*-config.json"))
        for p in files[:-20]:
            p.unlink(missing_ok=True)
        self.log("設定已儲存。需要重新載入的參數會在下次載入時套用。")
        return self.config

    def refine_reasoning_from_props(self, model_id: str, props: dict) -> None:
        """Refine pre-load GGUF heuristics using llama.cpp's own Jinja parser."""
        if not isinstance(props, dict):
            return
        template = props.get("chat_template")
        caps = props.get("chat_template_caps") if isinstance(props.get("chat_template_caps"), dict) else {}
        if not isinstance(template, str) or not template.strip():
            return
        detected = analyze_reasoning_template(template)
        if detected["reasoning_capability"] == "unknown":
            return
        if caps.get("supports_reasoning_effort") is False:
            detected["reasoning_efforts"] = []
            detected["reasoning_default_effort"] = ""
        model = next((m for m in self.config["models"] if m["id"] == model_id), None)
        if model is None:
            return
        for key in ("reasoning_capability", "reasoning_efforts", "reasoning_default_effort",
                    "reasoning_budget_supported", "reasoning_toggle_keys"):
            model[key] = copy.deepcopy(detected[key])
        model["reasoning_detection"] = "runtime"
        model["reasoning_supported"] = detected["reasoning_capability"] in ("toggle", "always")
        if self.model and self.model.get("id") == model_id:
            for key in ("reasoning_capability", "reasoning_efforts", "reasoning_default_effort",
                        "reasoning_budget_supported", "reasoning_toggle_keys", "reasoning_detection", "reasoning_supported"):
                self.model[key] = copy.deepcopy(model[key])
        atomic_json(self.config_path, self.config)

    def engine_command(self):
        override = os.environ.get("LMM_ENGINE_COMMAND_JSON")
        if override:
            command = json.loads(override)
            if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
                raise ValueError("LMM_ENGINE_COMMAND_JSON 必須是命令參數陣列。")
            return command
        return [str(Path(self.config["engine_dir"]) / "llama-server.exe")]

    async def start(self):
        self.http = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(connect=5, read=None, write=60, pool=5))
        self.tasks = [asyncio.create_task(self.worker()), asyncio.create_task(self.monitor()), asyncio.create_task(self.watch_vscode())]
        self.log("管理器已啟動，等待本地請求。")
        if self.config["preload"] and self.config["models"]:
            self.begin_load(self.config["default_model_id"])

    async def stop(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.accepting = False
        for ticket in list(self.tickets.values()):
            self.cancel_ticket(ticket)
        await asyncio.gather(*(t.cancel_task for t in list(self.tickets.values()) if t.cancel_task), return_exceptions=True)
        if self.load_task and not self.load_task.done():
            self.load_task.cancel()
            await asyncio.gather(self.load_task, return_exceptions=True)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.unload()
        for ticket in list(self.tickets.values()):
            self.finish(ticket, "cancelled")
        if self.http:
            await self.http.aclose()

    def model_by_id(self, ident):
        result = next((m for m in self.config["models"] if m["id"] == ident), None)
        if result is None:
            raise ValueError("找不到指定模型，請先加入模型庫。")
        return result

    def begin_load(self, ident):
        model = copy.deepcopy(self.model_by_id(ident))
        if self.tickets and (not self.model or self.model["id"] != ident):
            raise ValueError("目前有執行中或排隊任務，請完成後再切換模型。")
        if self.load_task and not self.load_task.done():
            if self.model and self.model["id"] == ident:
                return
            raise ValueError("模型正在載入，請稍候。")
        if self.state == "ready" and self.model and self.model["id"] == ident:
            return
        self.manual_loading = True
        self.load_task = asyncio.create_task(self.load(model))
        def handled(task):
            self.manual_loading = False
            if not task.cancelled():
                task.exception()
        self.load_task.add_done_callback(handled)

    def _drain_engine(self, process):
        # Consume all output to prevent pipe blockage. Never store unfiltered
        # engine lines: templates and error diagnostics may contain user prompts.
        try:
            for raw in iter(process.stdout.readline, b""):
                text = raw.decode("utf-8", errors="replace")
                if re.search(r"out of memory|CUDA error|failed to allocate", text, re.I):
                    self.last_error = "模型引擎回報記憶體或 GPU 錯誤，請減少 GPU 層數或上下文容量。"
                    self.log(self.last_error)
        finally:
            process.stdout.close()

    async def terminate(self, process):
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(asyncio.to_thread(process.wait), 5)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.to_thread(process.wait)
        except ProcessLookupError:
            pass

    async def _stop_engine(self):
        await self.terminate(self.fit_process)
        self.fit_process = None
        await self.terminate(self.process)
        self.process = None
        self.model = None
        self.loaded_config = None
        self.slots = []
        self.engine_base = None

    async def fit_layers(self, model):
        if not model["auto_fit"] or os.environ.get("LMM_SKIP_FIT") == "1":
            return model["gpu_layers"]
        fit = Path(self.config["engine_dir"]) / "llama-fit-params.exe"
        if not fit.is_file():
            raise ValueError("找不到 llama-fit-params.exe；請選擇完整引擎資料夾，或關閉自動 GPU 分配。")
        cmd = [str(fit), "-m", model["path"], "-c", str(model["context"]), "-ctk", model["cache_type"],
               "-ctv", model["cache_type"]]
        # Omit --fit-target unless the user explicitly overrides it, so
        # llama-fit-params can use llama.cpp's native default (currently
        # 1024 MiB per device) and follow future upstream default changes.
        if model.get("fit_target_enabled", False):
            cmd += ["--fit-target", str(model["fit_target_mib"])]
        # Keep this invocation aligned with interactive-start.ps1. The
        # current llama-fit-params binary does not accept --mmproj; the
        # projector is passed to llama-server after the layer count is known.
        proc = subprocess.Popen(cmd, cwd=self.config["engine_dir"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=HIDDEN)
        self.fit_process = proc
        try:
            output, _ = await asyncio.wait_for(asyncio.to_thread(proc.communicate), 180)
            output_text = output.decode("utf-8", errors="replace")
            fitted_layers = parse_fit_gpu_layers(output_text)
            if proc.returncode or fitted_layers is None:
                detail = output_text.strip()
                if len(detail) > 2400:
                    detail = detail[-2400:]
                suffix = f"\nllama-fit-params 輸出：\n{detail}" if detail else "\n工具沒有輸出可用的診斷訊息。"
                raise ValueError(f"GPU 分配計算失敗（退出代碼 {proc.returncode}）。{suffix}\n可調低上下文容量、降低預留顯示記憶體，或關閉自動 GPU 分配並手動指定層數。")
            return fitted_layers
        finally:
            await self.terminate(proc)
            self.fit_process = None

    async def load(self, model):
        async with self.lifecycle:
            if self.state == "ready" and self.model and self.model["id"] == model["id"]:
                return
            await self._stop_engine()
            self.state = "loading"
            self.last_error = None
            self.model = model
            try:
                if not Path(model["path"]).is_file():
                    raise ValueError("模型檔案不存在，請在模型庫重新選擇位置。")
                if model["vision"] and not Path(model["mmproj"]).is_file():
                    raise ValueError("視覺模型檔案不存在，請重新指定。")
                if model.get("mtp"):
                    if model.get("mtp_source", "native") == "native":
                        detected_mtp = inspect_gguf_capabilities(model["path"])
                        if detected_mtp["mtp_capability"] != "available":
                            raise ValueError("此 GGUF 未偵測到可用的內建 MTP／NextN 權重，請改用外部 MTP Draft 或關閉 MTP。")
                    else:
                        draft_path = Path(model.get("mtp_draft_path", ""))
                        if not draft_path.is_file():
                            raise ValueError("找不到外部 MTP Draft GGUF，請重新指定檔案。")
                        try:
                            with draft_path.open("rb") as draft_file:
                                if draft_file.read(4) != b"GGUF":
                                    raise ValueError("指定的外部 MTP Draft 不是有效的 GGUF 檔案。")
                        except OSError as exc:
                            raise ValueError("無法讀取外部 MTP Draft GGUF。") from exc
                engine_port = self.engine_port_override or self.config["engine_port"]
                if not free_port(engine_port):
                    raise ValueError(f"模型引擎連接埠 {engine_port} 已被使用。請停止舊啟動器或更換連接埠。")
                command = self.engine_command()
                if not Path(command[0]).is_file():
                    raise ValueError("找不到 llama-server.exe，請確認 llama.cpp 資料夾。")
                layers = await self.fit_layers(model)
                thread_limit = int(model.get("cpu_threads", 0) or 0)
                thread_arg = str(thread_limit if thread_limit > 0 else -1)
                args = ["-m", model["path"], "--alias", model["id"], "-c", str(model["context"]),
                        "-ctk", model["cache_type"], "-ctv", model["cache_type"], "--fit", "off", "-ngl", str(layers),
                        "-t", thread_arg, "-tb", thread_arg, "--jinja", "--reasoning-effort", "default", "--reasoning-budget", "-1",
                        "--no-reasoning-preserve", "--timeout", "18000", "--sse-ping-interval", "10",
                        "--host", "127.0.0.1", "--port", str(engine_port), "-np", "1", "--slots", "--metrics"]
                if model["mtp"]:
                    draft_max = model.get("mtp_draft_max")
                    args += ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(draft_max if draft_max is not None else 2)]
                    if model.get("mtp_source", "native") == "external":
                        args += ["--spec-draft-model", model["mtp_draft_path"]]
                if model["vision"]:
                    args += ["--mmproj", model["mmproj"], "--image-min-tokens", "1024"]
                self.process = subprocess.Popen(command + args, cwd=self.config["engine_dir"], stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, creationflags=HIDDEN)
                threading.Thread(target=self._drain_engine, args=(self.process,), daemon=True).start()
                self.engine_base = f"http://127.0.0.1:{engine_port}"
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise ValueError(f"模型引擎在載入時結束（代碼 {self.process.returncode}）。請檢查模型與 GPU／記憶體設定。")
                    try:
                        response = await self.http.get(self.engine_base + "/health", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.15)
                else:
                    raise ValueError("模型載入超過五分鐘，已停止本次載入。")
                self.loaded_config = {"model": copy.deepcopy(model), "engine_dir": self.config["engine_dir"], "engine_port": engine_port}
                self.state = "ready"
                self.last_used = time.monotonic()
                self.deferred_unload = False
                try:
                    props = (await self.http.get(self.engine_base + "/props", timeout=1)).json()
                    self.engine_version = self.clean(props.get("build_info", "")) or None
                    self.refine_reasoning_from_props(model["id"], props)
                except (httpx.HTTPError, ValueError, TypeError):
                    self.engine_version = None
                self.log(f"模型已載入：{model['name']}（GPU 層數 {layers}）。")
            except BaseException as exc:
                await self._stop_engine()
                if isinstance(exc, asyncio.CancelledError):
                    self.state = "unloaded"
                else:
                    self.state = "error"
                    self.last_error = self.clean(exc)
                    self.log(self.last_error)
                raise

    async def ensure_ready(self, ticket):
        if self.state == "ready" and self.model and self.model["id"] == ticket.model["id"]:
            return
        ticket.record["phase"] = "loading"
        if self.load_task and not self.load_task.done():
            await self.interruptible(asyncio.shield(self.load_task), ticket)
        if self.state != "ready" or not self.model or self.model["id"] != ticket.model["id"]:
            self.load_task = asyncio.create_task(self.load(ticket.model))
            try:
                await self.interruptible(asyncio.shield(self.load_task), ticket)
            except CancelledRequest:
                if not self.manual_loading and not any(t is not ticket and not t.done.is_set() for t in self.tickets.values()):
                    self.load_task.cancel()
                    await asyncio.gather(self.load_task, return_exceptions=True)
                raise

    async def unload(self):
        async with self.lifecycle:
            self.state = "unloading"
            await self._stop_engine()
            self.state = "unloaded"
            self.deferred_unload = False
            self.log("模型已卸載，API 仍待命。")

    def cancel_ticket(self, ticket):
        if ticket.done.is_set():
            return
        ticket.cancel.set()
        if self.active is not ticket:
            ticket.record["cancel_confirmed"] = True
            self.finish(ticket, "cancelled")
        elif ticket.cancel_task is None or ticket.cancel_task.done():
            ticket.cancel_task = asyncio.create_task(self.cancel_upstream(ticket))

    async def cancel_upstream(self, ticket):
        if not ticket.upstream_sent or not self.engine_base:
            ticket.record["cancel_confirmed"] = True
            return

        # DELETE may arrive before llama.cpp has fully registered the request.
        # Retry first, then give the single slot a reasonable grace period to
        # become idle before falling back to terminating the owned engine.
        success = False

        for attempt in range(3):
            try:
                response = await self.http.delete(
                    self.engine_base + "/v1/stream",
                    params={"conv_id": ticket.upstream_id},
                    timeout=2,
                )
                success = 200 <= response.status_code < 300
            except httpx.HTTPError:
                success = False

            # Before response headers, a successful DELETE can still have raced
            # request registration. Always replay at least once in that case.
            if success and (ticket.upstream_headers or attempt >= 1):
                break

            await asyncio.sleep(0.15 * (attempt + 1))

        if success:
            deadline = time.monotonic() + 3.0
            replay_at = time.monotonic() + 0.5

            while time.monotonic() < deadline:
                try:
                    slots = (
                        await self.http.get(
                            self.engine_base + "/slots",
                            timeout=1,
                        )
                    ).json()

                    if not isinstance(slots, list):
                        # /slots unsupported: a successful DELETE is our best
                        # available confirmation.
                        break

                    if not any(s.get("is_processing") for s in slots):
                        ticket.record["cancel_confirmed"] = True
                        return

                    # The original DELETE may have raced request registration.
                    # Replay occasionally while waiting for the slot to drain.
                    if time.monotonic() >= replay_at:
                        try:
                            await self.http.delete(
                                self.engine_base + "/v1/stream",
                                params={"conv_id": ticket.upstream_id},
                                timeout=2,
                            )
                        except httpx.HTTPError:
                            pass
                        replay_at = time.monotonic() + 0.5

                except (httpx.HTTPError, ValueError, TypeError):
                    # If DELETE itself succeeded but /slots cannot be queried,
                    # preserve the old fallback semantics.
                    ticket.record["cancel_confirmed"] = True
                    return

                await asyncio.sleep(0.2)

            # One final check after the grace period.
            try:
                slots = (
                    await self.http.get(
                        self.engine_base + "/slots",
                        timeout=1,
                    )
                ).json()

                if isinstance(slots, list) and not any(
                    s.get("is_processing") for s in slots
                ):
                    ticket.record["cancel_confirmed"] = True
                    return
            except (httpx.HTTPError, ValueError, TypeError):
                ticket.record["cancel_confirmed"] = True
                return

        # Last-resort safety net. The manager owns this single-slot llama-server,
        # so killing it is preferable to leaving orphan inference consuming GPU.
        self.log("引擎在取消寬限時間後仍未停止，正在停止本管理器擁有的模型進程。")
        await self.unload()
        ticket.record["cancel_confirmed"] = True

    async def interruptible(self, awaitable, ticket):
        operation = asyncio.ensure_future(awaitable)
        cancel = asyncio.create_task(ticket.cancel.wait())
        try:
            if ticket.cancel.is_set():
                raise CancelledRequest()
            done, _ = await asyncio.wait((operation, cancel), return_when=asyncio.FIRST_COMPLETED)
            if cancel in done:
                raise CancelledRequest()
            return operation.result()
        finally:
            cancel.cancel()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(cancel, operation, return_exceptions=True)

    def resolve(self, body, headers):
        ident = body.get("model") or self.config["default_model_id"]
        if not isinstance(ident, str):
            raise ValueError("model 必須是模型 ID。")
        parts = ident.split("::")
        if len(parts) > 2:
            raise ValueError("無效的模型模式別名。")
        model = copy.deepcopy(self.model_by_id(parts[0]))
        profile_id = parts[1] if len(parts) == 2 else headers.get("x-llm-profile")
        if not profile_id:
            # VS Code agents generated by AMIEBL carry a stable marker in their
            # system/developer instructions. User messages are intentionally
            # excluded so quoting an agent marker cannot change request policy.
            instruction_text = "\n".join(message_text(m) for m in body["messages"] if m.get("role") in ("system", "developer"))
            marker = re.search(r"(?i)AMIEBL_PROFILE\s*:\s*([A-Za-z0-9_-]+)", instruction_text)
            if marker:
                profile_id = marker.group(1)
            else:
                # Compatibility with agent files generated by older AMIEBL versions.
                text = instruction_text.lower()
                if "local deep coding" in text or "perform deep analysis" in text:
                    profile_id = "deep-coding"
                elif "answer the user's question directly and concisely" in text:
                    profile_id = "quick-chat"
                elif "work directly on the user's requested coding task" in text:
                    profile_id = "coding"
                else:
                    profile_id = model.get("default_profile_id") or self.config["default_profile_id"]
        profile = next((p for p in self.config["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise ValueError("指定的使用模式不存在。")
        return model, copy.deepcopy(profile)

    def submit(self, body, headers):
        if not self.accepting or self.stopping.is_set():
            raise HTTPException(503, "管理器目前暫停接收新任務。")
        if len(self.tickets) >= MAX_QUEUE:
            raise HTTPException(429, "等待中的任務過多，請稍後再試。")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
            raise ValueError("messages 必須是非空陣列。")
        if any(not isinstance(m, dict) or not isinstance(m.get("role"), str) for m in body["messages"]):
            raise ValueError("訊息格式不正確。")
        for key in ("max_tokens", "max_completion_tokens", "n_predict"):
            if key in body:
                number(body[key], key, 1, 1048576, True)
        if "stream" in body and not isinstance(body["stream"], bool):
            raise ValueError("stream 必須是布林值。")
        if body.get("n", 1) != 1:
            raise ValueError("目前單 slot 模式每次只支援一份生成結果（n=1）。")
        if "chat_template_kwargs" in body and not isinstance(body["chat_template_kwargs"], dict):
            raise ValueError("chat_template_kwargs 必須是物件。")
        model, profile = self.resolve(body, headers)
        ident = str(uuid.uuid4())
        record = {"id": ident, "model_id": model["id"], "model_name": model["name"], "profile_id": profile["id"],
                  "profile_name": profile["name"], "phase": "queued", "started_at": utc(), "finished_at": None,
                  "elapsed_seconds": 0, "first_token_seconds": None, "classifier_seconds": None,
                  "prompt_tokens": None, "cached_tokens": None, "generated_tokens": None, "thinking_tokens": None,
                  "prompt_tps": None, "generation_tps": None, "prompt_progress": None, "reasoning_level": None, "effort": None,
                  "thinking_budget": None, "max_tokens": None, "decision": None, "error": None, "cancel_confirmed": False}
        ticket = Ticket(copy.deepcopy(body), model, profile, headers, record)
        ticket.response = asyncio.get_running_loop().create_future()
        self.tickets[ident] = ticket
        self.queue.put_nowait(ticket)
        self.last_used = time.monotonic()
        if self.config["log_request_bodies"]:
            self.write_body_log(ticket)
        return ticket

    def write_body_log(self, ticket):
        logs = self.data / "request-bodies"
        logs.mkdir(exist_ok=True)
        # Opt-in bodies are bounded and separate from everyday summaries.
        text = json.dumps(ticket.body, ensure_ascii=False)
        if len(text.encode("utf-8")) <= 1024 * 1024:
            atomic_text(logs / (ticket.record["id"] + ".json"), text)
        files = sorted(logs.glob("*.json"), key=lambda p: p.stat().st_mtime)
        cutoff = time.time() - self.config["log_retention_days"] * 86400
        for index, path in enumerate(files):
            if index < len(files) - 100 or path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def finish(self, ticket, phase, error=None):
        if ticket.done.is_set():
            return
        ticket.record.update(phase=phase, finished_at=utc(), elapsed_seconds=round(time.monotonic() - ticket.started, 3))
        if error:
            ticket.record["error"] = self.clean(error)
        if not ticket.response.done():
            status = 499 if phase == "cancelled" else 502
            ticket.response.set_result((status, {"error": {"message": error or "請求已取消。", "type": phase}}, False))
        ticket.done.set()
        self.history.append(copy.deepcopy(ticket.record))
        self.tickets.pop(ticket.record["id"], None)
        atomic_json(self.data / "history.json", list(self.history))
        self.last_used = time.monotonic()
        self.log(f"任務 {ticket.record['id'][:8]}：{phase}。")

    def observe(self, ticket, obj):
        if not isinstance(obj, dict):
            return
        r = ticket.record
        usage = obj.get("usage") or {}
        timing = obj.get("timings") or {}
        mapping = [(usage.get("prompt_tokens"), "prompt_tokens"), (usage.get("completion_tokens"), "generated_tokens"),
                   ((usage.get("prompt_tokens_details") or {}).get("cached_tokens"), "cached_tokens"),
                   ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"), "thinking_tokens"),
                   (timing.get("prompt_per_second"), "prompt_tps"), (timing.get("predicted_per_second"), "generation_tps"),
                   (timing.get("prompt_n"), "prompt_tokens"), (timing.get("predicted_n"), "generated_tokens"),
                   (obj.get("tokens_cached"), "cached_tokens")]
        for value, key in mapping:
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                r[key] = value
        for choice in obj.get("choices", []):
            delta = choice.get("delta") or choice.get("message") or {}
            if delta.get("reasoning_content") or delta.get("reasoning"):
                r["phase"] = "thinking"
            elif delta.get("content") or delta.get("tool_calls"):
                r["phase"] = "generating"
            else:
                continue
            if r["first_token_seconds"] is None:
                r["first_token_seconds"] = round(time.monotonic() - ticket.started, 3)

    async def classify(self, ticket):
        """Fast local reasoning policy classifier.

        Auto mode must never spend a second model inference just to decide
        whether the real inference should think. Only the latest user message
        is inspected; system/developer prompts are deliberately ignored so an
        Agent persona mentioning debugging or planning cannot bias every task.

        The heuristic is conservative: clearly simple language tasks disable
        thinking, clearly analytical/technical tasks enable it, and ambiguous
        requests fall back to the selected profile's normal effort/budget.
        """
        ticket.record["phase"] = "classifying"
        started = time.monotonic()
        p = ticket.profile
        decision = {"thinking": True, "level": p["reasoning_level"]}

        user_text = ""
        for message in reversed(ticket.body["messages"]):
            if message.get("role") == "user":
                user_text = message_text(message).strip()
                if user_text:
                    break

        normalized = re.sub(r"\\s+", " ", user_text).strip().lower()

        complex_patterns = (
            r"\\b(debug|bug|error|exception|traceback|crash|race condition|deadlock|root cause|architecture|design|refactor|optimi[sz]e|algorithm|complexity|plan|strategy|compare|analy[sz]e|reason|why|implement|code|program|database|sql|api|network|performance|security|calculate|derive|proof|diagnose|troubleshoot)\\b",
            r"(除錯|偵錯|錯誤|異常|崩潰|閃退|競態|死鎖|根因|架構|設計|重構|最佳化|優化|演算法|複雜度|規劃|策略|比較|分析|推理|為什麼|原因|實作|程式碼|程式|資料庫|網路|效能|安全|計算|推導|證明|診斷|排查|怎麼修|修復)",
        )
        simple_patterns = (
            r"^(hi|hello|hey|yo|thanks?|thank you|你好|嗨|哈囉|哈啰|早安|午安|晚安|謝謝|感謝)[\\s!！?？,.，。～~]*$",
            r"\\b(translate|rewrite|proofread|paraphrase|shorten)\\b",
            r"(翻譯|翻成|改寫|潤飾|校對|縮短|換句話說|修正文法|整理格式)",
        )

        has_complex = bool(normalized) and any(re.search(pattern, normalized, re.I) for pattern in complex_patterns)
        has_simple = bool(normalized) and any(re.search(pattern, normalized, re.I) for pattern in simple_patterns)

        if has_complex:
            ticket.record["decision"] = "自動判斷：需要思考"
        elif has_simple:
            decision = {"thinking": False, "level": p["reasoning_level"]}
            ticket.record["decision"] = "自動判斷：直接回答"
        else:
            ticket.record["decision"] = "自動判斷：採用模式預設"

        ticket.record["classifier_seconds"] = round(time.monotonic() - started, 4)
        return decision

    async def policy(self, ticket):
        body = copy.deepcopy(ticket.body)
        p, model = ticket.profile, ticket.model
        body["model"] = model["id"]
        limits = [p["max_tokens"], max(1, model["context"] - 256)]
        limits += [body[k] for k in ("max_tokens", "max_completion_tokens", "n_predict") if k in body]
        cap = int(min(limits))
        body["max_tokens"] = cap
        for key in ("max_completion_tokens", "n_predict"):
            if key in body:
                body[key] = cap
        ticket.record["max_tokens"] = cap
        for key in ("temperature", "top_p", "top_k", "min_p"):
            val = model.get(key)
            if val is not None:
                body.setdefault(key, val)
        kwargs = body.get("chat_template_kwargs") or {}
        reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
        explicit = any(k in body for k in ("reasoning_effort", "thinking_budget_tokens")) or "effort" in reasoning or any(k in kwargs for k in ("enable_thinking", "thinking", "add_nothink_token", "thinking_budget", "thinking_budget_tokens"))
        answer_reserve = min(512, max(1, cap // 4))
        max_budget = max(0, cap - answer_reserve)
        if explicit:
            ticket.record["decision"] = "採用客戶端指定的思考設定"
            effort = body.get("reasoning_effort", reasoning.get("effort"))
            budget = body.get("thinking_budget_tokens", kwargs.get("thinking_budget_tokens", kwargs.get("thinking_budget")))
            if budget is not None:
                number(budget, "思考預算", -1, 1048576, True)
                budget = max_budget if budget == -1 else min(int(budget), max_budget)
                body["thinking_budget_tokens"] = budget
                for key in ("thinking_budget", "thinking_budget_tokens"):
                    if key in kwargs:
                        kwargs[key] = budget
                if "chat_template_kwargs" in body:
                    body["chat_template_kwargs"] = kwargs
            ticket.record.update(effort=effort, thinking_budget=budget)
            return body
        capability = model.get("reasoning_capability", "unknown")
        if capability in ("none", "unknown") or p["thinking_mode"] == "model":
            ticket.record["decision"] = "跟隨模型預設" if p["thinking_mode"] == "model" else "此模型未提供可控制的思考能力"
            return body

        decision = {"thinking": p["thinking_mode"] != "off", "level": p["reasoning_level"]}
        if p["thinking_mode"] == "auto":
            decision = await self.classify(ticket)
        else:
            ticket.record["decision"] = "固定開啟思考" if decision["thinking"] else "固定關閉思考"

        # Always-reasoning templates cannot safely be forced off. Respect the
        # model's own contract rather than injecting an unsupported switch.
        if capability == "always" and not decision["thinking"]:
            ticket.record["decision"] = "模型不支援關閉思考，已跟隨模型預設"
            ticket.record["reasoning_level"] = decision["level"]
            return body

        enabled = bool(decision["thinking"])
        level = decision["level"]
        ticket.record["reasoning_level"] = level
        supported_efforts = model.get("reasoning_efforts") or []
        native_effort = map_native_reasoning_effort(level, supported_efforts) if enabled else None

        # Apply the exact toggle variables declared by the model template.
        toggle_keys = model.get("reasoning_toggle_keys") or []
        if capability == "toggle":
            kwargs = apply_reasoning_toggle(kwargs, toggle_keys, enabled)
            if kwargs:
                body["chat_template_kwargs"] = kwargs

        if not enabled:
            # llama.cpp treats reasoning_effort=none as a universal request to
            # disable reasoning, while template-specific kwargs above cover
            # DeepSeek/GLM/Qwen variants that expose their own switch.
            body["reasoning_effort"] = "none"
            if model.get("reasoning_budget_supported", False):
                body["thinking_budget_tokens"] = 0
            ticket.record.update(effort="none", thinking_budget=0)
            return body

        budget = None
        if p.get("budget_mode", "auto") == "custom":
            budget = min(int(p["thinking_budget"]), max_budget)
        elif not native_effort and model.get("reasoning_budget_supported", False):
            # Models without native effort levels (for example many hybrid
            # thinking templates) use a token budget as AMIEBL's fallback
            # implementation of light/balanced/deep/extreme.
            budget = auto_reasoning_budget(level, max_budget)

        if native_effort:
            body["reasoning_effort"] = native_effort
        if budget is not None and model.get("reasoning_budget_supported", False):
            body["thinking_budget_tokens"] = budget

        ticket.record.update(effort=native_effort, thinking_budget=budget)
        return body

    async def process_ticket(self, ticket):
        await self.ensure_ready(ticket)
        body = await self.policy(ticket)
        if ticket.cancel.is_set():
            raise CancelledRequest()
        ticket.record["phase"] = "prompt"
        ticket.upstream_id = ticket.record["id"]
        ticket.upstream_sent = True
        ticket.upstream_headers = False
        headers = {"X-Conversation-Id": ticket.upstream_id, "Content-Type": "application/json"}
        if ticket.headers.get("x-agent-task-id"):
            headers["X-Agent-Task-Id"] = ticket.headers["x-agent-task-id"]
        request = self.http.build_request("POST", self.engine_base + "/v1/chat/completions", headers=headers, json=body)
        response = await self.interruptible(self.http.send(request, stream=True), ticket)
        ticket.upstream_headers = True
        try:
            if response.status_code >= 400:
                await self.interruptible(response.aread(), ticket)
                message = f"模型引擎回覆 HTTP {response.status_code}。請檢查上下文長度與模型設定。"
                # Return original error to the requesting client; never persist
                # its body, which may echo parts of their confidential prompt.
                ticket.response.set_result((response.status_code, response.content, False))
                self.finish(ticket, "error", message)
                return
            streaming = "text/event-stream" in response.headers.get("content-type", "")
            if streaming:
                ticket.response.set_result((response.status_code, None, True))
                async for line in response.aiter_lines():
                    if ticket.cancel.is_set():
                        raise CancelledRequest()
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload != "[DONE]":
                            try:
                                self.observe(ticket, json.loads(payload))
                            except ValueError:
                                pass
                    await self.interruptible(ticket.output.put((line + "\n").encode("utf-8")), ticket)
            else:
                raw = await self.interruptible(response.aread(), ticket)
                try:
                    self.observe(ticket, json.loads(raw))
                except ValueError:
                    pass
                ticket.response.set_result((response.status_code, raw, False))
            # The upstream can finish immediately after a disconnect or an
            # explicit cancel (the DELETE makes llama.cpp close the stream).
            # Let cancellation win that race so the task is never reported as
            # completed after the caller has already stopped waiting.
            if ticket.cancel.is_set():
                raise CancelledRequest()
            self.finish(ticket, "completed")
        finally:
            await response.aclose()

    async def worker(self):
        while not self.stopping.is_set():
            ticket = await self.queue.get()
            try:
                if ticket.done.is_set():
                    continue
                self.active = ticket
                await self.process_ticket(ticket)
            except CancelledRequest:
                if ticket.cancel_task is None:
                    ticket.cancel_task = asyncio.create_task(self.cancel_upstream(ticket))
                await asyncio.shield(ticket.cancel_task)
                self.finish(ticket, "cancelled")
            except asyncio.CancelledError:
                self.finish(ticket, "cancelled")
                raise
            except Exception as exc:
                # Do not include HTTP response/message bodies in everyday logs.
                message = str(exc) if isinstance(exc, ValueError) else f"本地引擎連線失敗（{type(exc).__name__}）；本次任務未自動重送。"
                self.finish(ticket, "error", message)
            finally:
                self.active = None
                self.queue.task_done()

    def pending(self):
        if self.config["api_port"] != self.port:
            return True
        if not self.loaded_config:
            return False
        live = self.loaded_config
        if live["engine_dir"] != self.config["engine_dir"] or live["engine_port"] != (self.engine_port_override or self.config["engine_port"]):
            return True
        current = next((m for m in self.config["models"] if m["id"] == live["model"]["id"]), {})
        ignore = {"keep_loaded", "idle_minutes", "default_profile_id", "name", "temperature", "top_p", "top_k", "min_p", "reasoning_supported", "reasoning_capability", "reasoning_efforts", "reasoning_default_effort", "reasoning_budget_supported", "reasoning_toggle_keys", "reasoning_detection", "native_context", "mtp_capability", "mtp_layers"}
        return {k:v for k,v in current.items() if k not in ignore} != {k:v for k,v in live["model"].items() if k not in ignore}

    def idle_limit(self):
        if not self.model:
            return None
        model = next((m for m in self.config["models"] if m["id"] == self.model["id"]), self.model)
        if model.get("keep_loaded"):
            return None
        minutes = model.get("idle_minutes")
        return float(self.config["idle_minutes"] if minutes is None else minutes) * 60

    def status(self):
        idle = max(0, time.monotonic() - self.last_used) if not self.tickets else 0
        limit = self.idle_limit()
        return {"state": self.state, "model_id": self.model["id"] if self.model else None,
                "model_name": self.model["name"] if self.model else None,
                "pid": self.process.pid if self.process and self.process.poll() is None else None,
                "accepting": self.accepting, "active_count": int(self.active is not None and not self.active.done.is_set()),
                "queued_count": sum(1 for t in self.tickets.values() if t is not self.active and not t.done.is_set()),
                "uptime_seconds": round(time.monotonic() - self.started, 1), "idle_seconds": round(idle, 1),
                "unload_in_seconds": round(max(0, limit - idle), 1) if limit is not None and self.state == "ready" and not self.tickets else None,
                "last_error": self.last_error, "api_url": f"http://127.0.0.1:{self.port}/v1/chat/completions",
                "engine_version": self.engine_version, "slots": self.slots, "resources": self.resources,
                "pending_config": self.pending(), "deferred_unload": self.deferred_unload}

    def records(self):
        records = [copy.deepcopy(t.record) for t in self.tickets.values()] + list(self.history)
        for r in records:
            if r["id"] in self.tickets:
                r["elapsed_seconds"] = round(time.monotonic() - self.tickets[r["id"]].started, 3)
        return sorted(records, key=lambda r: r["started_at"], reverse=True)[:MAX_HISTORY]

    def sample_resources(self):
        result = dict(self.resources)
        if os.name == "nt":
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
            m = Memory()
            m.length = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                result["ram_total_gb"] = round(m.total / 1024**3, 2)
                result["ram_used_gb"] = round((m.total - m.available) / 1024**3, 2)
        smi = shutil.which("nvidia-smi")
        if smi:
            try:
                r = subprocess.run([smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                                   capture_output=True, text=True, timeout=2, creationflags=HIDDEN)
                if r.returncode == 0:
                    pairs = [list(map(float, line.split(","))) for line in r.stdout.strip().splitlines()]
                    result["gpu_used_mib"] = sum(p[0] for p in pairs)
                    result["gpu_total_mib"] = sum(p[1] for p in pairs)
            except (OSError, ValueError, subprocess.SubprocessError):
                result["gpu_used_mib"] = result["gpu_total_mib"] = None
        return result

    async def monitor(self):
        resource_at = 0
        while not self.stopping.is_set():
            try:
                if self.process and self.process.poll() is not None and self.state == "ready":
                    self.last_error = f"模型引擎意外結束（代碼 {self.process.returncode}）。下次請求可以重新載入。"
                    self.state = "error"
                    self.slots = []
                    self.log(self.last_error)
                if self.state == "ready" and self.engine_base:
                    # Poll only an already-running owned engine. The manager
                    # performs unload itself; observing never causes auto-wake.
                    try:
                        raw = (await self.http.get(self.engine_base + "/slots", timeout=0.7)).json()
                        if isinstance(raw, list):
                            allowed = {"id", "id_task", "state", "is_processing", "n_ctx", "n_decoded", "n_prompt_tokens", "n_prompt_tokens_processed", "n_prompt_tokens_cache", "n_tokens", "n_past", "prompt_progress"}
                            self.slots = [{k:v for k,v in s.items() if k in allowed} for s in raw if isinstance(s, dict)]
                            if self.active and self.active.record["phase"] in ("prompt", "thinking", "generating"):
                                slot = next((s for s in raw if s.get("is_processing")), None)
                                if slot:
                                    r = self.active.record
                                    total, processed = slot.get("n_prompt_tokens"), slot.get("n_prompt_tokens_processed")
                                    if isinstance(total, (int, float)) and total > 0 and isinstance(processed, (int, float)):
                                        r["prompt_progress"] = min(1, max(0, processed / total))
                                    for key, target in (("n_prompt_tokens", "prompt_tokens"), ("n_decoded", "generated_tokens"), ("n_prompt_tokens_cache", "cached_tokens")):
                                        if isinstance(slot.get(key), (int, float)) and slot[key] >= 0:
                                            r[target] = slot[key]
                    except (httpx.HTTPError, ValueError):
                        self.slots = []
                    limit = self.idle_limit()
                    if not self.tickets and (self.deferred_unload or (limit is not None and time.monotonic() - self.last_used >= limit)):
                        await self.unload()
                if time.monotonic() >= resource_at:
                    self.resources = await asyncio.to_thread(self.sample_resources)
                    resource_at = time.monotonic() + 5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"狀態監看暫時失敗：{type(exc).__name__}。")
            await asyncio.sleep(0.35)

    async def watch_vscode(self):
        root = Path(os.environ.get("VSCODE_LOG_ROOT", str(Path(os.environ.get("APPDATA", "")) / "Code/logs")))
        watched = None
        offset = 0
        partial = ""
        discovery_at = 0
        initial = True
        replay_bytes = 16 * 1024

        def discover():
            try:
                candidates = []
                for p in root.rglob("*.log"):
                    if ("agent" in p.name.lower() or "copilot" in str(p).lower()) and p.is_file():
                        s = p.stat()
                        if s.st_mtime > time.time() - 21600 and s.st_size < 64 * 1024**2:
                            candidates.append((s.st_mtime, p))
                return max(candidates, default=(0, None))[1]
            except OSError:
                return None

        while not self.stopping.is_set():
            try:
                if self.config.get("vscode_abort_watch", True) and os.environ.get("VSCODE_ABORT_LOG_WATCH", "1") != "0":
                    if time.monotonic() >= discovery_at:
                        candidate = await asyncio.to_thread(discover)
                        discovery_at = time.monotonic() + 5

                        if candidate != watched:
                            watched = candidate
                            partial = ""

                            if watched:
                                size = watched.stat().st_size

                                # 啟動時不重播舊 Abort，避免舊紀錄取消新任務。
                                # 但 VS Code 執行期間建立/輪替的新 log 要回讀尾端，
                                # 否則 Abort 可能在下一次 discovery 前就已經寫入而被漏掉。
                                if initial:
                                    offset = size
                                else:
                                    offset = max(0, size - replay_bytes)
                            else:
                                offset = 0

                        initial = False

                    if watched:
                        size = watched.stat().st_size

                        if size < offset:
                            # 同一路徑被 truncate / rotate，也回讀少量尾端。
                            offset = max(0, size - replay_bytes)
                            partial = ""

                        if size > offset:
                            with watched.open("rb") as f:
                                f.seek(offset)
                                raw = f.read(64 * 1024)

                            offset += len(raw)

                            lines = (
                                partial + raw.decode("utf-8", errors="replace")
                            ).splitlines(keepends=True)

                            partial = (
                                lines.pop()
                                if lines and not lines[-1].endswith(("\n", "\r"))
                                else ""
                            )

                            for line in lines:
                                matched = next(
                                    (
                                        p
                                        for p in (
                                            "aborting session",
                                            "cancelling session",
                                            "canceling session",
                                            "session aborted",
                                            "abort session",
                                        )
                                        if p in line.lower()
                                    ),
                                    None,
                                )

                                if matched:
                                    ticket = self.active

                                    if (
                                        ticket
                                        and not ticket.done.is_set()
                                        and not ticket.cancel.is_set()
                                    ):
                                        event_time = None

                                        # VS Code logs use local time, e.g.
                                        # 2026-10-08 04:52:27.707 [info] ...
                                        match = re.match(
                                            r"^(\d{4}-\d{2}-\d{2} "
                                            r"\d{2}:\d{2}:\d{2}(?:\.\d+)?)",
                                            line,
                                        )

                                        if match:
                                            try:
                                                event_time = dt.datetime.fromisoformat(
                                                    match.group(1)
                                                )
                                            except ValueError:
                                                pass
            
                                        accept = True

                                        if event_time is not None:
                                            try:
                                                started_utc = dt.datetime.fromisoformat(
                                                    ticket.record["started_at"]
                                                )           

                                                started_local = (
                                                    started_utc
                                                    .astimezone()
                                                    .replace(tzinfo=None)
                                                )

                                                # An Abort written before this request existed
                                                # cannot possibly belong to this request.
                                                if event_time < started_local:
                                                    accept = False

                                                # Tail replay only needs to recover a few seconds
                                                # around log discovery. Reject clearly stale events.
                                                now_local = dt.datetime.now()

                                                if (
                                                    now_local - event_time
                                                    > dt.timedelta(seconds=10)
                                                ):
                                                    accept = False

                                            except (ValueError, TypeError, KeyError):
                                                pass

                                        if accept:
                                            self.log(
                                                f"偵測到 VS Code 停止訊號 "
                                                f"({matched})，取消目前執行中的任務。"
                                            )
                                            self.cancel_ticket(ticket)

            except OSError:
                watched = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log(f"VS Code 停止訊號監看失敗：{type(exc).__name__}。")

            await asyncio.sleep(0.3)


def scan_models(directories):
    models, projectors, seen = [], [], set()
    for directory in directories:
        root = Path(directory)
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not Path(current, d).is_symlink()]
            for filename in files:
                if not filename.lower().endswith(".gguf"):
                    continue
                path = Path(current, filename)
                key = str(path.resolve()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                low = filename.lower()
                if low.startswith("mtp-") or "draft" in low:
                    continue
                shard = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", low)
                if shard and int(shard[1]) != 1:
                    continue
                try:
                    size = path.stat().st_size
                    if shard:
                        base = filename[:shard.start()]
                        size = sum(p.stat().st_size for p in path.parent.glob(base + "-?????-of-" + shard[2] + ".gguf"))
                    entry = {"path": str(path), "name": path.stem, "size_bytes": size}
                    entry.update(inspect_gguf_capabilities(str(path)))
                    (projectors if "mmproj" in low else models).append(entry)
                except OSError:
                    pass
    return {"models": sorted(models, key=lambda m: m["name"].lower()), "projectors": sorted(projectors, key=lambda m: m["name"].lower())}


def create_app(manager: Manager):
    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        yield
        await manager.stop()

    app = FastAPI(title="Local Model Manager", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def authorize(request, call_next):
        # Browser sites cannot mutate a local model manager through CSRF.
        origin = request.headers.get("origin")
        if origin and origin not in (f"http://127.0.0.1:{manager.port}", f"http://localhost:{manager.port}"):
            return JSONResponse({"error": "不允許此網站存取本地管理器。"}, status_code=403)
        if request.url.path.startswith("/manager/"):
            token = request.headers.get("X-Manager-Token", "")
            if not secrets.compare_digest(token, manager.token):
                return JSONResponse({"error": "需要有效的管理權杖。"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({"error": {"message": manager.clean(exc), "type": "invalid_request_error"}}, status_code=400)

    @app.get("/health")
    async def health():
        return {"ok": True, "app": "local-model-manager", "version": VERSION}

    @app.get("/manager/config")
    @app.get("/manager/export")
    async def config():
        return manager.config

    @app.put("/manager/config")
    @app.post("/manager/import")
    async def save_config(request: Request):
        return manager.save_config(await request.json())

    @app.get("/manager/status")
    async def status():
        return manager.status()

    @app.get("/manager/requests")
    async def requests():
        return {"requests": manager.records()}

    @app.post("/manager/records/clear")
    async def clear_records():
        # Keep live tickets intact so this button is safe to use while an
        # Agent request is running. Completed summaries and opt-in body dumps
        # are the only data removed.
        request_count = len(manager.history)
        log_count = len(manager.lines)
        body_count = 0
        body_dir = manager.data / "request-bodies"
        if body_dir.is_dir():
            for path in body_dir.glob("*.json"):
                try:
                    path.unlink()
                    body_count += 1
                except OSError:
                    pass
        manager.history.clear()
        atomic_json(manager.data / "history.json", [])
        manager.lines.clear()
        return {"ok": True, "requests": request_count, "logs": log_count, "request_bodies": body_count}

    @app.get("/manager/logs")
    async def logs():
        return {"lines": list(manager.lines)}

    @app.post("/manager/scan")
    async def scan():
        return await asyncio.to_thread(scan_models, manager.config["model_dirs"])

    @app.post("/manager/model-capabilities")
    async def model_capabilities(request: Request):
        body = await request.json()
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("請指定要檢查的 GGUF 模型路徑。")
        return await asyncio.to_thread(inspect_gguf_capabilities, path)

    @app.post("/manager/load")
    async def load(request: Request):
        body = await request.json()
        manager.begin_load(body.get("model_id", manager.config["default_model_id"]))
        return {"ok": True}

    @app.post("/manager/unload")
    async def unload():
        if manager.tickets:
            manager.deferred_unload = True
            return {"ok": True, "deferred": True}
        if manager.load_task and not manager.load_task.done():
            manager.load_task.cancel()
            await asyncio.gather(manager.load_task, return_exceptions=True)
        await manager.unload()
        return {"ok": True, "deferred": False}

    @app.post("/manager/accepting")
    async def accepting(request: Request):
        value = (await request.json()).get("accepting")
        if not isinstance(value, bool):
            raise ValueError("accepting 必須是布林值。")
        manager.accepting = value
        return {"ok": True, "accepting": value}

    @app.post("/manager/keep-loaded")
    async def keep(request: Request):
        body = await request.json()
        if not isinstance(body.get("keep_loaded"), bool):
            raise ValueError("keep_loaded 必須是布林值。")
        c = copy.deepcopy(manager.config)
        model = next((m for m in c["models"] if m["id"] == body.get("model_id")), None)
        if not model:
            raise ValueError("找不到指定模型。")
        model["keep_loaded"] = body["keep_loaded"]
        manager.save_config(c)
        return {"ok": True}

    @app.post("/manager/requests/{ident}/cancel")
    async def cancel(ident: str):
        ticket = manager.tickets.get(ident)
        if ticket:
            manager.cancel_ticket(ticket)
        elif not any(r["id"] == ident for r in manager.history):
            raise HTTPException(404, "找不到此任務。")
        return {"ok": True}

    @app.post("/manager/shutdown")
    async def shutdown():
        manager.accepting = False
        for ticket in list(manager.tickets.values()):
            manager.cancel_ticket(ticket)
        async def exit_later():
            await asyncio.sleep(0.1)
            await manager.stop()
            if manager.exit_callback:
                manager.exit_callback()
        asyncio.create_task(exit_later())
        return {"ok": True}

    @app.get("/manager/connection")
    async def connection():
        exists = Path(manager.engine_command()[0]).is_file()
        occupied = not free_port(manager.engine_port_override or manager.config["engine_port"])
        return {"ok": exists and (not occupied or manager.process is not None),
                "api_url": f"http://127.0.0.1:{manager.port}/v1/chat/completions",
                "models": model_list()["data"], "engine_exists": exists, "python_ok": True,
                "port_status": "引擎由本管理器執行" if manager.process else ("引擎連接埠已被其他程序使用" if occupied else "可用")}

    @app.get("/manager/vscode/preview")
    async def vscode_preview():
        import vscode_integration
        return vscode_integration.preview(manager.config, manager.data)

    @app.post("/manager/vscode/apply")
    async def vscode_apply():
        import vscode_integration
        return await asyncio.to_thread(vscode_integration.apply, copy.deepcopy(manager.config), manager.data)

    def model_list():
        rows = []
        for m in manager.config["models"]:
            for ident in [m["id"]] + [m["id"] + "::" + p["id"] for p in manager.config["profiles"]]:
                rows.append({"id": ident, "object": "model", "created": 0, "owned_by": "local-model-manager"})
        return {"object": "list", "data": rows}

    @app.get("/v1/models")
    async def models():
        return model_list()

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "請求超過 32 MiB。")
        try:
            body = json.loads(raw)
        except ValueError:
            raise ValueError("請求不是有效 JSON。")
        ticket = manager.submit(body, dict(request.headers))
        async def watch_disconnect():
            while not ticket.done.is_set():
                try:
                    # Once the body has been consumed, receive the next ASGI
                    # event directly. Starlette's non-blocking helper can
                    # miss a disconnect that arrives just after its cancelled
                    # receive scope; a bounded receive makes pre-header
                    # cancellation deterministic without blocking the worker.
                    message = await asyncio.wait_for(request.receive(), 0.25)
                    disconnected = message.get("type") == "http.disconnect"
                except asyncio.TimeoutError:
                    disconnected = False
                if disconnected:
                    manager.cancel_ticket(ticket)
                    return
                await asyncio.sleep(0.1)
        watcher = asyncio.create_task(watch_disconnect())
        async def stop_watcher():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        try:
            code, content, streaming = await asyncio.shield(ticket.response)
        except asyncio.CancelledError:
            manager.cancel_ticket(ticket)
            await stop_watcher()
            raise
        headers = {"X-Conversation-Id": ticket.record["id"], "X-Manager-Request-Id": ticket.record["id"]}
        if not streaming:
            await stop_watcher()
            if isinstance(content, bytes):
                return Response(content, status_code=code, headers=headers, media_type="application/json")
            return JSONResponse(content, status_code=code, headers=headers)
        async def stream():
            try:
                while not ticket.done.is_set() or not ticket.output.empty():
                    try:
                        chunk = await asyncio.wait_for(ticket.output.get(), 0.2)
                        yield chunk
                    except asyncio.TimeoutError:
                        pass
            finally:
                if not ticket.done.is_set():
                    manager.cancel_ticket(ticket)
                await stop_watcher()
        headers["Cache-Control"] = "no-cache"
        headers["X-Accel-Buffering"] = "no"
        return StreamingResponse(stream(), status_code=code, headers=headers, media_type="text/event-stream")

    return app


def main():
    parser = argparse.ArgumentParser(description="Local Model Manager loopback service")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "LocalModelManager")
    parser.add_argument("--port", type=int)
    parser.add_argument("--engine-port", type=int)
    args = parser.parse_args()
    manager = Manager(args.data_dir, args.port, args.engine_port)
    app = create_app(manager)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=manager.port, access_log=False, log_level="warning", timeout_graceful_shutdown=8))
    manager.exit_callback = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
