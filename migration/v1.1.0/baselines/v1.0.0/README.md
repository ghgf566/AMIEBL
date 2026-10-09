# Frozen desktop visual reference

Ten retained captures and the original WinUI acceptance/animation measurements.
The capture executable reports build commit `a3440cb089d26e49b9a4dfa6c8ca8908d7708e03`.
A current `git diff` against v1.0.0 (`eab5dc1451bf38d1c008558ba8d49c85954c04bb`)
is empty for `desktop-winui`, `desktop-core` and `desktop-platform`; these are
therefore frozen desktop-source reference captures, not native-GUI evidence.

Screenshots render the root at 1224 x 821 pixels. Capture setup uses the real
reference GUI acceptance mode and isolated synthetic fixtures. No production
models, user settings, tokens or engine binaries are included. The manifest
records PNG SHA-256 hashes. State-dependent text/resource counters and InfoBar
visibility must be matched before pixel comparison. These captures alone do
not verify all DPI, errors, keyboard/accessibility, tray or real inference.
No interaction video was retained; that remains an acceptance gap.
