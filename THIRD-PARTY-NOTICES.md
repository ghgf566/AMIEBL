# 第三方元件與授權

AMIEBL 原始碼適用 Apache License 2.0；此授權不會變更隨附第三方元件的授權。
正式發行包的 `third-party/` 目錄保存版本對應的授權、著作權與第三方聲明全文。
打包時必須保留各元件原始授權檔；此摘要不取代原文。

| 元件 | 版本／用途 | 授權與來源 |
| --- | --- | --- |
| llama.cpp / ggml | b11232，commit 6f767fe96；GGUF 推理 | MIT；https://github.com/ggml-org/llama.cpp |
| LLVM OpenMP | llama.cpp 隨附 libomp.dll | Apache 2.0 with LLVM exceptions；保留引擎 LICENSE-LLVM-OpenMP |
| NVIDIA CUDA runtime、cuBLAS、cuBLASLt | CUDA 12.4；NVIDIA GPU 推理 | NVIDIA CUDA EULA，非 Apache 2.0；https://docs.nvidia.com/cuda/archive/12.4.1/eula/index.html |
| CPython | 3.14.7 Windows x64 embedded runtime | PSF License 及隨附第三方條款；https://www.python.org/ |
| FastAPI、httpx、uvicorn 及相依套件 | 後端 HTTP 服務；版本見 runtime 的 dist-info | 各自 MIT / BSD / Apache 等條款，保留所有 dist-info 授權與 metadata |
| .NET runtime | 10.0.12，Windows x64 自包含 | MIT 與 .NET THIRD-PARTY-NOTICES；https://github.com/dotnet/runtime |
| Windows App SDK / WinUI 3 及相依元件 | 1.8.260921001 與建置鎖定套件 | 各 NuGet 元件隨附 Microsoft 授權與 NOTICE；https://github.com/microsoft/WindowsAppSDK |
| Inno Setup | 6.7.3，安裝／解除安裝執行碼 | Inno Setup license；https://github.com/jrsoftware/issrc |

CUDA DLL 僅作為 AMIEBL 推理功能的相依元件提供；使用與再散布需遵守 NVIDIA 條款。
不包含 NVIDIA 驅動、完整 CUDA Toolkit 或 GGUF 模型。模型由使用者另行取得並遵守其授權。
第三方授權清單與套件版本應在每次更新 runtime 時重新核對。
