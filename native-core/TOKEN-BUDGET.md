# Model context allocation

Each normalized model has an integer `output_percent` (5–95, default 25).
The shared generation ceiling is `floor(context * output_percent / 100)`;
the remaining tokens are the input allowance advertised to VS Code. Input
includes instructions, tools and conversation history. Output includes both
reasoning and the visible answer. This ceiling does not guarantee completion.

The model editor previews both allowances and saves ratio changes automatically.
Use the existing VS Code synchronization action after changing context or ratio
to update its model metadata. Requests do not rewrite `chatLanguageModels.json`.

All usage profiles share this model ceiling. Their old `max_tokens` values remain
as compatibility metadata but no longer cap requests for normalized models.
Profiles still configure thinking strategy, effort and budget. The thinking
budget is clamped against the effective output ceiling with answer space reserved.
Client `max_tokens`, `max_completion_tokens` and `n_predict` can lower that ceiling.

Raw legacy fixtures without `output_percent` keep the previous request and VS Code
allocation behavior for frozen differential tests. The new normalized configuration
contract intentionally adds the model field; the Python reference is unchanged.

The editor and synchronization preview warn when an input allowance is below
4096 tokens. This is a heuristic warning, not a hard minimum or a guarantee.
Copilot also needs input space for tool definitions, instructions, history and
its prompt wrappers. A successful summary request does not mean the subsequent
Agent request succeeded: Copilot may reject that next prompt locally, before
AMIEBL receives it. AMIEBL completion describes only the request it served.
