@echo off
chcp 65001 >nul
cd /d "%~dp0"
python launch.py --stop
echo 已请求停止。
timeout /t 3 >nul
