@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 RAG Jev（首次会构建向量索引，请稍候）…
python launch.py
pause
