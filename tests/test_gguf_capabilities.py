from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("amiebl_manager_caps", ROOT / "backend" / "manager.py")
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(manager)


def gguf_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def write_fixture(path: Path, *, nextn_layers: int | None, nextn_tensor: bool) -> None:
    kv = []
    kv.append(gguf_string("general.architecture") + struct.pack("<I", 8) + gguf_string("qwen35"))
    if nextn_layers is not None:
        kv.append(
            gguf_string("qwen35.nextn_predict_layers")
            + struct.pack("<I", 4)
            + struct.pack("<I", nextn_layers)
        )

    tensor_name = "blk.24.nextn.eh_proj.weight" if nextn_tensor else "blk.0.attn_norm.weight"
    tensor = (
        gguf_string(tensor_name)
        + struct.pack("<I", 1)
        + struct.pack("<q", 1)
        + struct.pack("<I", 0)
        + struct.pack("<Q", 0)
    )

    payload = (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", 1)
        + struct.pack("<Q", len(kv))
        + b"".join(kv)
        + tensor
    )
    path.write_bytes(payload)


class GgufCapabilityTests(unittest.TestCase):
    def test_detects_native_mtp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mtp.gguf"
            write_fixture(path, nextn_layers=1, nextn_tensor=True)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "available")
            self.assertEqual(result["mtp_layers"], 1)
            self.assertEqual(result["gguf_architecture"], "qwen35")
            self.assertEqual(result["mtp_tensor_count"], 1)

    def test_rejects_model_without_mtp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.gguf"
            write_fixture(path, nextn_layers=None, nextn_tensor=False)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "unavailable")
            self.assertEqual(result["mtp_layers"], 0)

    def test_marks_declared_but_missing_mtp_as_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.gguf"
            write_fixture(path, nextn_layers=1, nextn_tensor=False)
            result = manager.inspect_gguf_capabilities(str(path))
            self.assertEqual(result["mtp_capability"], "incomplete")


if __name__ == "__main__":
    unittest.main()
