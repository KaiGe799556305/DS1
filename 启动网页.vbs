Option Explicit

Dim shell, fso, root, pythonExe, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

pythonExe = "python"
command = "cmd /c cd /d """ & root & """ && " & pythonExe & " server.py"

shell.Run command, 0, False
WScript.Sleep 1500
shell.Run "http://127.0.0.1:4173/analyze", 1, False
