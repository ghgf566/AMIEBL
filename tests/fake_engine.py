"""Small local HTTP engine used only by the manager integration tests.

It understands llama-server's --host/--port arguments and never opens a GGUF.
Every request is journaled under the isolated test directory for assertions.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8081)
args, unknown = parser.parse_known_args()
EVENTS = Path(os.environ["LMM_FAKE_EVENTS"])
LOCK = threading.Lock()
ACTIVE: dict[str, threading.Event] = {}
CONTROL: dict[str, object] = {"classifier": "normal", "classifier_delay": 0}
STARTED = time.monotonic()
READY_DELAY_FILE = os.environ.get("LMM_FAKE_READY_DELAY_FILE", "")
READY_DELAY = float(Path(READY_DELAY_FILE).read_text()) if READY_DELAY_FILE and Path(READY_DELAY_FILE).exists() else 0


def event(kind: str, **values: object) -> None:
    with LOCK:
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "pid": os.getpid(), "time": time.time(), **values}) + "\n")


def text_of(body: dict) -> str:
    return "\n".join(str(x.get("content", "")) for x in body.get("messages", []))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: object) -> None:
        pass

    def read_body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        return json.loads(raw or "{}")

    def json_response(self, body: object, status: int = 200) -> None:
        raw = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def sse(self, value: object, fragmented: bool = False) -> None:
        raw = ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode("utf-8")
        if not fragmented:
            self.wfile.write(raw)
            self.wfile.flush()
            return
        # Network chunks deliberately split frame delimiters and UTF-8 code points.
        for start in range(0, len(raw), 7):
            self.wfile.write(raw[start:start + 7])
            self.wfile.flush()
            time.sleep(0.001)

    def do_GET(self) -> None:
        event("get", path=self.path)
        if self.path.startswith("/health"):
            if time.monotonic() - STARTED < READY_DELAY:
                self.json_response({"error": {"message": "Loading model", "type": "unavailable_error", "code": 503}}, 503)
            else:
                self.json_response({"status": "ok"})
        elif self.path.startswith("/slots"):
            self.json_response([{"id": 0, "state": 2 if ACTIVE else 0, "is_processing": bool(ACTIVE), "n_decoded": 2, "n_prompt_tokens": 12, "n_prompt_tokens_processed": 12}])
        elif self.path == "/__control":
            self.json_response(CONTROL)
        elif self.path.startswith("/props"):
            props_file = os.environ.get("LMM_FAKE_PROPS_FILE")
            self.json_response(json.loads(Path(props_file).read_text(encoding="utf-8")) if props_file and Path(props_file).exists() else CONTROL.get("props", {"total_slots": 1, "build_info": "fake-test-only", "default_generation_settings": {"n_ctx": 4096}}))
        else:
            self.json_response({"error": "fake route unavailable"}, 404)

    def do_DELETE(self) -> None:
        event("delete", path=self.path)
        for stop in list(ACTIVE.values()):
            stop.set()
        self.json_response({"cancelled": True})

    def do_POST(self) -> None:
        body = self.read_body()
        if self.path == "/__control":
            CONTROL.update(body)
            self.json_response(CONTROL)
            return
        conv = self.headers.get("X-Conversation-Id", "")
        event("post", path=self.path, body=body, conversation=conv)
        if self.path != "/v1/chat/completions":
            self.json_response({"error": "fake route unavailable"}, 404)
            return
        prompt = text_of(body)
        # The manager's internal classification request deliberately has no user tools.
        classifier = bool(body.get("response_format")) or ("classif" in conv.lower()) or any(
            term in str(body.get("messages", [{}])[0].get("content", "")).lower()
            for term in ("thinking policy", "reasoning router", "classify", "分類", "classifier", "thinking decision")
        )
        stop = threading.Event()
        ACTIVE[conv or str(threading.get_ident())] = stop
        key = conv or str(threading.get_ident())
        try:
            if classifier:
                event("classifier", body=body)
                if stop.wait(float(CONTROL.get("classifier_delay", 0))):
                    return
                mode = CONTROL["classifier"]
                if mode == "error":
                    self.json_response({"error": {"message": "fake classifier failure"}}, 500)
                    return
                decision = CONTROL.get("decision", {"thinking": True, "enable_thinking": True, "effort": "medium", "reasoning_effort": "medium", "budget": 384, "thinking_budget": 384, "reason": "test decision"})
                content = "{ broken" if mode == "invalid" else json.dumps(decision)
                self.json_response({"id": "fake-classifier", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
                return
            if "FAKE_PREHEADER" in prompt:
                for _ in range(150):
                    if stop.wait(0.05):
                        break
            if "FAKE_ENGINE_ERROR" in prompt:
                self.json_response({"error": {"message": "fake engine failure"}}, 500)
                return
            message = {"role": "assistant", "content": "fake answer", "reasoning_content": "fake reasoning", "tool_calls": [{"id": "call_test", "type": "function", "function": {"name": "example", "arguments": '{"value":7}'}}]}
            usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 3}}
            timings = {"prompt_n": 12, "prompt_ms": 10, "prompt_per_second": 1200, "predicted_n": 8, "predicted_ms": 400, "predicted_per_second": 20}
            if not body.get("stream"):
                self.json_response({"id": "fake-completion", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}], "usage": usage, "timings": timings})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            deltas = [{"role": "assistant"}, {"reasoning_content": "fake reasoning"}, {"content": "fake answer"}, {"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}]
            fragmented = "FAKE_FRAGMENTED" in prompt
            if fragmented:
                deltas = [{"role": "assistant"}, {"reasoning_content": "分析中"}, {"content": "答案"},
                          {"tool_calls": [{"index": 0, "id": "call_test", "type": "function", "function": {"name": "example", "arguments": '{"value":"'}}]},
                          {"tool_calls": [{"index": 0, "function": {"arguments": '中文"}'}}]}]
            if "FAKE_SLOW" in prompt:
                deltas.extend([{"content": "."}] * 200)
            for delta in deltas:
                if stop.is_set():
                    break
                chunk = {"id": "fake-completion", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                self.sse(chunk, fragmented)
                if stop.wait(0.04):
                    break
            if not stop.is_set():
                final = {"id": "fake-completion", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": usage, "timings": timings}
                if fragmented:
                    self.sse({"id": "fake-completion", "choices": final["choices"]}, True)
                    final["choices"] = []  # OpenAI's usage-only terminal event.
                self.sse(final, fragmented)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            event("disconnected", conversation=conv)
        finally:
            ACTIVE.pop(key, None)
            event("finished", conversation=conv, cancelled=stop.is_set())


event("start", argv=unknown, port=args.port)
ThreadingHTTPServer((args.host, args.port), Handler).serve_forever(poll_interval=0.1)
