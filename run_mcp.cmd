@echo off
rem mediaocr MCP Server launcher (Windows)
rem 用当前目录(%~dp0)定位 .venv 和 server.py，可放在任意位置
set "PYTHONPATH="
"%~dp0.venv\Scripts\python.exe" "%~dp0server.py" %*
