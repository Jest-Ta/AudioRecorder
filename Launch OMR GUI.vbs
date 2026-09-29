Option Explicit
Dim shell, fso, here, preferred, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
preferred = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe")
If fso.FileExists(preferred) Then
    pythonw = preferred
Else
    pythonw = "pythonw.exe"
End If
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & here & "\Main.py" & Chr(34)
shell.Run command, 0, False
