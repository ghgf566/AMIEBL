# Desktop architecture and verification

The default frontend is now native WinUI 3 (Windows App SDK 1.8.260921001, x64, Windows 10 2004+). WPF remains an explicit fallback and a regression reference.

## Boundaries

- `desktop-core`: API transport, typed/lossless model and profile snapshots, observable workspace state, draft/field view models, async commands, editor schemas, serialized save/merge/conflict handling. No WPF or WinUI dependency.
- `desktop-platform`: Python discovery/private environment, owned backend process/job cleanup, shared single-instance identity, Windows startup and tray adapters.
- `desktop-winui`: native NavigationView/InfoBar/form bindings, window/picker/dialog adapters, five functional pages. View-specific navigation and UI orchestration remain here.
- `desktop`: retained WPF adapter using the shared workspace/configuration service. Its programmatic page builders remain intentionally available for comparison; this is not a rewrite of every legacy WPF view into XAML bindings.
- `backend`: existing FastAPI/llama.cpp supervisor. `/manager/status.loaded_model_settings` exposes the loaded snapshot and effective GPU layers, separately from saved configuration. No model/settings files are moved by the frontend migration.

## State rules

Resolve model/profile identity from the current snapshot when opening an editor. Editing uses a separate draft; polling must not overwrite it. Saving serializes mutations, starts from the newest server configuration, preserves unknown fields, and uses the validated PUT response as the persisted truth. A later status-refresh failure is reported as saved-but-refresh-failed. A stale poll cannot replace newer saved state. Draft editors only merge changed fields and reject conflicts with external changes to the same field. Capability metadata remains read-only; template inference is not proof of sampler enforcement.

The API still accepts complete JSON PUTs without a server ETag. The draft conflict check detects changes seen by its GET; an independent client writing in the interval between GET and PUT can still race. Backend revision/ETag support is future hardening, not a claimed guarantee.

## Build

```powershell
./build.ps1 -OutputDirectory C:\path\to\new-output -SelfContained
./build.ps1 -Frontend WPF -OutputDirectory C:\path\to\wpf-output
dotnet run --project tests/DesktopCoreRegression -c Release
python -m unittest discover -s tests -p "test_*.py" -v
```

Build into a new folder. Do not overwrite a running release, its portable data directory, or its models. WinUI and WPF deliberately share the existing single-instance identity for a given data directory, so fully exit the old program before launching the new one with that data directory.

## Native operation regression

Create a dedicated configuration containing two registered models and two profiles, disable preload/autostart, choose free API/engine ports, and use a new data directory:

```powershell
./LocalModelManager.exe --smoke-test --data-dir C:\path\to\isolated-data --port 18990
```

WinUI smoke tests require an explicit data directory. They exercise TextBox bindings, actual API persistence, ListView selection away/back, the five page layouts, PNG rendering, and owned backend shutdown. They restore the test configuration in `finally`; no GGUF inference or startup registry writes are performed. Artifacts: `winui-smoke-test.json`, `winui-screenshots/`.

WPF uses `--smoke-test --editor-refresh-test --data-dir ...`. Core regressions cover unknown fields, observable refresh, failed saves, partial success, serialization, edit revisions, external merge/conflicts, array formatting, and stale responses.

Real picker interaction, tray menu clicks, VS Code writes, installer behavior, other Windows versions and GGUF families require separate acceptance testing. The frontend migration does not establish new inference compatibility guarantees.

The publish target explicitly includes the app PRI and compiled XBF resources; compiling successfully alone does not validate an unpackaged release. Use tests/Test-Desktop.ps1 with FrontendDirectory to test the exact published artifact in a fresh isolated directory; it checks process exit and fresh result files.

## UI/UX parity pass

WinUI now groups model, Profile, system and overview controls into cards. Thinking capability has its own visible section. Advanced sampling and Agent fields can collapse. Context, CPU threads, thinking budget and generation limit support sliders alongside exact entry; initialization does not modify saved values and unknown native Context uses the supported input range. Inline guidance explains load-time versus request-time behavior.

Model library provides add/change/remove search locations, matching the WPF registration semantics without moving files. Task history has a bounded left list, a separate scrollable detail area, and collapsible logs with copy/open-folder actions. Polling keeps the task-page containers and selected request instead of rebuilding the entire page.

The clean navigation path no longer disables and restores NavigationView for every click. A dedicated composition indicator replaces the template indicator and retargets its Offset animation; Windows animation preference is respected. API reference: https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.composition.implicitanimationcollection?view=windows-app-sdk-1.8

The isolated native test now requires slider_sync_verified, model_locations_verified and rapid_navigation_verified. It tests 15 navigation changes at 25 ms intervals and produces the five page screenshots plus detailed performance/budget views. This validates final selection and layout, not a frame-by-frame visual smoothness benchmark on all display configurations.

## Layout motion and overview polling

SmoothExpander animates the content viewport's actual layout height over 260 ms. Neighboring model actions, task cards and ScrollViewer extents therefore interpolate with the content instead of changing immediately. Bottom-anchored location/log headers remain fixed. Windows reduced-motion preference is respected. Editor expanders reveal their header/content as they open; wheel/pointer interaction cancels automatic scrolling.

Overview polling updates existing text/card visibility rather than rebuilding the page, preserving its ScrollViewer and offset. The red exit action appears only in the system-page header and retains red pointer-over/pressed resources.

The isolated native regression samples intermediate action positions, task-card heights and editor scroll extents, verifies automatic reveal and unchanged overview scroll offset/page identity, and checks exit visibility on every page. Screenshot review covers the red hover state. These checks do not claim a frame-rate benchmark on every display configuration.

## Native Expander appearance

SmoothExpander now hosts a real WinUI Expander. It obtains the installed SDK header Style (including directional chevron and pointer/keyboard states), body brushes and padding, then applies a layout-only ControlTemplate with the named native parts. The body viewport still animates actual height; this is a customized native template, not the unmodified default template. This avoids competing native visibility/translation storyboards while retaining the native control and its automation behavior. SDK template-part changes are detected explicitly and require a compatibility update.

The isolated native regression additionally checks that the SDK header Style is present, toggles the actual header to verify two-way expansion, and reverses an animation before completion without jumping its height. Screenshot review verifies visible expanded content as well as the layout measurements.

## Stable compact navigation and frame pacing

Navigation uses the native PaneTitle rather than a variable-height PaneHeader. The Chinese tagline, version and author occupy PaneFooter only while the pane is open. This keeps all five menu items at the same vertical positions when toggling compact/open, and avoids clipping footer text into the compact rail.

SmoothExpander now advances on CompositionTarget.Rendering instead of a 16 ms DispatcherTimer. Rendering subscription exists only during an animation and is removed on completion, reversal and unload. Reveal targets are calculated once at expansion and tiny redundant ChangeView calls are skipped. Actual layout heights still interpolate, so this is UI-thread layout motion; it is not a claim of entirely compositor-independent animation.

The isolated regression toggles the pane four times, checks every menu item's Y coordinate, footer visibility and unchanged selection/page instance, captures compact/open screenshots, and records animation update counts and maximum callback intervals. Callback counts are diagnostics, not presented-screen FPS or a hardware benchmark. API documentation: https://learn.microsoft.com/en-us/windows/windows-app-sdk/api/winrt/microsoft.ui.xaml.media.compositiontarget.rendering?view=windows-app-sdk-1.8
