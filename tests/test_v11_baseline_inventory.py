"""Frozen v1.0.0 API/UI inventory for the native v1.1.0 rewrite.

This test protects the reference implementation while a feature-for-feature
port is developed. Passing this test does NOT imply Rust/C++ parity; native
integration and GUI acceptance gates live in migration/v1.1.0/PARITY_CONTRACT.md.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    ("GET", "/health"),
    ("GET", "/manager/config"),
    ("GET", "/manager/export"),
    ("PUT", "/manager/config"),
    ("POST", "/manager/import"),
    ("GET", "/manager/status"),
    ("GET", "/manager/requests"),
    ("POST", "/manager/records/clear"),
    ("GET", "/manager/logs"),
    ("POST", "/manager/scan"),
    ("POST", "/manager/model-capabilities"),
    ("POST", "/manager/load"),
    ("POST", "/manager/unload"),
    ("POST", "/manager/accepting"),
    ("POST", "/manager/keep-loaded"),
    ("POST", "/manager/requests/{ident}/cancel"),
    ("POST", "/manager/shutdown"),
    ("GET", "/manager/connection"),
    ("GET", "/manager/vscode/preview"),
    ("POST", "/manager/vscode/apply"),
    ("GET", "/v1/models"),
    ("POST", "/v1/chat/completions"),
}
PAGES = ("總覽", "模型庫", "使用模式", "任務與紀錄", "系統")


class FrozenV100ContractTests(unittest.TestCase):
    def test_all_legacy_http_routes_are_accounted_for(self):
        src = (ROOT / "backend/manager.py").read_text(encoding="utf-8-sig")
        actual = {
            (verb.upper(), path)
            for verb, path in re.findall(
                r'@app\.(get|post|put|delete)\("([^"]+)"\)', src
            )
        }
        self.assertEqual(actual, ROUTES, "Update acceptance inventory before changing an endpoint")

    def test_legacy_winui3_pages_and_order_are_recorded(self):
        src = (ROOT / "desktop-winui/MainWindow.xaml").read_text(encoding="utf-8-sig")
        found = tuple(re.findall(r'<NavigationViewItem\s+Content="([^"]+)"', src))
        self.assertEqual(found, PAGES)

    def test_native_migration_contract_is_present(self):
        contract = (ROOT / "migration/v1.1.0/PARITY_CONTRACT.md").read_text(encoding="utf-8")
        for marker in (
            "Zero-Regression Native Migration Contract",
            "SmoothExpander",
            "client disconnect",
            "Windows App SDK",
            "Rust parity",
            "C++/WinRT parity",
            "No hidden fallback",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contract)


if __name__ == "__main__":
    unittest.main()
