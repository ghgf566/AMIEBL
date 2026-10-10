## Latest engine-management continuation (2026-10-10)

VS Code background-model compatibility acceptance is tracked in
[RELEASE-READINESS.md](RELEASE-READINESS.md), with configuration guidance in
[VSCODE-UTILITY.md](../../native-core/VSCODE-UTILITY.md). This remains v1.1.0
compatibility work and does not remove single-slot or cancellation safeguards.

The user subsequently brought engine management into the 1.1.0 scope. The older
priority decision below is historical. Managed Windows llama.cpp packages,
Stable/Preview channels, hardware recommendations, safe installation, updates,
rollback/removal, server stop and independent overview status are implemented.
See ../NEXT-STEPS.md and ../../native-core/ENGINES.md for current evidence,
limitations and remaining release gates. This remains a development branch;
no release, main merge or completion of clean-machine acceptance is implied.
# v1.1.0 native migration — current continuation status

**IN PROGRESS. Not release ready. PR #8 is Draft. No acceptance gate is waived.**

## User priority decision (2026-10-09)

The user explicitly changed phase priorities: finish Python -> Rust and C# ->
C++/WinRT language migration, five-page GUI/Core integration and zero-regression
acceptance first. A new llama.cpp/CUDA download/install/version/rollback UI and
Runtime Package Manager are **deferred to a separate later project** and are
not blockers for completion of this language-migration phase. Existing manual
engine_dir, load/unload, GPU offload, Context, KV, MTP and Vision behavior must
remain intact. Fake Engine/Windows GUI acceptance comes first; actual CPU/CUDA
hardware acceptance remains a later user-machine gate, never assumed green.
No main/tag/release change is authorized. PR #8 stays Draft.

## Current editor/lifecycle continuation (after 2b4887d)

### Implemented

- Model library, profiles and system page builders replace the remaining empty
  PageHost branches. The frozen 20/10/12 field schemas, labels, sections, help,
  native controls, sliders, optional values and dependency predicates are ported.
- Search/selection, GGUF/folder pickers, model scan/add/remove/default/load,
  profile add/duplicate/delete, location management, connection diagnostics and
  settings import/export are connected to the real Rust management endpoints.
- Separate drafts preserve unknown fields. Saves GET latest, merge only changed
  fields, reject same-field conflicts before PUT, publish normalized PUT values,
  preserve drafts on failure and distinguish persistence from status refresh.
  Navigation retains save/discard/cancel, and polling/reconnection retains drafts.
- Win32 tray menu, close-to-background, SHA-256 data-directory singleton/reveal,
  hidden startup and owned/attached Core recovery are implemented. Development
  startup registration deliberately uses AMIEBL.Native.Development instead of
  overwriting the supported v1.0.0 Run value; production key migration remains
  an explicit packaging gate.
- Native integration tests exercise actual controls, dialogs, window messages,
  second-process startup and Core restart; no GUI or management API is mocked.
- Build-Development.ps1 prepares isolated GUI/Core development artifacts without
  Python/.NET or engine payloads. Another machine may still need the VC runtime;
  this is not installer or clean-machine acceptance.

### Tested

- Release x64 C++/WinRT build and native window/resource startup pass locally.
- Full Python/reference/native regression: **217 tests pass in 351.472 s**,
  with both AMIEBL_REQUIRE_GUI_TESTS and AMIEBL_REQUIRE_NATIVE_TESTS enabled.
  All five real native GUI integration cases pass; no reference assertions or
  Fake Engine behavior were changed. The earlier reference port-rebind flake
  did not recur in this run; this does not prove it cannot recur.
- Desktop Core and Platform regression executables pass independently.
- GUI cases exercise 20/10/12 editor fields, dependency states, optional values,
  concurrent changed-field merge, same-field conflict, numeric validation,
  unknown fields, save/discard/cancel navigation, profile tools/add/duplicate,
  system persistence, close-to-background, second-process reveal, attached Core
  protection, owned Core restart and retained drafts.
- Sanitized three-page screenshots and lifecycle results are retained locally.
  git diff --check passes.
- The follow-up Unicode whitespace/ordinal-search and save-state fixes pass
  a fresh Release x64 build and all five GUI cases in **32.209 s**. Tests now
  include non-ASCII whitespace, accented case-insensitive search and actual
  profile-deletion cancel/confirm controls. Startup registration failure keeps
  the draft dirty; registry-denial simulation remains unverified.
- Build-Development.ps1 produced Release GUI + Release Rust Core artifacts
  at 68b951d. That package passes startup and all five real GUI cases in
  **31.100 s**. No Python/.NET runtime, model, engine, token or personal
  configuration payload is included. Final follow-up artifacts are rebuilt
  and rechecked separately; no clean-machine claim follows from this.
- Code checkpoint 68b951d is pushed. Its migration workflow
  [37917596449](https://github.com/ghgf566/AMIEBL/actions/runs/37917596449)
  and release-safety workflow
  [37917603065](https://github.com/ghgf566/AMIEBL/actions/runs/37917603065)
  pass. CI's native GUI job runs five cases successfully in 28.960 s;
  RustSec passes. Its CodeQL workflow
  [37917603119](https://github.com/ghgf566/AMIEBL/actions/runs/37917603119)
  has successful C#/Python jobs; C++ analysis is still running at this update.
  A later follow-up SHA must receive its own CI result; these are not its checks.

### Final follow-up evidence at code checkpoint 387f9d3

- Latest code is pushed at **387f9d3b9e481137d1707ace2bfb9ce9f17598a0**.
  A fresh complete local run passes **217/217 tests in 345.882 s**, with
  required native and GUI cases enabled. No reference expectation was changed.
- The rebuilt Release GUI + Release Rust Core development package passes
  native startup and all five actual GUI cases in **31.948 s**. All packaged
  file hashes match SHA256SUMS. It contains no GGUF, engine, admin token,
  personal configuration, Python runtime or .NET runtime.
- Package source is 387f9d3; ZIP SHA-256 is
  `a53a27dd5fe2d1bc891224f0282542a0aa75ca904706dff397a863a03d5f5fde`.
  It is development-only and does not prove clean-machine installation.
- 387f9d3 migration workflow
  [37918578929](https://github.com/ghgf566/AMIEBL/actions/runs/37918578929)
  and release-safety workflow
  [37918585801](https://github.com/ghgf566/AMIEBL/actions/runs/37918585801)
  pass. The real native GUI CI job passes five cases in **30.357 s**.
  RustSec passes. Its CodeQL workflow
  [37918585816](https://github.com/ghgf566/AMIEBL/actions/runs/37918585816)
  passes all three C#/Python/C++ jobs, including real C++/WinRT compilation,
  extraction, analysis and results upload. All three workflows for the code
  checkpoint are successful.
- The earlier 68b951d CodeQL workflow is now fully successful, including
  actual C++/WinRT build, C++ extraction, analysis and results upload.
- Subsequent documentation-only commits do not change this tested code or
  package source. Their separately triggered checks must not be represented
  as the code checkpoint's checks.

### Confirmed equivalent (finite cases)

The tested merge/conflict/unknown-field, optional sampling, tools-array,
navigation decision and Core ownership cases preserve the frozen reference
contract. Field schemas preserve the original keys, labels, help and predicates.
These finite results do not prove complete product or visual equivalence.

### Pending / unverified

Complete matched-state pixel, DPI, accessibility, keyboard/focus, scrolling,
picker/tray interaction, startup registration and hardware parity remain
**unverified**. Full import/export/scan/removal/default-selection UI acceptance,
all persistence/refresh failure permutations and editor animation timing need
broader coverage. Production startup-key migration and clean-machine native
dependency/installer acceptance remain pending. Native C++ CodeQL passes at
the tested code checkpoint; this does not prove complete security or UI parity.
No milestone A-E or complete product equivalence is declared merely from a build.

## Historical GUI/Core checkpoint at 2b4887d


Remote branch and Draft PR were rechecked before work; no newer agent commit
was present. Existing ten frozen-reference captures remain available in the
local acceptance output. No main/tag/release change is authorized or made.

- Real native overview and tasks/history controls replace those two empty
  pages. Core transport, management-token authentication, protocol-1 health
  header, owned process Job and attached-service protection are implemented.
- Load/unload, pause/resume, persisted keep-loaded toggle, task list/detail,
  active SSE cancellation with upstream DELETE and confirmed slot release are
  exercised through actual WinUI automation peers and the real Rust service.
- Fixed confirmed Windows HTTP caching defect that returned stale unloaded
  state after a successful engine load; both cache read/write are disabled.
- Service log SmoothExpander uses the original 260 ms smoothstep layout
  animation and SDK header style. Tests verify mid-animation reversal without
  a height discontinuity and completed expansion/collapse. They do not prove
  full visual, accessibility, timing-frame or all-DPI equivalence.
- Added Windows CI GUI/Core integration plus sanitized screenshot/test artifact
  upload. Existing Python/C# baseline and production deployment are retained.

Local continuation evidence: Release x64 native build and window startup pass;
Rust formatting/clippy pass; all 19 Rust unit tests pass; Desktop Core and
Platform regression pass. GUI owned/attached operations pass. Full regression
ran 213 tests: 212 passed, one unchanged Python reference reload scenario
returned 502 because its owned engine port had not become reusable on Windows.
The unchanged case passes on isolated rerun; its Rust counterpart passes in
the full run. This is retained as an unresolved reference port-rebind flake,
not hidden or interpreted as a full-green run. Windows CI will independently
reproduce the expanded suite and RustSec check on the pushed commit.

Final local rerun on code commit `ed12cd70e38cc8a328b451e2c3217d3fd4576067`
passes **215 tests** (335.064 seconds), including all three GUI owned/attached/
incompatible-service cases. The earlier reference port-rebind failure remains
recorded above. GitHub run 37909747305 builds and starts the native GUI but
fails the GUI/Core integration step: both operation cases reject an immediate
collapse as a height discontinuity. The test originally applied animated
reversal continuity to reduced-motion systems. The acceptance harness now
checks the captured animation origin only when reversing an active animation;
when Windows animations are disabled it requires immediate completed collapse.
It records animations_enabled and expander_reversal_checked explicitly.
The new CI rerun must confirm this diagnosis; no product animation policy or
parity requirement is weakened.
Its RustSec check passes. CodeQL 37909754013 and frozen release-safety
37909754040 pass; neither establishes native GUI parity or packaging.

The ten recovered reference captures, original animation measurements and
SHA-256 manifest are now preserved under `baselines/v1.0.0/`. Capture build
metadata is a3440cb; desktop source trees were checked identical to v1.0.0.
All-DPI, interaction video and remaining states are still absent.

Final code commit `3db8f8a60b676327b184afa7ac8ac045d3a1cf96`:
- Migration CI [37911508437](https://github.com/ghgf566/AMIEBL/actions/runs/37911508437)
  succeeds: frozen reference/native regression, RustSec, Release x64 native
  build, window startup and all three real GUI/Core integration cases.
- CodeQL [37911515753](https://github.com/ghgf566/AMIEBL/actions/runs/37911515753)
  and frozen release-safety [37911515575](https://github.com/ghgf566/AMIEBL/actions/runs/37911515575)
  succeed. Frozen release-safety still does not test native installation.
- Local rebuild and three GUI cases pass again after the harness correction.
  Local JSON reports animations_enabled=true and expander_reversal_checked=true.
- Frozen original WinUI Test-Desktop rerun passes save/switch/restore, all five
  reference pages and owned shutdown, recreating screenshots/animation evidence.
- main/tag remain eab5dc1; PR #8 stays Draft. No production GUI was removed.

These successful checks close this specific integration/harness stage. They
**do not** close complete Core/UI parity, native deployment or milestones A-C.

**Not complete:** models/profiles/system page builders, all editors and dirty
state, tray/background close, single instance, complete reconnection/timeouts,
all DPI/keyboard/manual visual acceptance, real CPU/CUDA inference, independent
runtime package manager and native installer/clean-machine acceptance. Wired
VS Code/dialog/log actions still require broader interactive verification.
Only tested overview/task operations are accepted at this stage; no complete
milestone A/B/C or product parity claim is made.

## Actual runtime milestone

The new `native-core/src/runtime.rs`, `runtime/watch.rs` and
`src/bin/amiebl-core.rs` implement an actual Tokio/Axum inference service,
separate from the unchanged read-only preview. Explicit development opt-in
and an isolated data directory are required. Production packaging still uses
the frozen Python/C# reference; this milestone does not replace it.

Implemented and wired through the service:

- Original 22 HTTP method/route combinations, loopback binding, management
  token/origin checks, config/import/export, GGUF scanning/capabilities,
  records/log clearing, connection and VS Code preview/apply.
- Bounded admission (32 including the active ticket), one inference worker,
  model/profile snapshots, request validation and the verified `request.rs`
  policy, terminal records/history/pruning and opt-in bounded body logging.
- Real upstream OpenAI nonstream/SSE transport, conversation/request headers,
  fragmented UTF-8/tool argument preservation, streaming backpressure,
  token/timing observation, original thinking/sampling/client override rules.
- Owned engine child launch, argument generation (CPU/GPU/vision/native or
  external MTP), fit process/timeout, GGUF prevalidation, health/props readiness,
  runtime capability persistence, unload/reload/deferred unload and idle handling.
- Cancellation in queue, loading, preheaders, streaming and client disconnect;
  upstream DELETE replay, slot-idle verification and owned-child fallback
  before the worker advances. No inference is automatically resent.
- VS Code initial log skip, recent-file discovery, new/rotated-file tail replay,
  partial-line handling, timestamp filtering and active-only cancellation.
  Windows RAM and nvidia-smi GPU observation; background work is joined on exit.

`storage.rs` adds atomic capability persistence without creating user-edit
backups. Earlier native source edits otherwise mainly apply whole-crate
rustfmt and four behavior-preserving Clippy diagnostics fixes.

The existing 64 Fake Engine HTTP tests run against Rust by changing only the
manager launch command. Eight additional tests execute four identical live
scenarios against Python and Rust: log history/stale/partial events, new-log
rotation with queued work, slot handoff after cancellation and crash/reload.
Original backend code, fake engine and test assertions remain unchanged.

## Historical native shell checkpoint (before ed12cd7)

`native-gui/AMIEBL.Native.vcxproj` is a real C++20/C++/WinRT WinUI 3 target
with app-local Windows App SDK. Its MainWindow XAML is copied from v1.0.0
with only the class namespace changed. The native window compiles, initializes
its XAML/resources and closes in an actual process startup test.

**The PageHost is still empty. Five page builders, editors, SmoothExpander,
navigation animation, tray, dirty drafts, focus/DPI, Windows integration and
GUI–Core transport have not been ported. The native shell is not UI parity and
must not replace or be packaged as the existing C# GUI.**

The frozen C# WinUI build and its actual desktop regression passed locally.
Ten rendered captures (five pages plus expanded/compact states) and the JSON
interaction/animation measurements were saved as local acceptance artifacts.
They establish the reference, not native equivalence. Full DPI captures,
recordings, pixel comparisons and human acceptance remain outstanding.

## Validation and reproducibility

Local Windows x64 checks for the new milestone:

- Locked native build: passed.
- Rust unit tests: 19 passed.
- Whole-crate rustfmt check and strict all-targets Clippy: passed.
- DesktopCoreRegression and DesktopPlatformRegression: passed.
- C++ Release x64 compile and actual native startup/close: passed.
- Frozen WinUI desktop interaction/render/animation regression: passed.
- All 73 runtime scenarios passed locally after the bounded owned-port fix:
  64 unchanged Rust/reference assertions, one 20-cycle Rust reload stress case,
  plus four edge scenarios executed against both Python and Rust.
- Full 210-test discovery at implementation commit `05b9347` passed 209; the
  frozen Python same-model explicit-reload case failed with a local Windows
  port conflict. Its isolated repeat reproduced the failure. Backend/reference
  assertions remain unchanged. Rust passed all cases in that run.
- GitHub independently passed the full 210-test suite, native GUI build/startup
  and RustSec audit at `05b9347`: [migration run](https://github.com/ghgf566/AMIEBL/actions/runs/37899870165).
  [Release safety/reference builds](https://github.com/ghgf566/AMIEBL/actions/runs/37899878182)
  and [CodeQL](https://github.com/ghgf566/AMIEBL/actions/runs/37899878180) also passed.
  Those release builds package the old Python/C# product, not the native target.
- The subsequent reload fix `b962765` adds a 211th full-suite test. All 211
  passed locally (315.161 seconds). The previously failing frozen Python case
  passed this run, but its independently reproduced intermittent failure
  remains recorded. All three workflows also passed at this exact code commit:
  [migration, including 211 scenarios/RustSec/native GUI](https://github.com/ghgf566/AMIEBL/actions/runs/37901334001),
  [release safety/reference packaging](https://github.com/ghgf566/AMIEBL/actions/runs/37901339372),
  [CodeQL](https://github.com/ghgf566/AMIEBL/actions/runs/37901339078).
  The final status update is documentation only; validated runtime/GUI sources
  are unchanged from `b9627659b8e919d39345222bb1851553c68f1e1e`.

The first runtime run passed 63/64 reference scenarios, failing same-model
reload. Subsequent stress/full runs reproduced Windows port rebind failures
in both the frozen Python reference and Rust. Rust now uses the reference
exclusive bind probe, does not retain idle engine sockets, and recognizes a
just-terminated **owned** original port after a bounded two-second exclusive
bind recheck or a refused TCP connection. A connection timeout is never treated
as availability. A 200-ms connection probe alone was insufficient on this host;
additional stress reproduced that defect after the first green CI, so the
bounded recheck and 20-cycle regression were added.
External/new ports still require the normal conflict check; no external
process is stopped. Repeated runtime shutdown also exposed one temporary log
lock; background resource observation is now joined before exit. One later
queued-cancel run raised a Windows socket ReadError; isolated repeats passed.
These observations are retained, not hidden by changing reference assertions
or retrying inference. The local frozen-reference intermittent failure has
not had its root cause fixed; the latest full local run passed.

The migration workflow now requires the actual runtime executable (missing
binary is a failure), runs both services' scenarios, whole-crate fmt/Clippy,
adds a RustSec locked-dependency audit, and builds/exercises the native GUI.
Green checks remain finite coverage, never a migration-complete declaration.
Local C++ tooling was assembled under the isolated work directory from official
Visual Studio catalog packages with SHA-256 checks because this host lacked
UWP/XAML build support. No shared Visual Studio installation was changed.
GitHub's Windows runner independently reproduced the ordinary project build
and native startup test at the implementation commit.

## Remaining release blockers and next work

1. Expand lifecycle/cancellation stress coverage: cancellation with shared
   loading, unsupported DELETE/slots and forced owned termination, fit failure/
   timeout, 300-second readiness timeout, queue saturation, shutdown races,
   persistence failures and every API rejection/header/large-body case.
2. Verify with real llama.cpp CPU/CUDA/GGUF generation and cancellation,
   capability differences and full Windows owned-process/GUI lifetime. Fake
   Engine HTTP evidence does not certify real model/hardware behavior.
3. Port the full five-page C++ GUI and all editor/animation/tray/Windows behavior
   against frozen screenshots and interaction measurements; retain C# until
   complete visual and end-to-end acceptance.
4. **Deferred separate project per user decision:** separately versioned CPU/CUDA runtime package manager:
   trusted source, SHA-256, resumable download, atomic install, corrupt archives,
   rollback/offline use and external-engine/model path protection.
5. Complete versioned GUI–Core negotiation, native Portable/Setup packaging,
   app-local CRT/App SDK dependency audit, clean Windows 10/11 installation,
   uninstall/data protection, DPI/accessibility/human acceptance and licenses.

No main merge, tag mutation, release publication, or relaxed parity requirement
is authorized. v1.0.0 remains the supported usable product.
