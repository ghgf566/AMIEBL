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
        self.assertIn("UninstallLogMode=overwrite", iss)
        self.assertIn("PrepareToInstall", iss)
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

    def test_apache_license_is_bundled_in_every_distribution(self):
        license_text = source("LICENSE")
        self.assertIn("Apache License", license_text[:100])
        self.assertIn("Version 2.0, January 2004", license_text[:100])
        for path in ("build-winui.ps1", "build.ps1", "package.ps1"):
            self.assertIn("'LICENSE'", source(path), f"{path} must distribute the license")
        self.assertIn("LICENSE", source("README.md"))
        self.assertIn("LICENSE", source("tests/Test-ReleasePackaging.ps1"))

    def test_end_to_end_installer_test_requires_disposable_ci_runner(self):
        test = source("tests/Test-ReleasePackaging.ps1")
        self.assertIn("$env:CI -ne 'true'", test)
        self.assertIn("$env:GITHUB_ACTIONS -ne 'true'", test)
        self.assertIn("Manual uninstall deleted user file", test)
        self.assertIn("Inno uninstaller deleted user file", test)

    def test_release_ci_executes_windows_regression_and_packaging(self):
        ci = source(".github/workflows/release-tests.yml")
        self.assertIn("windows-latest", ci)
        self.assertIn("test_*.py", ci)
        self.assertIn("tests/DesktopPlatformRegression", ci)
        self.assertIn("Test-ReleasePackaging.ps1", ci)
        self.assertIn("-BuildInstaller", ci)

    def test_installed_mode_overrides_old_portable_marker(self):
        host = source("desktop-platform/BackendHost.cs")
        self.assertIn('File.Exists(Path.Combine(applicationDir, "installed.flag"))', host)
        self.assertIn('File.Exists(Path.Combine(applicationDir, "portable.flag")) && !installed', host)
        self.assertIn("CopyLegacyInstalledData", host)
        self.assertIn("Directory.Move(staging, destination)", host)


if __name__ == "__main__":
    unittest.main()
