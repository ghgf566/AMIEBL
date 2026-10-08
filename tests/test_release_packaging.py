"""Static release guardrails; Windows installer behavior requires acceptance tests."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


class ReleasePackagingSafetyTests(unittest.TestCase):
    def test_inno_does_not_install_portable_data_or_delete_user_data(self):
        iss = source("installer/LocalModelManager.iss")
        self.assertNotIn("[Registry]", iss)
        self.assertNotRegex(iss, r"(?i)Type:\s*filesandordirs")
        self.assertNotIn('"{localappdata}\\LocalModelManager"', iss)
        self.assertIn(r"\portable.flag", iss)
        self.assertIn(r"\data\*", iss)
        self.assertIn(r"\models\*", iss)
        self.assertIn("installed.flag", iss)
        self.assertIn("CurUninstallStepChanged", iss)
        self.assertIn("RegQueryStringValue", iss)

    def test_manual_installer_never_purges_install_directory(self):
        install = source("installer/Install-LocalModelManager.ps1")
        self.assertNotRegex(install, r"(?im)Remove-Item\s+-LiteralPath\s+\$target")
        self.assertIn("/XD (Join-Path $source 'data') (Join-Path $source 'models')", install)
        self.assertIn("installed.flag", install)
        self.assertIn("installed-files.json", install)
        self.assertIn("Get-FileHash", install)
        self.assertNotIn("CurrentVersion\\Run", install)

    def test_manual_uninstaller_preserves_data_and_only_removes_known_files(self):
        uninstall = source("installer/Uninstall-LocalModelManager.ps1")
        self.assertIn("[switch]$DeleteUserData", uninstall)
        self.assertIn("if ($DeleteUserData)", uninstall)
        self.assertIn("installed-files.json", uninstall)
        self.assertIn("Get-FileHash", uninstall)
        self.assertIn("StartsWith($rootPrefix", uninstall)
        self.assertNotRegex(uninstall, r"(?i)Remove-Item\s+-LiteralPath\s+\$target\s+-Recurse")
        self.assertIn("Write-Warning '舊版安裝沒有安全的檔案清單", uninstall)

    def test_portable_build_does_not_destroy_existing_output(self):
        package = source("package.ps1")
        check = package.split("if (Test-Path -LiteralPath $portable)", 1)[1].split(
            "New-Item -ItemType Directory", 1
        )[0]
        self.assertIn("throw", check)
        self.assertNotIn("Remove-Item", check)

    def test_installed_mode_overrides_old_portable_marker(self):
        host = source("desktop-platform/BackendHost.cs")
        self.assertIn('File.Exists(Path.Combine(applicationDir, "installed.flag"))', host)
        self.assertIn('File.Exists(Path.Combine(applicationDir, "portable.flag")) && !installed', host)
        self.assertIn("CopyLegacyInstalledData", host)
        self.assertIn("Directory.Move(staging, destination)", host)


if __name__ == "__main__":
    unittest.main()
