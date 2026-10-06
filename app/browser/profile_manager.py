"""Менеджер браузерных профилей (ТЗ §31).

Каждый Twitch-аккаунт связан с отдельным профилем браузера ОС. Авторизация
выполняется пользователем в браузере один раз; приложение НЕ извлекает
cookies и НЕ переносит сессии — только запускает нужный профиль с нужным URL
и следит за процессом.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from app.core.config import AppConfig
from app.database import models

log = logging.getLogger(__name__)

TWITCH_INVENTORY_URL = "https://www.twitch.tv/drops/inventory"
TWITCH_CAMPAIGNS_URL = "https://www.twitch.tv/drops/campaigns"

WINDOWS_BROWSERS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chromium": [r"C:\Program Files\Chromium\Application\chrome.exe"],
}
POSIX_BROWSERS = {
    "chrome": ["google-chrome", "google-chrome-stable", "chromium"],
    "edge": ["microsoft-edge"],
    "chromium": ["chromium", "chromium-browser"],
}


def stream_url(channel: str) -> str:
    return f"https://www.twitch.tv/{channel.lstrip('/')}"


class BrowserProfileManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ------------------------------------------------------------- команды
    @staticmethod
    def _default_executable(browser: str) -> str | None:
        table = WINDOWS_BROWSERS if sys.platform.startswith("win") else POSIX_BROWSERS
        candidates = table.get(browser, [])
        if sys.platform.startswith("win"):
            for path in candidates:
                if os.path.exists(path):
                    return path
            return None
        # POSIX: первый существующий в PATH
        import shutil

        for name in candidates:
            found = shutil.which(name)
            if found:
                return found
        return None

    def launch_command(self, profile: models.BrowserProfile, url: str) -> list[str]:
        executable = profile.executable or self._default_executable(profile.browser)
        if not executable:
            raise FileNotFoundError(
                f"Браузер «{profile.browser}» не найден. Укажите путь в настройках профиля."
            )
        return [
            executable,
            f"--profile-directory={profile.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ]

    # ------------------------------------------------------------- действия
    def open(self, profile: models.BrowserProfile, url: str) -> int | None:
        """Открывает URL в нужном профиле. В DRY RUN — только превью команды."""
        command = self.launch_command(profile, url)
        if self.config.dry_run:
            log.info("[DRY RUN] Браузер не запускается. Команда: %s", " ".join(command))
            return None
        log.info("Запуск браузера: %s", " ".join(command))
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return process.pid

    @staticmethod
    def is_running(pid: int | None) -> bool:
        if not pid:
            return False
        if sys.platform.startswith("win"):
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def terminate(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 15)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("Не удалось завершить браузер (pid %s): %s", pid, exc)
            return False
