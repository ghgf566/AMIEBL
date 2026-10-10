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

Copilot conversation summarization is identified using its exact official user
instruction prefix, format instruction, and a system `<summary>` marker together.
Recognized summarization disables thinking. A user's ordinary request to summarize
does not alone trigger this special case. Unknown auxiliary formats retain the
profile; title generation has no speculative detector.

History records include `request_kind` and a human-readable `decision` explaining
the choice. These are decisions, not confirmation that a model obeyed them.
Model capability handling and total output/thinking budget clamps still apply.
Native adaptive thinking is deferred.

Format evidence: Microsoft's `agentPrompt.tsx`, `promptRegistry.ts`, and
`summarizedConversationHistory.tsx` under
https://github.com/microsoft/vscode/tree/main/extensions/copilot/src/extension/prompts/node/agent
and the locally installed Copilot 0.69.0 bundle. These delimiters are prompt
conventions, not authenticated metadata. This is a performance heuristic, not
a security boundary or a universal semantic task classifier.
