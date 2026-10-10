# v1.1.0 Release Readiness

This is a development-branch acceptance ledger, not authorization to publish a release. Other migration and clean-machine gates in STATUS.md remain applicable.

## VS Code background model compatibility — 2026-10-10

- Audited Rust admission, serial worker, model reuse and cancellation: no special rejection of first chats, duplicate same-model calls, titles or summaries. No concurrency restriction was removed.
- Corrected the modern synchronization preview to explain utility routing. Added [configuration and diagnostic instructions](../../native-core/VSCODE-UTILITY.md). User settings and explicit utility choices remain untouched by synchronization.
- Microsoft utility resolver supports `chat.utilityModel`, `chat.utilitySmallModel` (`vendor/id`) and fallback `chat.byokUtilityModelDefault=mainAgent`. Explicit overrides win. The test machine's initially empty settings were backed up locally before selecting `mainAgent`.
- Regression test `test_first_agent_title_and_summary_share_loaded_single_slot`: first streamed call, queued background title, then summary all succeed, three requests, one engine start, `-np 1`, summary Thinking off.
- Regression test `test_background_burst_keeps_bounded_queue_and_cancellation`: active + 31 queued tickets; next request returns 429, cancelled waiting requests never reach the engine.
- Validation: 33 Rust unit tests; Clippy with warnings denied; 67 full native HTTP integration tests before adding the queue test; both new tests passed individually; 7 frozen VS Code parity tests passed. Frozen reference implementation and CI workflow are unchanged.

### Real VS Code evidence

Installed VS Code 1.141.0 / Copilot 0.69.0. Test machine: i5-12500H / Iris Xe. Current registered models: Qwen3.5 4B and 2B UDQ4KXL with MTP, 0.8B BF16 without MTP; all 32768 Context / 20% output. Model list synchronization completed with backups. Model sizes/MTP differ, but utility routing uses each registered physical id.

An earlier Copilot CLI/agent-host new-chat attempt returned `No response was returned` without a new accepted Core ticket. This is not evidence of Core title/summary rejection. After synchronization and selection of the **Local** chat path, the first 2B request was accepted at 16:32:13 Taipei (Core ticket `89e2756a-1d9a-4f38-b3a9-427f1f41116a`); a second same-model request queued at 16:32:14 (`d7a63d9b-8a95-4ce0-a30d-d2088119820c`).

**PASS: real new Agent chat, first answer and automatic title.** The title changed from the original English user prompt to `植物需要陽光解釋`. Copilot log `8ee614de` reports success/stop at 16:38:13, and `b7466ecf` reports success/stop at 16:39:06, followed by ToolCallingLoop completion. VS Code displays the two-sentence photosynthesis answer and `Completed 2 steps in 6m 53s`. Both Core tickets completed without errors (360.628 and 411.801 seconds respectively, including queue time); server PID 6664 remained loaded, then active/queued counts returned to zero. In this version the title runs before the main answer; no assumption of answer-first ordering is required. Title Thinking used most of its 1889-token budget, so background generation can add substantial latency on this hardware. No speculative title detector or summary-policy removal was introduced.

Additional checks: 500 complete frozen Python/Rust request-policy comparisons passed; new native GUI/Core development package built successfully. Screenshots are saved locally in `local-handoff/utility-evidence` and excluded from Git along with personal settings.

**PASS: explicit utility selections and no-MTP model.** Temporarily set both utility selectors to `customendpoint/llama.cpp/model-d21c3c3f` and selected 0.8B in a new Local Quick Chat. The title changed to `一句話，請打招呼`, the answer was `你好！`, and VS Code displayed `Completed 2 steps in 3m 45s`. Core tickets `803a2c55-f44b-4597-a8f8-b41e8dfe7fd2` and `d31b9e82-17f1-45ce-9827-860aae8c9efa` completed (208.071 and 224.129 seconds including queue time). Copilot reports success/stop for model-d21c3c3f, with no utility override lookup error. Both selectors were configured together; the test does not independently attribute every auxiliary call to a particular utility family. Microsoft resolver source confirms each family's setting path. User settings were restored to `mainAgent` without fixed utility overrides afterward; model Context/MTP/profile settings were preserved.

A direct HTTP request with the recognized Copilot summary envelope against the same real 0.8B model returned a normal `stop` response. This supplements the fake-engine summary regression; it does not claim automatic Copilot compaction was triggered in this short UI conversation.

Local raw logs/settings backups remain under ignored `local-handoff`; admin tokens and personal prompts are excluded from Git. Core request records distinguish accepted waiting/generating/error/completed states. For pre-admission rejection or client-side failures, correlate HTTP status and VS Code logs as described in VSCODE-UTILITY.md; absence of a Core ticket alone cannot prove that VS Code never sent HTTP.
