@echo off
rem WeChat OCR MCP Server launcher
rem Clears PYTHONPATH to avoid importing wrong mcp lib from Hermes venv
set "PYTHONPATH="
"C:\Users\songf\wechat-ocr-mcp\.venv\Scripts\python.exe" "C:\Users\songf\wechat-ocr-mcp\server.py" %*
