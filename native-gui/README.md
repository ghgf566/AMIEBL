# Native GUI migration build target — not a replacement GUI

This is a real C++20/C++/WinRT WinUI 3 executable, with app-local Windows App SDK
and the frozen v1.0.0 MainWindow XAML shell (only its class namespace changes).
The overview and tasks/history pages now construct real controls and communicate
with the actual Rust service. Load/unload, pause/resume, keep-loaded saving,
status polling and cancellation are connected. VS Code preview/confirmation/apply
and log actions are wired, but have not received full interactive acceptance.
The log expander ports the frozen 260 ms smoothstep layout animation, including
reversal and reduced-motion handling. The three remaining page builders,
editors, draft preservation, tray, close-to-background and single-instance
behavior are still missing. This is not the replacement GUI.

Development runs require `--data-dir <isolated-directory>` and optionally
`--core <amiebl-core.exe> --port <port> --engine-port <port>`.
The GUI checks `X-AMIEBL-Core-Protocol: 1` on health and uses the existing
management token. An owned Core is launched in a private kill-on-close Job;
a compatible already-running Core is attached and never stopped by GUI exit.
HTTP caching is disabled so polling observes current engine/task state.

`tests/test_native_gui_integration.py` launches actual WinUI controls with the
real Core and unchanged Fake Engine. It invokes automation peers, verifies
load/unload and persisted toggles, confirms active SSE cancellation and slot
release, tests expander reversal, and captures PNG evidence. This proves the
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
