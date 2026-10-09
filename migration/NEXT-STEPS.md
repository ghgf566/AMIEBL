# AMIEBL 1.1.0 現況與下一步

更新：2026-10-10。開發分支為 `refactor/v1.1.0-rust-cpp-winui3`；本次成果提交至該分支。仍是開發版本，未修改 main／v1.0.0，未發行 1.1.0。

## 已完成

- Python → Rust Core、C# → C++/WinRT WinUI 3，五頁介面與管理 API 已串接，保留 v1.0.0 參考程式與資料相容驗證。
- llama.cpp 與主程式分離；受管套件位於軟體根目錄的英文 `engines/`，外部引擎與模型不搬移、不自動刪除。
- Stable／Preview 官方 Windows x64 套件來源、官方 SHA-256 校驗、安全解壓、安裝空間檢查、獨立版本、固定版本、更新政策、回滾與移除。
- 啟動時硬體偵測、自動推薦與手動指定 CPU、CUDA 12.4／13.4、SYCL、OpenVINO、ROCm、Vulkan。推薦基於硬體／驅動及實際發行套件，GPU 套件另以 --list-devices 驗證。
- 更新中心採選項自動儲存、啟動及運行期間定期檢查、手動檢查、下載進度，僅安裝期間顯示取消。硬體與版本採可讀格式。
- OpenVINO 裝置名稱大小寫誤判已修正；實際辨識 i5-12500H CPU 及 Iris Xe GPU。
- 可單獨停止 owned server，取消任務、卸載模型與暫停接收，主程式維持開啟。版本清單標示目前選用；停止後可移除全部套件。最後一個刪除後關閉自動更新，避免重新下載。
- 總覽新增獨立 server 卡片，以程序存活與 /health 回應判斷運行、尚未就緒、停止、無回應、退出、外部服務或連接埠占用；顯示 PID、版本、套件與服務位址。

## 驗證與限制

30 項 Rust 單元測試與嚴格 clippy 通過。引擎 API／GUI、官方 Stable／Preview CPU 下載安裝與切換回滾已有驗證。官方 OpenVINO 安裝、裝置探測、啟用及連線通過；SYCL 在 Iris Xe 上辨識、隔離設定載入與基本短回應通過。真實 CPU server 驗證運行中移除被阻擋、停止後刪除最後套件、主程式維持運作；總覽實際畫面已檢查。使用者手動測試目前未觀察到問題。

上述不代表所有模型、Vision、MTP、GPU 效能或乾淨安裝均已驗收。既有 Qwen 模型設定啟用 MTP 時能力檢查不通過；診斷只修改隔離副本。完整回歸仍有舊 Python 參考程式的 Windows 重載／連接埠釋放問題，凍結參考程式沒有為此改寫；歷史全部通過紀錄不代表該間歇問題已消失。

## 接下來的優先順序

1. 完成原生 Portable／Setup 與解除安裝：納入受管引擎 ownership 清理、模型與外部檔案保存、正式 startup 註冊，以及 Program Files 的有限權限更新方案。
2. 乾淨 Windows 驗收：可攜移動、中文／空白路徑、唯讀目錄、權限不足、未知檔案與 reparse points、安裝／升級／移除及資料保護。
3. 擴大真實模型驗證：CPU／SYCL／OpenVINO 的生成、串流、取消、Vision／tools／reasoning／MTP 等能力及效能；CUDA／ROCm／Vulkan 在對應硬體上驗證，不能以本機 Intel 結果代替。
4. 更新韌性：離線／網路中斷、取消與異常退出的 staging 清理、跨頁更新提示、模型載入失敗後自動回滾及失敗版本封鎖。續傳可另行評估。
5. 完成全介面 DPI、鍵盤與無障礙驗收，整理完整回歸與參考程式間歇性失敗證據，再進行 1.1.0 發行關卡。

Ollama／SGLang／vLLM 留至 2.0.0；目前保留 engine_id 與來源／套件模組接點，尚未實作其他 provider。

## 儲存庫定位

原生 AMIEBL 執行不依賴 Python 或 .NET：GUI 為 C++/WinRT，管理服務為 Rust，推理由獨立 llama.cpp server 提供。Python 及 .NET 僅留作開發測試與舊版參考；目前舊 installer 尚未完成原生替換，不能把它視為新的正式交付。驗收完成後再整理或封存參考實作，不要求使用者安裝 Python。

- `native-core/`、`native-gui/`：持續開發的原生產品。
- `native-core/ENGINES.md`：引擎政策、API、相容條件與限制。
- `migration/v1.1.0/STATUS.md`、`PARITY_CONTRACT.md`：遷移歷史及發行契約。
- Python／C# 參考程式及測試在完整驗收前保留。
- `build/`、`dist/`、`local-handoff/`：本機產物與環境，不提交模型、下載套件、個人設定、令牌或本機啟動器。
