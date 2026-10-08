# AMIEBL

**A Mindless Inference Engine Built on Llama.cpp**

**一個基於 [llama.cpp](https://github.com/ggml-org/llama.cpp) 打造的無腦推理引擎。**

AMIEBL 是專為 **Windows 本地 AI 模型與 VS Code Agent** 使用情境設計的桌面管理工具。它整合 GGUF 模型管理、推理參數、使用模式（Profile）、OpenAI-compatible API Proxy 與執行狀態監控，希望讓使用者不必先熟悉繁瑣的部署指令，也能開始使用本地 AI Agent。

> **設計理念：讓複雜的事情留在介面背後。**
> 本專案不是另一套模型推理核心；模型運算仍由 llama.cpp 負責，AMIEBL 則負責部署、設定、串接與觀察。

**目前版本：** 1.0.0（正式 Release 候選驗收中）
**作者：** Mr. Chen
**平台：** Windows x64（Windows 10 2004+；建議 Windows 11）
**桌面介面：** WinUI 3 / Windows App SDK / .NET 10
**授權：** [Apache License 2.0](LICENSE)

[Releases](https://github.com/ghgf566/AMIEBL/releases) · [使用說明](使用說明.md) · [架構與驗證](DESKTOP-ARCHITECTURE.md) · [安全性政策](SECURITY.md)

## 為什麼開發 AMIEBL？

Ollama 重視快速上手，LM Studio 提供完整的圖形化本地模型體驗，原生 llama.cpp 則提供豐富的底層參數。

如果希望使用 **llama.cpp + VS Code Agent**，卻不想反覆處理 API 端點、模型切換、GPU 記憶體分配、工具呼叫相容性與參數管理，AMIEBL 可以把這些工作集中在熟悉的 Windows 桌面介面中。

開發的起點，是讓本地 Agent 的**部署、使用與除錯更加容易**，而非要求每個使用者先成為推理引擎專家。

## 功能一覽

### Windows 原生介面

使用 WinUI 3，介面以繁體中文為主，採用卡片、導覽列、滑桿、可收合的進階設定，以及操作說明。現有五個主要頁面：

| 頁面 | 用途 |
| --- | --- |
| **總覽** | 查看引擎狀態、載入／卸載模型、資源用量、佇列及 VS Code 同步入口 |
| **模型庫** | 登錄／掃描 GGUF、管理模型位置、Context、GPU、KV Cache、MTP、視覺及採樣設定 |
| **使用模式** | 建立、複製與管理 Profile，配置思考策略、生成上限及 Agent 指示 |
| **任務與紀錄** | 查詢請求階段、耗時、Token 統計、錯誤與服務日誌 |
| **系統** | 設定背景執行、閒置卸載、服務位置、設定匯入／匯出與連線檢查 |

### GGUF 模型與效能設定

- 匯入 GGUF，搜尋模型資料夾，偵測模型與 projector。
- 調整 **Context、CPU 執行緒、GPU Offload**，或使用 GPU 層數自動估算／顯存預留。
- 選擇 KV Cache 格式（`f16`、`q8_0`、`q4_0`）。
- 調整 Temperature、Top P、Top K、Min P。
- 為相容模型設定視覺 projector（mmproj）、MTP／外部 Draft 模型。
- 顯示已保存設定與已載入引擎設定的差異；需重新載入的選項不會冒充立即生效。

**實際支援程度取決於 GGUF、llama.cpp 版本及硬體**。並非所有模型都能使用 MTP、視覺或相同的推理控制。

### Profile：不同任務，不同使用方式

內建 **Quick Chat、Coding、Deep Coding** 使用模式，也能建立自訂 Profile。

每個 Profile 可設定思考策略、抽象思考強度、思考 Token 預算、整次生成上限，以及 VS Code Agent 的工具、描述和指示。模型與 Profile 是分開管理的，同一個實體模型可以服務不同的任務情境。

AMIEBL 會根據 GGUF Chat Template、模型 metadata，以及模型載入後可取得的 llama.cpp 資訊，嘗試判斷 Thinking 開關、原生 Reasoning Effort 與 Budget 支援，避免只依模型名稱猜測。這套能力偵測機制涵蓋 Qwen、DeepSeek、GLM、Gemma 等系列的不同模板情況。

> **思考控制並非保證。** Light / Balanced / Deep / Extreme 是跨模型的抽象設定，不一定等於原生模型的 effort 名稱；自訂思考 Token 上限是盡力傳遞給引擎，能否強制截斷取決於 llama.cpp 的解析及模型支援。

### VS Code Agent 同步

透過總覽的 **「同步至 VS Code」**，AMIEBL 可協助：

1. 將已登錄的**實體模型**同步至 VS Code 模型選擇器。
2. 為 Profile 建立或更新 `.agent.md` 檔案，以 `AMIEBL_PROFILE:<id>` 標記選擇模式。
3. 依模型 Context 配置 VS Code 的輸入／輸出 Token 額度。
4. 在寫入前備份受影響的 VS Code 設定及 Agent 檔案，盡量保留使用者的其他 Provider 與手動內容。

同步後請**重新載入 VS Code 視窗**。在 Agent 模式下選擇本地模型及對應的使用模式。如果看不到自訂模型，請確認 VS Code 的 `chat.agentHost.byokModels.enabled` 已開啟。

### API Proxy、模型按需載入與任務紀錄

AMIEBL 提供本機 OpenAI-compatible API，由後端協調 llama.cpp 的模型載入、請求排隊、Profile 套用及執行監控。

預設 API：

```text
http://127.0.0.1:8080/v1/chat/completions
```

透過管理器可查看排隊、載入、提示詞處理、思考、生成、取消與錯誤等階段，以及可取得的 Token／速度資料。無法取得的指標不會以虛構數字代替。

目前採用**一次一個已載入模型、單一推理 Slot** 的設計。可設定全域閒置卸載時間，並透過系統匣控制背景服務；關閉主視窗不一定代表服務已結束。

## 下載與快速開始

### 下載

請查看 [GitHub Releases](https://github.com/ghgf566/AMIEBL/releases)。首次正式版本尚在準備中，Release 資產發布後才會在該頁提供下載。

v1.0.0 候選產物提供兩種發行方式（通過正式發布確認後上傳）：

| 形式 | 說明 |
| --- | --- |
| **Portable** | 解壓縮後執行 `LocalModelManager.exe`；設定及執行資料可保存在發行包的 `data` 目錄 |
| **Setup.exe** | 使用 Inno Setup 製作的 Windows 使用者層級安裝程式；程式與使用者資料分開保存 |

檔名為 `AMIEBL-v1.0.0-win-x64-portable.zip` 與 `AMIEBL-v1.0.0-win-x64-setup.exe`。

**目前沒有 MSI 打包流程。** 預設發行包不附 GGUF 模型、個人設定或 API 金鑰。候選包內含 llama.cpp b11232（CPU / CUDA 12.4）、Python 3.14.7 與後端套件、.NET 10 自包含 runtime 及 Windows App SDK；不需另行安裝 Python 或 .NET，NVIDIA GPU 仍需相容驅動。首次啟動不需下載後端套件。第三方元件適用 [各自授權](THIRD-PARTY-NOTICES.md)。

### 執行環境

- Windows 10 2004+ x64 或 Windows 11 x64。
- 可用的 Windows llama.cpp 推理執行檔，以及相容的 GGUF 模型。
- 足夠的 RAM／VRAM（取決於模型、量化、Context 與 Offload 設定）。
- 未包含 .NET Runtime 的發行包需安裝 **.NET 10 Windows Desktop Runtime**。
- 未包含 Python 執行環境的發行包需有可用的 **Python 3.11+**；如果後端套件尚未安裝，首次啟動可能需要網路下載 FastAPI、httpx 與 uvicorn。

安裝程式不應擅自啟用 Windows 登入自動啟動；可在 AMIEBL「系統」頁自行設定。

### 使用步驟

1. 解壓縮 Portable 或執行 Setup.exe，開啟 `LocalModelManager.exe`。
2. 進入**模型庫**，選擇 GGUF 檔案或掃描模型資料夾。
3. 配合硬體資源調整 Context、GPU Offload、KV Cache 等參數。
4. 進入**使用模式**，選取或建立適合任務的 Profile。
5. 在**總覽**按下「同步至 VS Code」，確認同步預覽。
6. 重新載入 VS Code，在 Agent 模式選擇本地模型和 Agent，開始使用。

完整說明請參考 [使用說明.md](使用說明.md)。

## 設定、資料與隱私

- **Portable：** 發行包具有 `portable.flag` 時，預設使用同層的 `data/`。
- **安裝版：** 預設使用 `%LOCALAPPDATA%\LocalModelManager`。
- 可使用 `--data-dir` 指定隔離的資料目錄，適用於測試。
- 一般任務紀錄不預設保存完整提示詞及回答；若開啟完整請求記錄，請注意敏感資料可能寫入日誌。
- 設定可匯出／匯入；執行前後請留意備份與路徑。
- 服務預設只使用本機 loopback。**不要將 AMIEBL API 直接開放到公網或不受信任的網路。** 其他 Agent 客戶端的資料處理方式不在 AMIEBL 的控制範圍內。

**升級與解除安裝前，建議先備份使用者資料。** 正式安裝版的資料保留策略與腳本細節請參考 [installer/README.md](installer/README.md)。

## 專案架構

```text
VS Code Agent / OpenAI-compatible 客戶端
                   |
                   v
             AMIEBL API Proxy
              FastAPI / Python
                   |
                   v
              llama-server
                llama.cpp
                   |
                   v
                GGUF 模型

  WinUI 3 桌面介面 --> AMIEBL 管理服務
```

| 目錄 | 內容 |
| --- | --- |
| `desktop-winui/` | 原生 WinUI 3 桌面前端（預設） |
| `desktop-core/` | 共用 ViewModel、草稿編輯、設定儲存與衝突處理 |
| `desktop-platform/` | Windows 整合、後端啟動、背景程序及系統匣 |
| `desktop/` | 保留的 WPF 對照／備用前端 |
| `backend/` | FastAPI、llama.cpp 管理、請求處理、VS Code 同步 |
| `tests/` | Python 整合測試、.NET 核心測試及桌面回歸驗證 |
| `installer/` | Inno Setup 與 PowerShell 安裝／解除安裝腳本 |

架構與隔離測試細節見 [DESKTOP-ARCHITECTURE.md](DESKTOP-ARCHITECTURE.md)。

## 從原始碼建置

需要 Windows x64、.NET 10 SDK、Windows App SDK 的 NuGet 相依套件、可用的 Python 環境，以及供實際推理使用的 llama.cpp Windows 版本。

```powershell
git clone https://github.com/ghgf566/AMIEBL.git
cd AMIEBL

# 建置 WinUI 3（輸出到新資料夾）
.\build.ps1 -OutputDirectory 'C:\AMIEBL-build'

# 需要將 .NET runtime 納入發行包時
.\build.ps1 -OutputDirectory 'C:\AMIEBL-build-selfcontained' -SelfContained

# WPF 對照版本
.\build.ps1 -Frontend WPF -OutputDirectory 'C:\AMIEBL-WPF'
```

### 製作 Portable 與 Setup.exe

```powershell
# Portable：指定已準備好的 llama.cpp 路徑
.\package.ps1 -LlamaRoot 'C:\Tools\llama.cpp' -PythonHome 'C:\AMIEBL-build\python-clean' -SelfContained -OutputDirectory 'C:\AMIEBL-release'

# 如果安裝 Inno Setup 6，再加上 -BuildInstaller 製作 Setup.exe
.\package.ps1 -LlamaRoot 'C:\Tools\llama.cpp' -PythonHome 'C:\AMIEBL-build\python-clean' -SelfContained -BuildInstaller -OutputDirectory 'C:\AMIEBL-setup-release'
```

正式打包時 `-PythonHome` 應指向乾淨、已安裝 backend/requirements.txt 的執行環境，不要直接複製個人 Python 安裝。可用 `-IsccPath` 指定免安裝編譯器，`-ThirdPartyDirectory` 納入版本對應授權全文，`-VCRuntimeDirectory` 指向 Visual Studio x64 CRT 的合法可再散布目錄。開發測試時 `-PythonHome` 可省略；`-ModelDirectory` 可選擇將模型納入 Portable，否則預設不複製大容量模型。為避免誤刪資料，打包器不會覆蓋已存在的 Portable 輸出資料夾。

詳見 [安裝與解除安裝說明](installer/README.md)。

## 測試

```powershell
# Python 單元與整合測試
python -m unittest discover -s tests -p "test_*.py" -v

# .NET 共用編輯器回歸測試
dotnet run --project tests/DesktopCoreRegression -c Release

# Portable / 安裝版資料位置及遷移回歸測試
dotnet run --project tests/DesktopPlatformRegression -c Release

# 驗證實際編譯的桌面執行檔（隔離測試資料）
.\tests\Test-Desktop.ps1 -FrontendDirectory 'C:\AMIEBL-build'
```

桌面 smoke test 會使用專用資料目錄，不需要載入真實 GGUF；但無法替代不同 Windows 版本、硬體、模型，以及真實 VS Code Agent 使用情境的驗收。

## 已知限制與開發方向

目前主要支援 Windows x64；一次只有一個已載入模型／推理 Slot。Thinking Budget、MTP、視覺及各種採樣功能仍需搭配相容模型和 llama.cpp 版本。VS Code 設定修改後必須重新同步；部分引擎參數修改後需要重新載入模型。

未來考慮加入 llama.cpp／CUDA Libraries 更新、主題切換、介面語言切換，以及其他推理核心支援。這些**不是目前已實作的功能，也沒有承諾發布時間**。

## 授權與貢獻

AMIEBL 以 **[Apache License, Version 2.0](LICENSE)** 發布。使用、修改與再散布時請遵守授權、保留必要聲明。打包的第三方元件（例如 llama.cpp、Windows SDK 或 CUDA 相依項目）仍適用各自的授權條款。

歡迎透過 GitHub Issues 回報問題或提出功能建議；若涉及漏洞，請依 [安全性政策](SECURITY.md) 避免在公開討論中暴露敏感資訊。

**作者：Mr. Chen**

*The complexity belongs behind the interface, not in front of the user.*
