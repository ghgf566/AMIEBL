# Setup translations

English uses Inno Setup's compiler-provided Default.isl.
Traditional Chinese is vendored from jrsoftware/issrc:
https://github.com/jrsoftware/issrc/blob/6ef32198ef1f7b7b375cd4b6b90896c2a58eb4c2/Files/Languages/ChineseTraditional.isl

Original translator credits are preserved in ChineseTraditional.isl.
The installer script also localizes AMIEBL's custom errors and uninstall shortcut.
Both language paths must compile and pass installer acceptance in native-release.yml.
