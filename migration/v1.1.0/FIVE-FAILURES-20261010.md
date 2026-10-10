# Five reported failures: resolution (2026-10-10)

| Reported failure | Evidence and resolution |
| --- | --- |
| `bad_budget` | v1.0 rejects a custom budget above the profile cap; approved v1.1 policy validates its own range and clamps against the model/client allocation per request. Preserve the old rejection assertion and explicitly construct the new expected normalized result. |
| `model_with_inactive_optional_paths` | Compare the complete frozen normalized result plus exactly the approved `output_percent: 25` default. Inactive paths and unknown properties remain checked. |
| `repaired_old_disk_context` | Apply the same explicit field-default expectation to startup normalization; retain all other migration assertions. |
| Ratio slider allowance | Current version already passed after allowing asynchronous TextChanged completion. Replace the fixed delay with a bounded wait for draft and live allowance changes, retain the 8192/50% -> 4096 allowance assertion, and additionally assert persisted percentage 50. |
| Draft after Core reconnect | Reproduced failure reported same editor=true, dirty=false, context=4096. Autosave had retained the value and cleared dirty; this was not lost content. Verify editor identity/value and require persistence if clean. Also reconnect with an invalid unsaved value and require dirty draft/control retention with unchanged stored config. |

Validation: rebuilt native GUI and ran the full native config and VS Code
differential modules plus editor and lifecycle GUI acceptance: 10 tests passed
in 13.523 seconds. All original config scenarios remain; no frozen Python edits.
GUI includes real shutdown/recovery, tray hide/reveal and secondary-instance flow.
Evidence was collected locally under `local-handoff/logs/five-failures-final/`.

This resolves the five listed failures, not all release gates. It does not claim
manual UI parity, clean-machine installation, or all-GPU/model acceptance.
