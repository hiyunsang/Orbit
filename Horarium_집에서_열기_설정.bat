@echo off
rem Horarium: share to my own devices over Tailscale (private network). See DESIGN.md section 27.
rem Content must stay ASCII only.
"C:\Users\PC1\AppData\Local\Programs\Python\Python38\python.exe" "%~dp0share_tailnet.py"
echo.
pause
