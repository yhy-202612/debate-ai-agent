@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 人机辩论赛 · 大屏

set PORT=8080

echo ============================================
echo   人机辩论赛 · 大屏计时与指示
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPY

echo 正在启动本地服务 http://localhost:%PORT% ...
start "" "http://localhost:%PORT%/display.html"
echo.
echo 【重要】请不要关闭这个黑窗口，比赛结束后再关。
echo          关闭窗口 = 大屏服务停止。
echo.
python -m http.server %PORT%
goto :EOF

:NOPY
echo 未检测到 Python。
echo.
echo 将以「直接打开文件」的方式启动：
echo   · 计时、指示灯、键盘/鼠标控制  —— 正常可用
echo   · 开发板按钮（串口）           —— 不可用
echo.
echo 如需使用开发板按钮，请安装 Python（python.org 下载，安装时勾选 Add to PATH），
echo 或改用 Chrome 的高级设置里允许不安全来源。详见「使用说明.md」。
echo.
start "" "display.html"
pause
