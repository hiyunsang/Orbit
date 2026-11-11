' MAENG_worklog start script (for shell:startup)
' Runs tracker.py and server.py hidden with pythonw. Safe to run twice:
' both programs exit by themselves if another copy is already running.
Option Explicit
Dim sh, py, root
py = "C:\Users\PC1\AppData\Local\Programs\Python\Python38\pythonw.exe"
root = "C:\Users\PC1\Documents\MAENG_worklog"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run """" & py & """ """ & root & "\tracker.py""", 0, False
sh.Run """" & py & """ """ & root & "\server.py""", 0, False
Set sh = Nothing
