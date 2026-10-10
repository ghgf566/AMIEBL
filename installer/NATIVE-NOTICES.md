# AMIEBL v1.1.0 native distribution notices

AMIEBL is licensed under Apache License 2.0 (included LICENSE).
The native GUI uses Windows App SDK / WinUI 3 and app-local Microsoft Visual C++ redistributable runtime.
Their Microsoft licenses and redistribution terms remain applicable:
https://github.com/microsoft/WindowsAppSDK
https://aka.ms/vs/18/redistribution

The Rust Core dependency versions are locked in native-core/Cargo.lock.
Dependency license files and metadata are collected in third-party/ during packaging.
Inno Setup installer/uninstaller code and the translated messages retain their upstream terms:
https://github.com/jrsoftware/issrc/blob/main/license.txt

This distribution does not contain Python, .NET, GGUF models, llama.cpp or CUDA payloads.
Separately installed engines and their runtime components retain their own package licenses.
This summary does not replace the included upstream license texts.
