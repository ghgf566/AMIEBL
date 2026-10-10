# VS Code background model requests (v1.1.0)

AMIEBL registers physical models through the `customendpoint` provider. Registration alone does not select Copilot's utility models. In VS Code user settings, use:

```json
{"chat.byokUtilityModelDefault": "mainAgent"}
```

Select the AMIEBL model in a **Local** Agent chat. Utility and small utility requests then follow the selected main model. Existing explicit `chat.utilityModel` and `chat.utilitySmallModel` selections take precedence; select the local model in those settings' model pickers if explicit routing is desired. Their storage format is `vendor/id`, including the provider's full model id (for this registration, `customendpoint/llama.cpp/<model-id>`), not the display name. Do not copy a stale model id after replacing models. Synchronize the model list and reload VS Code first.

AMIEBL synchronization preserves user settings and existing utility choices; its preview explains this configuration. It does not silently switch other providers to a local model. `mainAgent` follows the main model, so selecting a cloud main model also changes the fallback destination.

Microsoft implementation: <https://raw.githubusercontent.com/microsoft/vscode/main/extensions/copilot/src/extension/prompt/vscode-node/endpointProviderImpl.ts> (utility overrides, `vendorAndId`, and `none`/`mainAgent`/`copilot` fallback). Behavior is version dependent; verified against installed VS Code 1.141.0 / Copilot 0.69.0.

## Core behavior and diagnostics

There is no first-chat, title, summary, or same-model duplicate rejection. The runtime processes accepted requests through one worker and starts llama.cpp with `-np 1`. At most 32 tickets (including the active ticket) are admitted. Paused/stopping managers return 503; a full queue returns 429; malformed or unknown-model requests return validation errors. Cancellation and deferred unload still protect model lifetime. Recognized Copilot summarization retains Thinking off.

Use VS Code's Copilot log together with AMIEBL request records and HTTP status:

| Stage | Evidence |
| --- | --- |
| VS Code did not send | Client/provider error before HTTP; no matching new Core ticket. Absence of a ticket alone is insufficient, because admission errors also have no ticket. |
| Core rejected admission | Actual HTTP validation/429/503 response; no accepted ticket. |
| Accepted but waiting | Core ticket `queued`, then loading/prompt/thinking/generation. A client timeout can occur while a ticket continues or cancels; correlate timestamps, not just task completion. |
| Model generation failed | Accepted Core ticket `error` with detail and engine/client logs. |
| Core succeeded, client failed later | Core `completed`, but VS Code parsing/rendering/response error. This does not mean the whole Agent turn succeeded. |

Prompts are not logged by default. Do not infer a background request's purpose solely from its model id or timing. A chat's initial title may be the user's text; that is not proof of model-generated title success. Local extension-host Copilot chat and Copilot CLI/agent-host chat are distinct request paths; validate the intended Local chat.
