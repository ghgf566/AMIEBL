# Native GUI migration build target — not a replacement GUI

The system page now includes development engine management: Stable/Preview,
automatic/manual backend selection, installation progress/cancellation,
version activation/rollback and update policy. Managed engines live beside
the packaged executables in `推理引擎/`; external `engine_dir` remains available.
See [engine management](../native-core/ENGINES.md) for verification and limits.

This is a real C++20/C++/WinRT WinUI 3 executable, with app-local Windows App SDK
and the frozen v1.0.0 MainWindow XAML shell (only its class namespace changes).
The overview and tasks/history pages now construct real controls and communicate
with the actual Rust service. Load/unload, pause/resume, keep-loaded saving,
status polling and cancellation are connected. VS Code preview/confirmation/apply
and log actions are wired, but have not received full interactive acceptance.
The model library, profile and system pages now use the frozen editor schemas
and real Rust management endpoints. Drafts, changed-field merging, conflict
handling, save/discard/cancel navigation, model/profile operations and native
file pickers are implemented. The log and editor expanders port the frozen
260 ms smoothstep animation. Tray, close-to-background, singleton reveal and
Core recovery are implemented. This remains a development GUI pending complete
visual, keyboard, accessibility and hardware acceptance.

Development runs require `--data-dir <isolated-directory>` and optionally
`--core <amiebl-core.exe> --port <port> --engine-port <port>`.
The GUI checks `X-AMIEBL-Core-Protocol: 1` on health and uses the existing
management token. An owned Core is launched in a private kill-on-close Job;
a compatible already-running Core is attached and never stopped by GUI exit.
HTTP caching is disabled so polling observes current engine/task state.

`tests/test_native_gui_integration.py` launches actual WinUI controls with the
real Core and unchanged Fake Engine. It invokes automation peers, verifies
load/unload and persisted toggles, confirms active SSE cancellation and slot
release, tests expander reversal, editor merging/conflicts, navigation dialogs,
profile duplication, singleton reveal and Core recovery, and captures PNG evidence. This proves the
specified operations, not full visual/animation parity or real llama.cpp.
Use `AMIEBL_REQUIRE_GUI_TESTS=1` to require the built GUI; use
`AMIEBL_GUI_EVIDENCE_DIR` to retain sanitized test evidence.

Build with Visual Studio C++ desktop tools, the Windows SDK, and C++ UWP/XAML
build support (the Windows App SDK XAML compiler imports these C++ targets):

```
msbuild native-gui/AMIEBL.Native.vcxproj /restore /p:Configuration=Release /p:Platform=x64
```

Build output is isolated under ignored `build/native-gui/`. Project deployment
follows Microsoft's [self-contained C++ WinUI sample](https://github.com/microsoft/WindowsAppSDK-Samples/tree/main/Samples/SelfContainedDeployment/cpp/cpp-winui-unpackaged).
Baseline captures and full interaction/animation parity gates remain required
before the old GUI is retired; the shell is not evidence those gates passed.

`Build-Development.ps1 -OutputDirectory <new-directory>` builds an isolated
development folder with the native GUI/Core and app-local Windows App SDK.
It does not package an engine or replace the supported installation. Its
startup Run value is isolated as `AMIEBL.Native.Development`; production
registration migration and clean-machine VC runtime validation remain pending.
The new Runtime Package Manager is deferred by the user's phase-priority
decision; existing manual engine configuration remains required.

For session-isolated workspaces, the development builder honors
`CARGO_TARGET_DIR` (or `-RustTargetDirectory`) and optional
`AMIEBL_NATIVE_OUTPUT_DIR`, `AMIEBL_NATIVE_INTERMEDIATE_DIR`, and
`AMIEBL_NATIVE_RESTORE_DIR` (or the corresponding `-Native*Directory`
parameters). Native integration/probe tests use the same target/output
environment variables. With these unset, the original repository-local
paths and GitHub Actions behavior are retained. No global environment
changes are necessary.
