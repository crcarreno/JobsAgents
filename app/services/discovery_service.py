import hashlib

from core.database import db
from core.exceptions import PlatformError
from core.timeutil import utcnow
from app.adapters import build_adapter
from app.models import PlatformConfig, Task, TaskStatus
from app.services.log_service import LogService
from app.services.settings_service import SettingsService


class DiscoveryService:
    def __init__(self, config, settings: SettingsService):
        self.config = config
        self.settings = settings
        self.log = LogService()

    def scan_due_platforms(self, force_platform_id: int | None = None) -> dict:
        now = utcnow()
        query = PlatformConfig.query.filter_by(enabled=True)
        if force_platform_id:
            query = PlatformConfig.query.filter_by(id=force_platform_id)
        platforms = [platform for platform in query.all() if force_platform_id or platform.is_due(now)]
        summary = {"platforms": 0, "new_tasks": 0, "errors": [], "started_at": now.isoformat()}
        for platform in platforms:
            try:
                result = self.scan_platform(platform)
                summary["platforms"] += 1
                summary["new_tasks"] += result["new_tasks"]
            except Exception as exc:
                db.session.rollback()
                platform.last_scan_at = utcnow()
                platform.last_scan_status = "error"
                platform.last_scan_message = str(exc)[:2000]
                platform.status = "error"
                db.session.commit()
                summary["errors"].append({"platform": platform.slug, "error": str(exc)[:400]})
                self.log.error(
                    "Platform scan failed",
                    platform=platform.slug,
                    error=str(exc)[:1000],
                )
        summary["finished_at"] = utcnow().isoformat()
        return summary

    def scan_platform(self, platform: PlatformConfig) -> dict:
        if not platform.enabled:
            return {"new_tasks": 0, "skipped": "disabled"}
        adapter = build_adapter(platform, self.config, self.settings)
        connection = adapter.connect()
        platform.status = connection.status
        self.log.info(
            "Platform connection checked",
            platform=platform.slug,
            status=connection.status,
            detail=connection.message[:500],
        )
        if not connection.ok and connection.status == "error":
            platform.last_scan_at = utcnow()
            platform.last_scan_status = "error"
            platform.last_scan_message = connection.message[:2000]
            db.session.commit()
            raise PlatformError(connection.message)

        candidates = adapter.search_tasks(since=None)
        limit = int((platform.config or {}).get("limits", {}).get("max_tasks_per_scan", 50))
        candidates = candidates[:limit]
        new_tasks = 0
        for candidate in candidates:
            dedupe = hashlib.sha256(
                f"{platform.slug}:{candidate.external_id}".encode("utf-8")
            ).hexdigest()
            existing = Task.query.filter_by(dedupe_hash=dedupe).first()
            if existing is not None:
                continue
            task = Task(
                platform_id=platform.id,
                external_id=candidate.external_id,
                dedupe_hash=dedupe,
                title=candidate.title,
                description=candidate.description or "",
                url=candidate.url,
                reward_amount=candidate.reward,
                currency=candidate.currency,
                status=TaskStatus.PENDING,
                raw_payload=candidate.raw,
            )
            db.session.add(task)
            new_tasks += 1
        platform.last_scan_at = utcnow()
        platform.last_scan_status = "ok"
        platform.last_scan_message = f"{len(candidates)} candidates, {new_tasks} new tasks"
        db.session.commit()
        self.log.info(
            "Platform scan finished",
            platform=platform.slug,
            candidates=len(candidates),
            new_tasks=new_tasks,
        )
        return {"new_tasks": new_tasks, "candidates": len(candidates)}
