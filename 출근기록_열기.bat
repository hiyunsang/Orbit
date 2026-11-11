@echo off
rem MAENG_worklog: launch.pyw 를 실행한다 (수집기+서버 숨김 실행 후 브라우저 앱 창 열기).
rem 바탕화면의 "MAENG 출근기록" 아이콘과 같은 동작. 인자 --no-browser 를 주면 창은 열지 않는다.
start "" "C:\Users\PC1\AppData\Local\Programs\Python\Python38\pythonw.exe" "C:\Users\PC1\Documents\MAENG_worklog\launch.pyw" %*
