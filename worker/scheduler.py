import atexit
import os
import signal
import time
from pathlib import Path

from filelock import FileLock, Timeout
from flask import Flask

from core.logger import get_logger
from core.timeutil import utcnow
from app.models import WorkerStatus
from worker.task_processor import TaskProcessor


class WorkerScheduler:
    def __init__(self, app: Flask):
        self.app = app
        self.config = app.extensions["jobagents_config"]
        self.logger = get_logger("worker.scheduler")
        self.processor = TaskProcessor(app)
        self._stop = False
        self._last_cycle = 0.0
        self._lock: FileLock | None = None

    def _handle_signal(self, signum, _frame):
        self.logger.info("Worker received signal %s; stopping", signum)
        self._stop = True

    def _acquire_lock(self) -> bool:
        lock_path = self.config.resolve_path(self.config.get("worker.lock_file", "var/worker.lock"))
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            self.logger.error("Another worker instance already holds the lock at %s", lock_path)
            return False
        self._lock = lock
        atexit.register(self._release_lock)
        return True

    def _release_lock(self) -> None:
        try:
            if self._lock is not None:
                self._lock.release()
        except Exception:
            pass
        self._lock = None

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        if not self._acquire_lock():
            raise SystemExit(1)
        poll = max(2, self.config.get_int("worker.poll_seconds", 5))
        heartbeat = max(5, self.config.get_int("worker.heartbeat_seconds", 30))
        min_cycle = max(10, self.config.get_int("worker.min_cycle_seconds", 30))
        self.logger.info("Worker started with pid %s", os.getpid())
        with self.app.app_context():
            self.processor.update_state(status=WorkerStatus.RUNNING, message="worker started")
        last_heartbeat = 0.0
        try:
            while not self._stop:
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat:
                    try:
                        with self.app.app_context():
                            self.processor.update_state()
                    except Exception:
                        self.logger.exception("Heartbeat update failed")
                    last_heartbeat = now
                if now - self._last_cycle >= min_cycle:
                    try:
                        self.processor.run_cycle()
                    except Exception:
                        self.logger.exception("Unexpected worker cycle failure")
                    self._last_cycle = time.monotonic()
                time.sleep(poll)
        finally:
            with self.app.app_context():
                try:
                    self.processor.update_state(status=WorkerStatus.STOPPED, message="worker stopped")
                except Exception:
                    pass
            self._release_lock()
            self.logger.info("Worker stopped")
