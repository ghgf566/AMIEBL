# 安裝與解除安裝

`package.ps1` 會先產生 `portable` 資料夾。這個資料夾可以直接複製到另一台 Windows x64 電腦，執行 `LocalModelManager.exe` 即可；設定、記錄與首次建立的 venv 都放在 bundle 下的 `data`。若沒有包入 Python，程式第一次啟動會建立私有 venv 並安裝 `backend\requirements.txt`；若要完全離線使用，請在打包時指定 `-PythonHome`。若目標機器沒有 .NET 10 Desktop Runtime，打包時加入 `-SelfContained`（建置機需要已快取對應 runtime pack）。

要建立完整 portable bundle：

```powershell
.\package.ps1 -LlamaRoot 'C:\Users\kissi\llama.cpp' -PythonHome 'C:\Users\kissi\AppData\Local\Programs\Python\Python314' -SelfContained
```

若也要把模型放進 bundle，加入 `-ModelDirectory 'D:\model'`。模型通常很大，因此預設不複製；使用者仍可在 GUI 的系統頁設定任意模型資料夾。

若已安裝 Inno Setup 6，可追加 `-BuildInstaller`，在 `installer` 輸出 `LocalModelManager-Setup.exe`。安裝程式建立開始功能表捷徑與 Windows「已安裝的應用程式」項目，解除安裝時會停止本程式及其子程序、移除登入啟動項與捷徑、刪除安裝目錄及管理器資料。要保留設定與虛擬環境，可手動執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Uninstall-LocalModelManager.ps1 -InstallDirectory "$env:LOCALAPPDATA\Programs\LocalModelManager" -KeepData
```

沒有 Inno Setup 時，直接雙擊 portable 根目錄的 `Install-LocalModelManager.cmd` 也會完成使用者層級安裝並建立解除安裝項目。
