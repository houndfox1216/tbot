@echo off
cd /d "%~dp0"

if not exist ".venv" (
    echo 首次執行，建立虛擬環境並安裝套件...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)

if not exist ".env" (
    echo 錯誤：找不到 .env 檔案！
    echo 請先複製 .env.example 為 .env 並填入 BOT_TOKEN。
    pause
    exit /b 1
)

echo 啟動 Telegram Bot...
.venv\Scripts\python bot.py
pause
