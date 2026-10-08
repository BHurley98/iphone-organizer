Option Explicit
Dim shell, fso, root, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
command = Chr(34) & root & "\runtime\pythonw.exe" & Chr(34) & " " & Chr(34) & root & "\app\server.py" & Chr(34)
shell.Run command, 0, False
