# 推理引擎管理（開發版）

引擎管理由 Rust Core 提供，原生 GUI 的「系統 → 推理引擎」操作相同的管理 API。
初始使用外部引擎模式，保留 schema-v1 的手動 `engine_dir`，不自動搬移或覆寫自訂引擎。
選擇並啟用受管版本後，執行時使用受管路徑；切回外部模式會恢復使用原本保存的 engine_dir。

## 現有能力

- Stable／Preview、CPU／CUDA 12.4／CUDA 13.4／SYCL／OpenVINO／ROCm／Vulkan 的選包設定，或自動建議。
- Windows x64 CPU、GPU、驅動與 RAM 偵測；啟動時偵測一次並快取。NVIDIA 同時核對 nvidia-smi 的 Compute Capability 與驅動 CUDA 能力。偵測失敗回報，不將未知 GPU 宣稱為可用。
- 官方 GitHub 發行來源；Stable 的 nightly-tag.txt 對應經官方摘要驗證後才使用，同時保留 Stable 語意版本及實際 build tag。
- 官方 SHA-256 與下載大小驗證、限量解壓、路徑／連結檢查、取消、新版本暫存與原子移入版本目錄、啟動版本探測。
- 安裝後記錄檔案摘要，啟用時再次驗證。缺少／變更檔案不啟用。
- 安裝完成列為待啟用版本；切換與回滾要求模型已卸載、沒有進行中或排隊請求。已下載的版本可以離線切換。
- 自動更新在 Core 運行時進行：启动／設定套用時檢查，之後每 6 小時檢查；下載完成後每分鐘嘗試在模型已卸載時套用。
- 固定版本、關閉檢查、通知、自動下載與自動套用選項。手動選擇通道不會把舊通道候選默默當成新通道候選。
- 非選取版本可移除；遇到未知檔案或連結時保留。跨程序寫入鎖與 metadata 變更檢查避免互相覆蓋。

## Windows 套件相容性與介面

- 所選通道先解析實際 release，再列出其官方 Windows x64 套件及 OpenVINO／ROCm 的實際執行期版本；缺少對應套件不安裝。
- GTX 1070 Ti（Pascal／CC 6.1）推薦 CUDA 12.4；CUDA 13.x 不接受 Maxwell／Pascal／Volta。新 GPU 優先新套件，CUDA 12.4 不當作 Blackwell 的保底。
- Intel Iris Xe／11 代以上 Core 核顯與 Arc／Flex／Max 列為 SYCL 硬體候選。80 EU 是效能建議而非硬性相容條件。Windows SYCL 官方包帶執行期。
- ROCm 的已知型號篩選是 Windows 官方支援表的保守子集，不代表驅動與 OS 已通過認證；ROCm 10.0 自動推薦還需 Windows 11 25H2（build 26200）與可讀取的 Adrenalin 26.8.1 以上版本；缺少任一證據時建議驗證 Vulkan。不同 ROCm 版本不沿用 10.0 規則。
- 手動指定非 CUDA 後端可測試未確認的硬體；套件啟用前必須成功列出該後端裝置。
- 介面呈現 CPU、GPU、RAM 與版本摘要，不直接輸出 JSON。選項變更自動儲存；無套用／重偵測按鈕。閒置時隱藏取消與進度，下載顯示跨資產累積百分比；解壓與驗證顯示不定進度。
- 「下載並安裝更新」要求模型卸載後啟用；已下載的候選可直接「啟用更新」。版本管理放在摺疊區。
- 檢查連線與模型載入共用受管引擎路徑，不再檢查舊的 external engine_dir。

官方依據：
- https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md
- https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/OPENVINO.md
- https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/
- https://docs.nvidia.com/cuda/blackwell-compatibility-guide/index.html
- https://rocm.docs.amd.com/en/docs-10.0.0/compatibility/compatibility-matrix.html

## 儲存

根目錄來自 Core 執行檔所在目錄：`engines/manifest.json` 與
`engines/llama.cpp/versions/<package-id>/`。Core 與 GUI 在同一交付包目錄時，
這就是軟體根目錄。直接從 Cargo build 目錄啟動 Core 時，根目錄會在該 build 目錄。
舊的中文管理目錄在沒有另一個管理程序寫入時改名為 `engines`，保留內容。若同時已有新舊目錄，使用英文目錄並保留舊目錄。
安裝過程 staging 在同一根目錄，正常結束／失敗／取消後自動清除。

manifest 保存 policy、active／previous／pending、provider id、通道、上游版本、
build tag、套件 URL／官方摘要、版本探測與所有受管檔案摘要。引擎設定與模型設定表單分別儲存。

## 新增管理 API

所有端點繼承原有 loopback／Origin／X-Manager-Token 管理保護。

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | /manager/engines | 狀態、候選、版本、下載進度、硬體、policy |
| POST | /manager/engines/hardware | 偵測硬體 |
| POST | /manager/engines/policy | 完整 policy：channel、backend、update、pinned、mode |
| POST | /manager/engines/check | 解析候選版本與官方套件 |
| POST | /manager/engines/install | 安裝已檢查的候選；背景作業，GET 輪詢進度 |
| POST | /manager/engines/cancel | 取消安裝 |
| POST | /manager/engines/activate | `{ "id": "<package-id>" }` |
| POST | /manager/engines/rollback | 回到 previous |
| GET | /manager/engines/runtime | 獨立 server 程序、健康與服務狀態 |
| POST | /manager/engines/stop | 停止 owned server、取消任務並暫停接收 |
| POST | /manager/engines/remove | 移除版本（使用中會自動停止 server）；同 activate 的 id payload |

policy 值：channel=`stable|preview`；backend=`auto|cpu|cuda12|cuda13|sycl|openvino|rocm|vulkan`；
update=`notify|download|auto|off`；pinned=boolean；mode=`external|managed`。
手動安裝不受 pinned 限制；pinned 只停用自動更新。

## 可重現驗證

一般測試包含來源／摘要拒絕、ZIP 越界、損壞包、跨程序 ownership、API 認證與 policy 保存、外部設定保留、實際 GUI 引擎選項。

官方下載實測需顯式設定 `AMIEBL_TEST_ENGINE_DOWNLOAD=1`，執行
`python -m unittest discover -s tests -p test_engine_manager.py -v`。
這會在獨立軟體暫存目錄下載兩條通道的 CPU 包，驗證安裝、啟用、回滾、
已改動檔案拒絕、未知檔案保存與非選取版本移除。它不代表真實模型生成驗收。

## 本階段限制與後續

Windows x64 CPU 已實際測試；CUDA／Vulkan 尚未在相應硬體驗證。
GPU 套件另外以 --list-devices 驗證後端裝置；--version 探測只能證明二進位可啟動，不能證明模型、MTP、Vision、fit 或全部 CLI/API 相容。
目前保留失敗時的舊版選取狀態，但尚未完成真實模型載入失敗的自動回滾／失敗版本封鎖。
通知在系統頁呈現候選與日誌；完整跨頁更新提示仍待加入。
目前下載取消／失敗會重來，尚不支援續傳；強制終止留下的 staging 尚需清理工作。
Program Files 寫入權限不足時回報錯誤；專用提升權限更新 helper 尚未實作。
正式原生 installer/uninstaller 仍待接上 ownership 清理，不能宣稱目前已完整解除安裝引擎。
舊版 Python／C# installer 保持原行為，不使用新引擎目錄作為遞迴刪除規則。

套件 metadata 已帶 engine_id，來源解析與通用驗證／安裝／版本状态分開，為 2.0.0 provider adapter 留下接點；
目前僅實作 llama.cpp，未實作 Ollama／SGLang／vLLM。後續需分離啟動／請求／取消與能力適配，不能把 OpenAI 介面相同當成完整能力相同。
# Stop and removal

`POST /manager/engines/stop` cancels tasks, unloads the model and stops the owned
server while keeping AMIEBL running. New tasks are paused; resume accepting tasks
before using inference again. It never terminates externally owned servers.

The GUI marks the selected version. External engine paths appear only in external mode. A selected package can be removed when its
server and tasks are stopped. Removing the last managed package clears selection
and disables automatic updates to prevent immediate reinstallation. Unknown files,
models and external engines remain protected.
