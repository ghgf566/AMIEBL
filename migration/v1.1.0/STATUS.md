# v1.1.0 native migration — continuation status

**IN PROGRESS. Not a usable replacement, not release ready.**

This report records the 2026-10-09 continuation from branch head
`70731a02610f2730c1d6b666bc3f94ec8979563b`. The governing acceptance criteria
remain [PARITY_CONTRACT.md](PARITY_CONTRACT.md); no feature or gate is waived.

## Frozen reference

- `main` and the commit behind the annotated `v1.0.0` tag both resolve to
  `eab5dc1451bf38d1c008558ba8d49c85954c04bb` at the start of this continuation.
  The tag object itself is `ad2609441aee94d53be6e31e2eb19352195a1df0`.
- `backend/manager.py` on the migration branch is identical to the tagged
  source. The new request differential suite verifies its SHA-256 after
  normalizing checkout line endings:
  `0e91761e818b8974d3a0b455eaa4e9a3d6e36ff71052b216361eac57e75d79e7`.
- The previous head's frozen-reference, release-safety/build and CodeQL
  workflows passed. Those results establish the previous reference checks,
  **not** full Rust/C++ equivalence.

## Native code now available

| Area | Implemented migration unit | Boundary |
| --- | --- | --- |
| Configuration | Schema/defaults/startup normalization, unknown fields | Existing differential tests; not a complete manager config mutation API |
| Storage | Atomic settings/token storage and backups | Startup capability reconciliation and history side effects still need runtime integration |
| VS Code | JSONC, owned models, agent files, backups | Existing differential tests; continuous watcher and full lifecycle still pending |
| GGUF | Metadata, split shards, reasoning/MTP capabilities | Existing differential tests; real engine `/props` integration still pending |
| Inference request policy | Validation/error order; model/alias/header/agent marker resolution; output caps; sampling; classifier; native efforts; thinking switches; client overrides; auto/custom budgets and output reserve | New `request.rs` and `request-probe`; shared pre-inference logic, **not** an inference worker or transport |
| HTTP | Four read-only routes in an isolated preview | Refuses production port/existing config; no inference, lifecycle, or mutations; never packaged |

The request suite executes actual Python `submit()` and `policy()` on the same
JSON as Rust, compares complete forwarded bodies, model/profile snapshots,
record fields and rejection messages. Only measured classifier elapsed time is
allowed to vary; its native value must still be finite and nonnegative.
Multimodal content, tools, unknown fields, Unicode markers, integral float
settings, and the frozen classifier's literal regex escapes are characterized.
The required CI step fails if its probe is missing instead of reporting a skip.

`native-core/Cargo.lock` is committed, CI uses `--locked`, and native build
outputs are ignored. Native Rust source under `src/bin/` is explicitly tracked.
No Python/C# baseline behavior or production packaging is replaced in this step.

## Local validation (Windows x64, 2026-10-09)

- `cargo build --locked --manifest-path native-core/Cargo.toml --bins`: passed.
- `cargo test --locked --manifest-path native-core/Cargo.toml`: **19 passed**.
- Required request differential suite: **7 tests, 500 complete comparisons**,
  passed without skips.
- Full Python/native/reference regression discovery: **138 passed**, no skips,
  in 185.772 seconds. This includes the old fake-engine integration cases and
  existing config, VS Code, GGUF, preview, baseline and packaging checks.
- C# DesktopCoreRegression and DesktopPlatformRegression: both passed. These
  remain frozen-reference checks, not C++ UI acceptance.
- The two HTTP preview tests additionally passed with ResourceWarning treated
  as an error after closing HTTPError responses explicitly in their helper.
- Targeted native source formatting and `git diff --check`: passed.

An earlier standalone run of the 64 reference manager integration tests had
one 10-second model-reload timeout. The same test passed on an isolated rerun
and in the subsequent full 138-test run; no frozen backend code was changed.
This observation is retained for future native lifecycle/stress acceptance,
not treated as evidence of full runtime equivalence.

## Remaining blocking work

1. Real Tokio runtime: bounded single-slot queue, task records/history/privacy,
   worker/backpressure and cancellation in **every** phase. HTTP management
   methods, errors, auth/origin checks and persistence side effects must match.
2. OpenAI gateway: streaming and nonstreaming, fragmented UTF-8/SSE/tool calls,
   IDs, upstream failure handling, disconnect cancellation, no duplicate
   inference, `DELETE /v1/stream`, slot verification and owned-process fallback.
3. Engine: load/unload/reload/deferred actions, fitting, CPU/CUDA configuration,
   runtime capability refinement, monitoring and safe owned-process lifetime.
4. Versioned independent engine installer: checksum/provenance, CPU/CUDA DLLs,
   download/extract/install/update/rollback, offline use and custom-directory
   protection, with corrupt archive/failure acceptance tests.
5. C++20/C++/WinRT WinUI 3 implementation: all five pages, editors, animations,
   draft state, focus/accessibility, DPI, pickers/dialogs, tray and Windows
   integration. No native GUI implementation exists yet in this branch.
6. Frozen baseline screenshots/recordings and complete native UI/UX interaction
   comparisons; GUI–Core–engine end-to-end acceptance.
7. App-local/self-contained Windows App SDK, native installer/portable delivery,
   clean Windows 10/11 tests without Python/.NET/shared App SDK, security,
   dependency/license and exact packaged-asset checks.

The next runtime implementation must call the verified request policy from the
real worker, then compare the native service and frozen Python service against
the same fake engine, including queue/cancellation/failure cases. Expanding the
isolated read-only preview is not a substitute for that acceptance gate.

**No main merge, release, or migration-complete claim is authorized by these
component checks. v1.0.0 remains the usable baseline.**
