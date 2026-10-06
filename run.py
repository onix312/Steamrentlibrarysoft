"""Запуск приложения:  python run.py

Сборка в .exe (Windows):  pyinstaller --windowed --name SteamRentManager run.py
"""
from app.main import run

if __name__ == "__main__":
    raise SystemExit(run())
