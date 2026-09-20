' Arranca MicroZohm Scope sin ventana de consola (pythonw.exe). Errores en outputs\mzscope.log.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw -m mzscope", 1, False
