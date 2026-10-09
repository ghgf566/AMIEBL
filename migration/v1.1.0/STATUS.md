# v1.1.0 native migration — current continuation status

**IN PROGRESS. Not release ready. PR #8 is Draft. No acceptance gate is waived.**

Continued on 2026-10-09 from verified remote head
`a3440cb089d26e49b9a4dfa6c8ca8908d7708e03`. No intervening agent commit
was present. `main` and the commit behind `v1.0.0` remain
`eab5dc1451bf38d1c008558ba8d49c85954c04bb`; neither is modified.
[PARITY_CONTRACT.md](PARITY_CONTRACT.md), `CONTRACT.md`, and
`DESKTOP-ARCHITECTURE.md` remain the governing requirements.

## GUI/Core continuation from 7d98c3a (2026-10-09)

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

## Native GUI and visual baseline

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
4. Implement the separately versioned CPU/CUDA runtime package manager:
   trusted source, SHA-256, resumable download, atomic install, corrupt archives,
   rollback/offline use and external-engine/model path protection.
5. Complete versioned GUI–Core negotiation, native Portable/Setup packaging,
   app-local CRT/App SDK dependency audit, clean Windows 10/11 installation,
   uninstall/data protection, DPI/accessibility/human acceptance and licenses.

No main merge, tag mutation, release publication, or relaxed parity requirement
is authorized. v1.0.0 remains the supported usable product.
