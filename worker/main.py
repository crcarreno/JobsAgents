import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(os.getenv("JOBAGENTS_ENV_FILE", PROJECT_ROOT / ".env"))

from app import create_app
from core.logger import configure_logging, get_logger
from core.config import Config


def main() -> int:
    config = Config.load()
    configure_logging(config.get("logging.level", "INFO"))
    logger = get_logger("worker")
    app = create_app()
    for relative in ["var", "var/workspaces", "var/artifacts", "var/log"]:
        (PROJECT_ROOT / relative).mkdir(parents=True, exist_ok=True)
    from worker.scheduler import WorkerScheduler

    scheduler = WorkerScheduler(app)
    logger.info("Starting autonomous agent worker", environment=config.get("application.environment"))
    try:
        scheduler.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
