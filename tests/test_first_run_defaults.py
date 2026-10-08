"""First-run defaults must be usable without a developer's model drive."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("amiebl_first_run", Path(__file__).resolve().parents[1] / "backend" / "manager.py")
manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class FirstRunDefaultsTests(unittest.TestCase):
    def test_empty_model_library_has_no_invented_model_and_keeps_profiles(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LMM_MODEL_DIR": root, "USERPROFILE": root}, clear=True):
            config = manager.validate_config(manager.default_config())
            self.assertEqual(config["models"], [])
            self.assertEqual(config["default_model_id"], "")
            self.assertEqual(len(config["profiles"]), 3)
            self.assertFalse(config["auto_start"])
            self.assertFalse(config["preload"])

    def test_explicit_model_root_excludes_projectors_and_drafts(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LMM_MODEL_DIR": root, "USERPROFILE": root}, clear=True):
            for name in ("example.gguf", "mmproj-F16.gguf", "draft.gguf"):
                Path(root, name).touch()
            config = manager.default_config()
            model = config["models"][0]
            self.assertEqual(model["path"], str(Path(root, "example.gguf")))
            self.assertEqual(model["gpu_layers"], 0)
            self.assertFalse(model["mtp"])
            self.assertEqual(model["mmproj"], "")

    def test_existing_user_model_and_profile_are_preserved(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LMM_MODEL_DIR": root, "USERPROFILE": root}, clear=True):
            Path(root, "existing.gguf").touch()
            config = manager.default_config()
            config["models"][0].update(id="my-model", gpu_layers=17, mtp=True)
            config["default_model_id"] = "my-model"
            normalized = manager.validate_config(config)
            self.assertEqual(normalized["default_model_id"], "my-model")
            self.assertEqual(normalized["models"][0]["gpu_layers"], 17)
            self.assertTrue(normalized["models"][0]["mtp"])
