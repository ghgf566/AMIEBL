# Experimental inference runtime

`amiebl-core` is the actual Tokio/Axum migration service. It is separate from
the unchanged, isolated, read-only `amiebl-core-preview`. Neither is included
in production packaging. Complete migration acceptance is still blocked by
the remaining gates in `migration/v1.1.0/STATUS.md`.

```
cargo build --locked --manifest-path native-core/Cargo.toml --bins
native-core/target/debug/amiebl-core --experimental-runtime --data-dir <isolated-test-data> --port <test-port> --engine-port <test-engine-port>
```

The runtime reads the existing config schema and exposes the original 22 HTTP
method/route combinations on IPv4 loopback. Management routes require the
existing `admin-token` file. The worker calls the verified `request.rs` policy;
it owns queue admission, engine loading, upstream HTTP/SSE, terminal history,
and cancellation cleanup. A cancelled active request cannot hand the single
slot to the next ticket before upstream DELETE/idle verification or owned
engine termination completes. Client handler/body drop cancels the ticket.

`LMM_ENGINE_COMMAND_JSON`, `LMM_SKIP_FIT`, `VSCODE_LOG_ROOT`, and
`VSCODE_ABORT_LOG_WATCH` retain their reference meanings. Engine overrides are
used by the frozen fake-engine fixtures; production runtime packaging must
not depend on those Python fixtures.

```
# First install backend/requirements.txt for reference fixtures only.
AMIEBL_REQUIRE_NATIVE_TESTS=1 python -m unittest discover -s tests -p 'test_native_runtime*.py' -v
```

`test_native_runtime_integration.py` reuses all 64 original reference HTTP
assertions, changing only the manager launch command, plus a native-only
20-reload regression that verifies one engine start and one upstream inference
per request. `test_native_runtime_edges.py`
executes the same additional live cancellation/log-rotation/crash scenarios
against both services. No backend tests or fake-engine behavior are weakened.
These scenarios are finite coverage, not proof of every v1.0.0 behavior.

Formatting and strict Clippy now cover the whole native crate. Most edits to
earlier modules in this milestone are mechanical rustfmt changes; the storage
runtime-persistence method and the new runtime are the functional additions.

## Native GUI protocol handshake

The experimental runtime adds `X-AMIEBL-Core-Protocol: 1` to `/health`.
The original JSON response keys and public API behavior remain unchanged.
The native development GUI checks this header before authenticated attachment;
it does not stop an incompatible service or an attached compatible service.
This is an additive development handshake, not a production compatibility gate.
