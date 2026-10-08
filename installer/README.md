# 安裝、可攜版與解除安裝

AMIEBL 支援 Windows x64 的 **Portable 資料夾**與 **Inno Setup 6 EXE 安裝程式**。目前沒有 MSI 建置流程。

## Portable

package.ps1 會建立獨立的 portable 資料夾。完整複製到另一台電腦後，可直接執行 LocalModelManager.exe。

程式根目錄含有 portable.flag 時，設定、日誌、備份與私有 Python venv 儲存在 Portable 目錄下的 data 資料夾。你可以直接在 GUI 指定其他磁碟上的 GGUF 模型，不必把模型複製進 Portable。

需要使用者自行準備相容的 llama.cpp Windows 執行檔。發行包是否需要 Python/.NET 額外安裝，取決於是否包含相應 runtime：

- **-PythonHome**：把 Python 執行環境打包；未包含且本機沒有可用 Python／依賴時，首次啟動需安裝相依套件，可能需要網路。
- **-SelfContained**：把 .NET runtime 納入發行包；建置機需有對應 runtime pack。
- **-ModelDirectory**：只對 Portable 有效；會把指定的 GGUF 檔案複製進 Portable 的 models 資料夾。

~~~powershell
.\package.ps1 -LlamaRoot 'C:\Tools\llama.cpp' -PythonHome 'C:\Python314' -SelfContained -OutputDirectory 'C:\AMIEBL-release-v1'
~~~

**打包器不會覆蓋既有的 portable 輸出資料夾**，以免清除其中的使用者設定、模型或紀錄。重建時請指定新的 -OutputDirectory。

## EXE 安裝版（Inno Setup）

需要先安裝 Inno Setup 6，然後在 package.ps1 指令加上 -BuildInstaller：

~~~powershell
.\package.ps1 -LlamaRoot 'C:\Tools\llama.cpp' -PythonHome 'C:\Python314' -SelfContained -BuildInstaller -OutputDirectory 'C:\AMIEBL-setup-v1'
~~~

安裝程式輸出：OutputDirectory\installer\LocalModelManager-Setup.exe。

- 安裝預設路徑為 %LOCALAPPDATA%\Programs\LocalModelManager，使用者資料放在 %LOCALAPPDATA%\LocalModelManager。
- 不會安裝 portable.flag、Portable 的 data，也**不會把 models 目錄打包到安裝位置**；請透過模型庫選擇 GGUF 所在資料夾。
- 安裝時**不會**建立開機啟動項目；只有使用者於系統設定明確啟用時才會啟動。
- 一般解除安裝只移除安裝程式追蹤的程式檔案、捷徑與屬於該安裝位置的開機啟動設定；**不刪除** %LOCALAPPDATA%\LocalModelManager 中的設定、任務紀錄及備份，也不遞迴刪除其他未知檔案。
- 從舊版安裝升級時，若舊安裝目錄存在 data，但新使用者資料位置還不存在，首次啟動會將舊資料複製到新位置，保留原始 data 作為備份。若新位置已存在，為避免覆寫，不會自動合併；請自行核對資料。

新版 Inno Setup 設定 `UninstallLogMode=overwrite`，以避免將舊安裝程式的危險 `UninstallDelete` 清理指令累加到新版解除安裝紀錄。副作用是舊版未再打包的程式檔案可能殘留，應在確認內容後另外清理，不能以遞迴刪除整個安裝目錄取代。

**既有舊版安裝使用者**：由於較早的 Inno Setup 安裝程式含有危險的解除安裝資料清理設定，升級前請先手動備份舊安裝目錄的 data 與 %LOCALAPPDATA%\LocalModelManager。不要先使用舊安裝程式解除安裝，確認新版在測試環境的升級及解除安裝流程後再替換正式使用中的版本。

## PowerShell 使用者層級安裝

不使用 Inno Setup 時，可從 Portable 根目錄執行 Install-LocalModelManager.cmd。此方法會：

- 將應用程式安裝到 %LOCALAPPDATA%\Programs\LocalModelManager，不刪除既有目錄，並拒絕覆蓋不屬於該安裝的 Portable 位置。
- 保留舊版安裝中的資料、模型及其他非程式檔案。
- 不複製 Portable 的 data、models 或 portable.flag，使用 installed.flag 標記安裝模式。
- 產生記載已安裝檔案 SHA-256 的 installed-files.json，供安全解除安裝。
- **不主動設定登入自啟**。

解除安裝時，可以從 Windows 已安裝的應用程式啟動，或執行：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\installer\Uninstall-LocalModelManager.ps1 -InstallDirectory "$env:LOCALAPPDATA\Programs\LocalModelManager"
~~~

預設保留所有使用者資料，並且**僅刪除雜湊與安裝清單相符的程式檔案**。被修改的檔案、非安裝器產生的檔案、data 及 models 都會保留。舊版未建立安全清單的安裝，解除安裝僅會取消登錄並保留目錄，避免不明內容被誤刪。

只有在你已備份資料，而且確定要另外刪除 %LOCALAPPDATA%\LocalModelManager 時，才手動加上 -DeleteUserData。此參數**不會**由正常解除安裝自動啟用；歷史的 -KeepData 仍可使用，但保留資料已是預設行為。

## 測試與注意事項

- 建議所有打包與升級測試使用全新的輸出目錄、隔離資料目錄及測試模型，不要直接對現有實際資料執行解除安裝。
- WinUI 3 建置與隔離 smoke test 詳見 [DESKTOP-ARCHITECTURE.md](../DESKTOP-ARCHITECTURE.md)。
- 上述為打包腳本的預期行為；發布前仍須在乾淨的 Windows x64 測試機上實際驗證安裝、首次啟動、升級、解除安裝及資料保留。
