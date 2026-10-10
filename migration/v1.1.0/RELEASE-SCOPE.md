# AMIEBL v1.1.0 發布範圍凍結

2026-10-11：依使用者決定，停止追加功能，先完成可用的原生版本。
此文件是候選版交付說明；驗收未完成前不得宣稱正式發布。

## 安裝與使用

- Windows x64；C++/WinRT WinUI 3 GUI 與 Rust Core，不需要 Python 或 .NET 10。
- Portable 解壓後執行 LocalModelManager.exe，資料在同層 data。
- Setup 提供繁體中文與英文安裝介面，預設安裝於目前使用者的 LocalAppData，資料在 %LOCALAPPDATA%\LocalModelManager。
- 不隨包附模型或 llama.cpp；在系統頁安裝引擎，或指定既有外部引擎。已安裝引擎後可離線使用。
- 安裝不主動開啟登入自動啟動。升級前備份既有資料。
- 引擎管理目錄位於程式同層，請使用可寫入位置；本版不新增 Program Files 提權 helper。
- 一般解除安裝保留使用者資料、模型、外部引擎與受管引擎。要移除受管引擎，先在系統頁使用已有的移除功能；不以遞迴刪除換取乾淨目錄。

## 凍結原則

只修安全、資料遺失、安裝／啟動失敗與可重現的核心流程退化。
不追加 Capability Registry、Tensor Placement、多 Provider、自適應 Thinking、特殊標題佇列、下載續傳或更新提權服務。
GPU、Vision、MTP 支援取決於引擎、模型與硬體，未測矩陣不宣称通過。
標題／摘要辨識保留既有保守回退與單 Slot 佇列。

## 最小發布關卡

- 最終 Portable／雙語 Setup 在乾淨的受支援 Windows 啟動，不依賴開發機 runtime；中文與空白路徑、移動、離線使用。
- 原有設定遷移、匯入匯出與升級／解除安裝保留資料；未知檔案、外部模型及引擎不刪除。
- 真實 CPU 模型回答、串流、取消後再請求；承諾支援的 GPU 後端需對應硬體證據。
- VS Code 新對話、標題及工具回合；取消後可繼續使用。
- 最终發行資產的來源提交、SHA256、第三方授權與啟動抽驗。

## 發布後清理

正式 v1.1.0 驗收及發布完成後合併收尾變更至 main，清理舊 Python／.NET 使用者文件與過時建置指引。
v1.0.0 tag 保留歷史；不提前刪除仍用作相容性 oracle 的參考程式與測試。
目前 README／舊 package.ps1 仍描述 v1.0.0，原生候選版使用 package-native.ps1。
