# Automatic thinking policy

Explicit client controls and fixed profile settings retain their existing
precedence. Only automatic thinking uses task classification. Modern normalized
models use this policy; raw legacy request fixtures retain their frozen classifier.

The classifier does not call a model. Compiled, cached regular expressions inspect
task text. Clear greetings and simple language transformations disable thinking;
technical/analytical matches take priority and keep thinking. Ambiguous tasks retain
the selected profile's thinking effort and budget. It never switches profiles.

For Copilot Agent messages, only a unique, complete `userRequest` or `user_query`
pair is classified. Context, attachments and reminders outside it are excluded.
Missing, repeated, conflicting or malformed wrappers fall back conservatively.
Tool-role results and tool continuations keep profile thinking.

Copilot title generation is recognized only when a system message begins with
Microsoft's documented "You are an expert in crafting ultra-compact titles for
chatbot conversations." prompt AND the latest user message starts with one of
the documented brief-title request/conversation prompts (including the older
"chat conversation above" variant). Recognized title generation disables thinking
in Auto mode before classifying the quoted original user request; technical
keywords inside that quoted request must not turn thinking back on. An ordinary
user asking for a title is not enough to trigger this special case.

Copilot conversation summarization is identified using its exact official user
instruction prefix, format instruction, and a system `<summary>` marker together.
Recognized summarization also disables thinking. A user's ordinary request to
summarize does not alone trigger this special case. Unknown auxiliary formats
retain the profile. Fixed profile and explicit client settings keep precedence,
and models without a supported disable control retain their normal capability
handling. Neither detector adds a model call.

History records include `request_kind` and a human-readable `decision` explaining
the choice. These are decisions, not confirmation that a model obeyed them.
Model capability handling and total output/thinking budget clamps still apply.
Native adaptive thinking is deferred.

Format evidence: Microsoft's `agentPrompt.tsx`, `promptRegistry.ts`, and
`summarizedConversationHistory.tsx` under
https://github.com/microsoft/vscode/tree/main/extensions/copilot/src/extension/prompts/node/agent
and the locally installed Copilot 0.69.0 bundle. Title evidence:
https://github.com/microsoft/vscode/blob/main/extensions/copilot/src/extension/prompts/node/panel/title.tsx
and
https://github.com/microsoft/vscode/blob/main/src/vs/platform/agentHost/node/agentHostSessionTitleController.ts
These prompts are conventions, not authenticated metadata. This is a
performance heuristic, not a security boundary or a universal task classifier.
