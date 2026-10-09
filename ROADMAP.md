# AMIEBL — Product Roadmap / 版本規劃

> **規劃文件（非已完成功能清單）。** 此文件記錄使用者明確討論過的中長期方向，供未來對話、Codex Agent 與貢獻者延續；具體 CLI 參數及模型相容性仍以實際選用的引擎版本、模型與硬體測試為準。
>
> 最後整理：2026-10-10。此文件先記錄在 `refactor/v1.1.0-rust-cpp-winui3` 開發分支；不代表 1.1.0 已發布，也不授權提前擴大 1.1.0 的功能範圍。

## 版本路線

| 版本 | 定位 | 規劃狀態 |
| --- | --- | --- |
| **1.0.0** | Windows llama.cpp / GGUF / Profile / VS Code Agent 管理器 | 已發布，保留相容基準 |
| **1.1.0** | Rust Core + C++20/C++/WinRT 原生重構、獨立 llama.cpp 引擎下載/切換管理、Portable/Setup 與完整發行驗收 | 主要架構已實作；**尚未完成發行驗收**，參見 [NEXT-STEPS](migration/NEXT-STEPS.md) |
| **1.2.0** | **模型設定系統大更新**：擴充可自訂推理選項，並導入 Capability Registry（能力註冊表）與動態 UI | **使用者已決定的版本方向，尚未實作** |
| **1.3.0** | 更底層的效能調校：Tensor Placement、細粒度 CPU/GPU 資源及張量分配等 | 使用者已提出的版本方向；詳細規格未定 |
| **後續／未排版號** | 固定使用者指定 Context 的自動調校、Benchmark、長時間穩定性/Agent 工作負載測試及最佳設定推薦；主題/介面語言等體驗改善 | 構想階段，版本與優先級待定 |
| **2.0.0（遠期）** | 多推理引擎 Provider Adapter，例如 Ollama / SGLang / vLLM | 遠期規劃；目前僅 llama.cpp 已實作 |

**版本界線：** 不要為了實作 1.2.0/1.3.0 而推遲 1.1.0 的既定驗收，也不要把下述候選選項當作所有 llama.cpp 版本必定支援的特性。

## 1.2.0 — 模型設定系統大更新（已決定）

### 產品目標

目前已具備 Context、CPU threads、GPU Layers/自動 Offload、KV Cache、MTP、Vision、Temperature/Top P/Top K/Min P 等核心調整，但模型可自訂項目仍偏少。**1.2.0 專注於擴充實用的模型推理選項，並且先偵測「目前這個模型 × 引擎版本與後端 × 硬體」實際支援哪些功能，再提供對應的控制項。**

設計原則：**保有 llama.cpp 底層控制力，但由 AMIEBL 承擔判斷、驗證與解釋的複雜度。** 對新手預設直覺，對進階使用者提供可理解的進階控制。

### 核心架構：Capability Registry（能力註冊表）

建立統一的**設定規格與能力判斷來源**，避免把每個新參數的可見性、有效值與支援判定寫死在個別 WinUI 控制項。

每筆設定規格至少描述：

- **識別與呈現**：穩定的 setting key、中文名稱、用途說明、分類、基本/進階層級、唯讀/可修改。
- **型別與有效值**：boolean/enum/integer/float/path 等、可用選項、上下限、單位、保守預設值與可選「Auto」。
- **能力條件**：GGUF 架構/metadata/Chat Template、引擎版本/編譯選項/CLI 能力、運算後端、裝置與驅動條件；必要時實際 Runtime/裝置探測。
- **判斷狀態**：`supported`（已確認支援）、`unsupported`（已確認不支援）、`unknown`（證據不足）；附**原因、資料來源與可驗證方式**。不得將「無資料」直接當成「不支援」，也不能只靠模型名稱白名單宣稱支援。
- **套用語意**：與目前引擎版本的 CLI/管理 API 參數映射、相依/互斥項、是否需要模型重新載入或服務重啟、何時真正生效；分開呈現「設定值」與「實際採用值」。
- **持久化與遷移**：schema/舊資料相容、未知欄位保留、使用者草稿、引擎或模型切換時的狀態重評估與警示；不悄悄丟棄先前設定。

建議偵測流程：

1. 從 **GGUF Metadata / 模型架構 / Chat Template** 取得模型側事實，明確區分固定架構特徵與執行時開關。
2. 以**目前選用的受管或外部 llama.cpp** 的版本、受支援參數與編譯後端解析引擎能力，不假設不同 build 完全相同。
3. 偵測 **CPU / GPU / Driver / Backend** 條件；有需要時用引擎的 device listing、runtime props 或受控 smoke test 補證據。
4. 綜合三者產生可用設定及狀態，UI 解釋選項被禁用或尚未驗證的原因。
5. 模型、engine build、backend、硬體或設定相依條件改變時重新評估；既有有效自訂值盡量保留，不可靜默套用無效參數。

**重要：** 模型本身的 GQA/MQA/MHA 類型、layer/head 數等屬於架構資訊，通常應**唯讀呈現**，不能誤設計成可任意切換的執行時設定。

### 設定擴充候選（須先逐項驗證支援條件）

| 分類 | 候選設定／資訊 | 設計提醒 |
| --- | --- | --- |
| **Attention** | Flash Attention 的 Auto/On/Off；SWA Full Cache 等實際可調功能 | 區分引擎實作選項與模型固有 Attention 架構；部分後端、量化或模型可能不支援 |
| **Context / RoPE** | RoPE frequency base/scale、Scaling 類型、YaRN 等 | 不因開啟設定就保證超出模型訓練長度的品質；資訊不足時標記 unknown |
| **KV Cache** | K/V 分別指定快取型別、KV Offload、其他引擎實際支援的快取策略 | 確認引擎版本、模型 Attention 與裝置相容性；保留現有選項語意 |
| **Batch / Prefill** | Batch Size、Micro-batch Size、Continuous Batching 等 | 明確說明 VRAM/RAM、吞吐與延遲權衡；單一 Slot 架構限制須據實顯示 |
| **記憶體/模型載入** | mmap、mlock、weight repacking 與適用的載入策略 | Windows/硬體/引擎 build 可行性須實測 |
| **CPU** | CPU inference threads、batch threads 及 affinity 類選項（若引擎支援） | 清楚區分推理與 prompt processing 的工作階段 |
| **唯讀模型資訊** | Attention 類型、Head/KV Head、RoPE 預設、SWA/Context metadata | 只顯示能證實的資訊；不要把固定架構當作旋鈕 |

此表是**研究/設計範圍，不是已實作參數清單**。實際 CLI 旗標、語意、邊界值與相容性須逐版本查證，不直接照抄某一版 `llama-server --help` 當作永久介面。

### UI / UX 行為

- **基本模式**：先顯示常用、已確認適用的設定；進階選項分組收合、提供描述及建議值。
- **進階／完整檢視**：允許查看不支援或未知項目及原因，不是直接消失；未知項目的實驗性嘗試應有明確提示及安全限制，不能冒稱保證可用。
- **動態相依關係**：例如關閉某項功能後，其附屬參數同步停用或隱藏；狀態改變不應刪除原本草稿。
- **保存與生效**：保留既有未儲存提示、dirty draft、驗證、伺服器合併/衝突處理；明確標示「立即生效／待重新載入／需重啟」。
- **能力資訊來源**：可檢查目前 model、engine build、backend 及最後偵測結果，避免無理由的灰階/不可點控制項。
- **失敗復原**：CLI 參數拒絕、模型載入失敗或 Runtime 與預測不符時提供診斷，不刪使用者設定；避免無聲 fallback。

### 1.2.0 驗收門檻（規劃）

1. **Registry 正確性**：各設定能解析 type/range/default、相依與衝突、未知狀態、版本/後端條件與參數映射；新增設定不需在各頁重寫判斷。
2. **相容性矩陣**：用不同 GGUF metadata、缺失 metadata、不同 llama.cpp builds 與 CPU/iGPU/dGPU backends，測試三態偵測結果；不可把未確認支援誤判為已確認。
3. **設定保存**：從 1.1.0 升級後，原有 model/profile/unknown fields、使用者草稿、匯入匯出、合併/衝突行為完整保留。
4. **套用行為**：對每項已啟用的控制，測試引擎參數映射、實際生效/待重載標記、錯誤回報與安全復原。
5. **UI/UX**：基本與進階模式、鍵盤/螢幕閱讀器、不同 DPI、原因說明、切換模型或引擎後重評估；不得造成 1.1.0 功能退化。
6. **真實引擎驗證**：至少對可取得的 CPU 與 Intel 核顯後端跑真實 GGUF；CUDA 等其他後端仍需相應硬體驗收，不能以 Fake Engine 取代。
7. **發行清潔度**：開發 Probe、測試 fixture、內部基準資料不進 Portable / Setup；維持 Rust+C++ 原生正式執行環境。

## 1.3.0 — 更底層的進階配置（方向已定，細節未定）

在 1.2.0 的 Registry 基礎上，進一步研究 Tensor Placement/Offloading、細粒度張量/Layer 的 CPU/GPU 分配、多 GPU 與共用記憶體核顯的配置語意、後端裝置能力及相關效能/容量取捨。應沿用 Capability Registry，不開第二套寫死的 UI 規則。

不要將「能偵測整合式 GPU」「顯存為共享系統記憶體」直接等同可使用所有 GPU offload 或 tensor override 參數；實際資源配置必須依引擎與後端確認。

## 更長期（版本未定）

- **自動最佳化 / Benchmark**：在**使用者指定的 Context 不變**的前提下，評估 GPU/CPU Offload、Batch、KV、MTP 等可支援選項；量測 prefill、decode、RAM/VRAM、穩定性，提供可檢查的推薦而非只顯示單一分數。
- **Overnight 深度測試**：長 Context、串流/取消、工具呼叫、多輪 Agent、長時間壓力測試；輸出結果與失敗案例。
- **體驗改善**：主題、多語言、易懂的設定說明，以及持續降低安裝、維護、資源占用負擔。
- **多 Provider（2.0.0）**：基於不同引擎各自的啟動、卸載、請求、取消、模型格式與能力語意設計 Adapter；不能只因皆提供 OpenAI-compatible API 就假設功能等價。

## 實作開始前的提醒

1. **先完成 1.1.0** 的 Portable/Installer/Uninstaller、乾淨 Windows、真實模型與 UI/UX/安全驗收；以 [migration/NEXT-STEPS.md](migration/NEXT-STEPS.md) 為當前執行清單。
2. 1.2.0 實作前先根據當時的最新 llama.cpp 官方文件/版本及真實套件重新核對候選 CLI 參數；對未知能力保持 unknown。
3. 本 Roadmap 只記錄經討論的方向；**不要擅自把長期構想加入當前版本的完成條件**，若要調整範圍應先更新本文件。
