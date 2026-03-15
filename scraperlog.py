from collections import deque
from datetime import datetime
from pathlib import Path
import textwrap
import threading
import traceback
import time
import atexit
import os

import colorama
from colorama import Fore, Back, Style


class Logging:
    def __init__(self, script_name: str, color_logs: bool = True, log_dir: Path | None = None):
        self.script_name: str = script_name.upper()
        self.color_logs: bool = color_logs
        self.log_dir: Path = Path(log_dir) if log_dir else Path.cwd()
        self.filename: str = self.script_name.lower() + '_' + self._get_dwt('%Y-%m-%d-%H-%M-%S') + '.log'
        self.filepath: Path = self.log_dir / self.filename if log_dir else Path.cwd() / 'logs' / self.filename
        self.log_queue: deque = deque()  # Left <--- Right
        self._stop_event: threading.Event = threading.Event()

        if self.color_logs:
            self.LOG_COLORS: dict[str, colorama.ansi] = {
                "DEBUG": Fore.LIGHTBLACK_EX + Style.DIM,
                "INFO": Fore.LIGHTBLUE_EX + Style.NORMAL,
                "WARNING": Fore.LIGHTYELLOW_EX + Style.BRIGHT,
                "ERROR": Fore.LIGHTRED_EX + Style.BRIGHT,
                "CRITICAL": Fore.MAGENTA + Style.BRIGHT
            }
            colorama.init()

        os.makedirs(self.log_dir, exist_ok=True)
        self.filepath.touch(exist_ok=True)

        self.log_thread: threading.Thread = threading.Thread(
            target=self._write_logs_to_file,
            daemon=True,
            name='LogWriterThread'
        )
        self.log_thread.start()
        atexit.register(self._graceful_shutdown)

    def log(self, message: str, category: str = 'DEBUG', exception: Exception | None = None):
        category: str = category.upper()
        if category not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            raise ValueError(f"{category} is not a supported category. Choose from ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].")

        timestamp: str = self._get_dwt()
        if exception:
            exc_type: str = type(exception).__name__
            tb: str = ''.join(traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__
            ))
            tb = textwrap.indent(tb, ' ' * 8)
            log_text: str = f"[{timestamp}] [{self.script_name}] [{category}][{exc_type}] {message}\n\n{tb}"
        else:
            log_text: str = f"[{timestamp}] [{self.script_name}] [{category}] {message}"

        self.log_queue.append(log_text)

        if self.color_logs:
            print(self.LOG_COLORS[category] + log_text + Style.RESET_ALL)
        else:
            print(log_text)

    def _write_logs_to_file(self):
        while not self._stop_event.is_set() or self.log_queue:
            try:
                if self.log_queue:
                    with open(self.filepath, 'a', encoding='utf-8') as f:
                        while self.log_queue:
                            log_entry: str = self.log_queue.popleft()
                            f.write(log_entry + '\n')
                time.sleep(0.5)  # avoid high CPU usage
            except Exception as e:
                print(f"[Logging Error] Failed to write log: {e}")

    def _graceful_shutdown(self):
        self._stop_event.set()
        self.log_thread.join()

    @staticmethod
    def _get_dwt(fmt: str = '%Y-%m-%d %H:%M:%S'):
        return datetime.now().strftime(fmt)
