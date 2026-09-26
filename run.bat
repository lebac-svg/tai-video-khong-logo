@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Tai video khong logo

if not exist ".venv\Scripts\python.exe" (
    echo Lan dau chay: dang tao moi truong Python va cai thu vien...
    python -m venv .venv
    if errorlevel 1 (
        echo Khong tim thay Python. Cai Python 3.10+ tu https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) roi chay lai.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)

".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
