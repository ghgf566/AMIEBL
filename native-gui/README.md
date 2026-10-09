# Native GUI migration build target — not a replacement GUI

This is a real C++20/C++/WinRT WinUI 3 executable, with app-local Windows App SDK
and the frozen v1.0.0 MainWindow XAML shell (only its class namespace changes).
It establishes the native XAML build, resource/deployment pipeline and original
navigation layout. It does **not** implement the five page builders, editors,
SmoothExpander, tray, draft state, or GUI–Core transport yet. The empty PageHost
is an explicit migration gap, not a functional page or a parity claim. Never
package this target as LocalModelManager or replace the C# application with it.

Build with Visual Studio C++ desktop/Windows SDK tools:

```
msbuild native-gui/AMIEBL.Native.vcxproj /restore /p:Configuration=Release /p:Platform=x64
```

Build output is isolated under ignored `build/native-gui/`. Project deployment
follows Microsoft's [self-contained C++ WinUI sample](https://github.com/microsoft/WindowsAppSDK-Samples/tree/main/Samples/SelfContainedDeployment/cpp/cpp-winui-unpackaged).
Baseline captures and full interaction/animation parity gates remain required
before the old GUI is retired; the shell is not evidence those gates passed.
