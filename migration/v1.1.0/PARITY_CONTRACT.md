# AMIEBL v1.1.0 — Zero-Regression Native Migration Contract

> **Status: IN PROGRESS — NOT RELEASE READY.** This is a migration branch, not an assertion that the rewrite is finished.
>
> Source of truth: **v1.0.0 tagged commit `eab5dc1451bf38d1c008558ba8d49c85954c04bb`**. Keep the tag and `main` untouched until native parity is proven. If sources disagree, verify actual v1.0.0 application behavior rather than silently changing it.

## Phase-priority amendment — explicit user decision, 2026-10-09

Complete the language migration and existing feature/UI parity first. New online
engine download, CPU/CUDA installer selection, version switching, update,
rollback and independent uninstall UI are deferred to a later independent
project. They must not block acceptance of this language-migration phase.
Existing manual engine_dir and all existing inference options remain mandatory.
The engine-manager sections below retain the long-term target; their new-feature
requirements are deferred, not satisfied. Hardware/clean-machine and complete
visual acceptance remain unverified until actually performed. No release is
implied by the phase-priority amendment.

## Non-negotiable acceptance rule

**Implementation languages may change; externally observable behavior must not change.** The native rewrite MUST retain every existing functional behavior, setting, shortcut, layout, animation, persistence semantic, error response, window/tray behavior, installation/uninstallation guarantee, and public API behavior. Do not trade a missing feature for a simpler implementation. An unverified or partly implemented replacement **must not be shipped as v1.1.0**.

Required final deployment:

- Desktop: **C++20 / C++/WinRT / WinUI 3**, matching the old C# WinUI 3 GUI. Windows App SDK is app-local/self-contained; no .NET or Python runtime is required by the installed production app.
- Core: **Rust** standalone background process implementing the previous Python manager, including API Proxy and VS Code Agent integration. Loopback HTTP/JSON/SSE is the GUI–Core contract; avoid FFI coupling.
- Engine: **llama.cpp and CPU/CUDA runtime components downloaded and versioned separately**, integrity checked, with independent install/update/rollback and offline-friendly diagnostics. No runtime download or updater may overwrite user-selected custom `engine_dir`.
- Preserve Windows 10 2004+ x64 and Windows 11 x64 compatibility unless a verified blocking requirement is recorded explicitly. Windows 10/11 x86 is **not** a v1.0.0 parity requirement.
- No changes may silently remove optional configurations or change established default behavior. The release must not depend on an internet connection after its required engine was installed.

## Contract sources

1. `CONTRACT.md`, `DESKTOP-ARCHITECTURE.md`, `README.md`, `使用說明.md`, and v1.0.0 Release Notes.
2. All existing tests in `tests/` (Python integration tests, C# Desktop Core/Platform regressions, GUI and installer acceptance tests).
3. Production code: `backend/manager.py`, `backend/vscode_integration.py`, `desktop-core/`, `desktop-platform/`, `desktop-winui/`, `installer/`.
4. Screenshots and **screen recordings captured from a frozen v1.0.0 build** using representative settings: default/hover/selected/focus/dirty/loading/error, dialogs, expanded editors, tray, DPI 100/125/150/200%, and system text scaling. These are required before the legacy GUI can be retired.

## GUI/UX compatibility inventory

- Five navigation pages, labels and order: **總覽 / 模型庫 / 使用模式 / 任務與紀錄 / 系統**.
- Preserve navigation pane behavior, footer/branding, dark styling, fonts, colors, spacing, icon geometry, focus order and accessible control roles.
- **SmoothExpander** must preserve expansion/collapse animation, scroll anchoring and pointer/keyboard behavior, including rapidly reversing animations.
- Editable forms: original field types, validation ranges, defaults, disabled/read-only states, tooltips, dynamic visibility, unsaved edits, navigation-away confirmation, save errors, refresh preservation, profiles and model settings, explicit import/export, path pickers.
- Model library and scan: selecting/adding GGUF, split shards, projector, MTP draft; display GGUF-derived native context and thinking controls; never invent missing models.
- Model and profile duplicate/add/delete, Quick Chat/Coding/Deep Coding, all profile options and client override behavior.
- Task list/detail/status refresh: queue, prefill, generating, thinking, cancellation, throughput/latency/token usage, resource metrics, sorting and history pruning.
- Startup options, single instance, restore/focus existing window, tray menu, close-to-tray, background, X vs fully exit, startup autolaunch OFF by default; no consoles.
- Application window layout/resize/minimums, system/native dialogs, file/folder pickers, InfoBar messages, active navigation indicator, animations under reduced-motion preferences, update/polling that does not block the UI.
- **Do not assume a WinUI 3 XAML file ports automatically:** UI-building code is in `MainWindow.xaml.cs`, `MainWindow.Editors.cs`, `MainWindow.UX.cs`, `MainWindow.Actions.cs`, and `SmoothExpander.cs`. Existing `desktop-core/` and `desktop-platform/` logic also requires migration or faithful relocation.

## HTTP endpoints (retain HTTP methods, URLs, schemas, headers, errors and side effects)

| Method | Endpoint |
| --- | --- |
| GET | `/health` |
| GET | `/manager/config` |
| GET | `/manager/export` |
| PUT | `/manager/config` |
| POST | `/manager/import` |
| GET | `/manager/status` |
| GET | `/manager/requests` |
| POST | `/manager/records/clear` |
| GET | `/manager/logs` |
| POST | `/manager/scan` |
| POST | `/manager/model-capabilities` |
| POST | `/manager/load` |
| POST | `/manager/unload` |
| POST | `/manager/accepting` |
| POST | `/manager/keep-loaded` |
| POST | `/manager/requests/{ident}/cancel` |
| POST | `/manager/shutdown` |
| GET | `/manager/connection` |
| GET | `/manager/vscode/preview` |
| POST | `/manager/vscode/apply` |
| GET | `/v1/models` |
| POST | `/v1/chat/completions` |

- Keep loopback-only binding at `127.0.0.1`, default manager port 8080, engine port 8081; no inadvertent LAN exposure.
- Keep `X-Manager-Token` authorization on management endpoints, token persistence, secrecy and error sanitization; preserve `/health` and inference loopback accessibility and origin checks.
- Preserve full JSON config import/export schema, unknown-field preservation and atomically persisted configurations; existing `schema_version: 1` data stays readable. Keep portable `data/` versus installed `%LOCALAPPDATA%\LocalModelManager`, migration/backup behavior and recovery from corruption.
- Keep API request identifiers in `X-Conversation-Id` / `X-Manager-Request-Id`; support streaming SSE fragmented UTF-8 and tool-call fragments as well as nonstreaming replies, reasoning, multimodal requests, client sampling/model/profile overrides.
- Keep bounded queue and single inference slot, no duplicate inference after HTTP transport issues, deferred unload and pending engine restarts versus live-config updates.
- Cancellation must work at all phases: queued, before engine load, loading, classification, before upstream headers, during streaming, and on client disconnect. Retain upstream `DELETE /v1/stream?conv_id=...` handling, slot-idle verification and owned-engine fallback termination without killing unrelated processes.
- Automatic GPU offload (`llama-fit-params`), fit margin override, GPU and CPU thread settings, context, KV cache, GGUF metadata, native/external MTP and mmproj settings, detection from runtime `/props`, no false precision.
- Preserve reasoning policy: abstract effort mapping, native effort, thinking toggles and budgets, thinking/adaptive classifier, explicit user overrides, bounded max tokens and minimum output reserve.
- History/logs privacy: default request-body logging OFF; redaction, retention, maximum entry counts, task states, metrics, in-flight status and opt-in body captures.
- VS Code: `chatLanguageModels.json` JSONC parsing, owned-model tracking, generated `.agent.md` files, profile markers, user-authored tools/prompt preservation, preview-before-apply, transaction-like rollback on errors, backups, model and alias behavior.

## Deployment/integrity compatibility

- Actual installer and portable must be independently tested on a **clean machine** with no Python/.NET Runtime/Windows App SDK shared dependency assumed. Do not accidentally test against development SDKs.
- `LocalModelManager.exe` naming and installed app registration/shortcuts must remain compatible unless explicitly approved.
- App-local Windows App SDK and needed native CRT components uninstall with AMIEBL; `llama.cpp` versions and their CPU/CUDA package dependencies are managed separately with verified SHA-256, provenance/version metadata, atomic installation, previous-version rollback and accurate compatibility detection.
- No automatic installation of GPU drivers. CPU-only fallback when CUDA is unavailable; expose failure and recovery in UI instead of silently falling back without feedback.
- Preserve user GGUF files, custom binaries, unknown files, config, logs, VS Code backups and opt-in data during upgrade/uninstall. Never recursively delete a user-selected model or engine directory.
- Retain legal notices and third-party license texts; release SHA256SUMS, build source commit and clearly written limitations.
- Freeze **v1.0.0 baseline** in an immutable Git tag; branch only, never delete old tests before equivalent native tests run.

## Approved configuration differences (2026-10-10)

The model allocation slider intentionally changes the normalized v1.1 contract:
models missing `output_percent` gain 25, including startup migration. Custom
thinking budgets are validated independently of legacy profile `max_tokens` and
clamped against the actual model/client output allowance at request time.
`tests/test_native_config_parity.py` retains every frozen-reference scenario and
asserts these specific differences explicitly, comparing the entire remaining
result. The frozen Python reference is unchanged. This does not waive other
schema or release gates; see `native-core/TOKEN-BUDGET.md`.

GUI recovery acceptance must preserve edited values and editor identity. Valid
edits may become clean only after verified persistence under autosave; invalid
unsaved edits must survive reconnection without changing persisted settings.
Slider acceptance waits for its real UI events, checks both live allowances and
the persisted percentage, and retains a bounded failure timeout.

## Completion gates (ALL must pass)

1. **Baseline characterization:** save HTTP golden fixtures and screenshots/recordings from v1.0.0; record behavior for success, failure and cancellation cases.
2. **Rust parity:** all 22 endpoints validated using identical requests against old/new backends, including response shapes, statuses, auth, exceptions, persistence and reproducible streaming/cancellation/failure tests.
3. **C++/WinRT parity:** screenshot/pixel comparison plus *manual* interaction checks for every control and all five pages; timing/animation validation; focus, keyboard, high-DPI, reduced motion and accessibility.
4. **Lifecycle parity:** GUI↔Rust Core↔engine process lifetime, startup/tray/exit, hangs, port conflict, unexpected exit, application updates and migration from existing data.
5. **Runtime installer parity:** clean Windows 11 and supported Windows 10 x64; CPU-only/no NVIDIA; compatible NVIDIA; offline with installed engine; first-run download/update/rollback, malicious or corrupt archive, failed extraction, low disk space.
6. **Security/release:** static and dependency analysis, build reproducibility, detached engine checksums, third-party notices, signed binaries if available, exact released asset smoke test.
7. **No hidden fallback:** installed/native v1.1.0 ships **no old Python backend, C# GUI, `.NET` Runtime, embedded llama.cpp/CUDA payload**. Any unsupported feature is a blocking release defect, not a documented omission.

**Release decision:** keep developing and extending this branch until EVERY applicable gate is demonstrably green. If a gate is untested or fails, v1.1.0 is not finished; v1.0.0 remains the official usable baseline.
