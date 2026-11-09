import shutil
from pathlib import Path

from core.exceptions import DeliveryError

INLINE_LIMIT_BYTES = 1_500_000


def build_artifact_records(artifacts: list[dict], workspace: Path, store_dir: Path) -> list[dict]:
    total = sum(len(item["content"].encode("utf-8")) for item in artifacts)
    if total <= INLINE_LIMIT_BYTES:
        return [
            {"path": item["path"], "size": item["size"], "content": item["content"]}
            for item in artifacts
        ]
    store_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for item in artifacts:
        target = store_dir / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / item["path"], target)
        records.append({"path": item["path"], "size": item["size"], "stored_at": str(target)})
    return records


def load_artifact_contents(records: list[dict] | None) -> list[dict]:
    result = []
    for record in records or []:
        content = record.get("content")
        if content is None and record.get("stored_at"):
            try:
                content = Path(record["stored_at"]).read_text(encoding="utf-8")
            except OSError as exc:
                raise DeliveryError(f"Stored artifact is unavailable: {record.get('path')}") from exc
        if content is None:
            continue
        result.append(
            {
                "path": record.get("path"),
                "content": content,
                "size": record.get("size", len(content.encode("utf-8"))),
            }
        )
    return result
