import os
import shutil
from pathlib import Path

from core.exceptions import ExecutionError

MAX_FILES = 50
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024


class WorkspaceManager:
    def __init__(self, root: Path, artifacts_dir: Path | None = None):
        self.root = Path(root).resolve()
        self.artifacts_dir = Path(artifacts_dir).resolve() if artifacts_dir else self.root.parent / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: int, attempt: int) -> Path:
        workspace = self.root / f"task-{task_id}-attempt-{attempt}"
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def cleanup(self, workspace: Path) -> None:
        try:
            path = Path(workspace).resolve()
            if self.root in path.parents and path.exists():
                shutil.rmtree(path)
        except OSError:
            pass

    @staticmethod
    def _safe_target(workspace: Path, relative_path: str) -> Path:
        if not relative_path or "\x00" in relative_path:
            raise ExecutionError("Invalid artifact path")
        normalized = relative_path.replace("\\", "/").lstrip("/")
        if normalized.startswith("../") or "/../" in normalized or normalized == "..":
            raise ExecutionError(f"Path traversal attempt blocked: {relative_path}")
        if Path(normalized).is_absolute():
            raise ExecutionError(f"Absolute paths are not allowed: {relative_path}")
        target = (workspace / normalized).resolve()
        if target != workspace and workspace not in target.parents:
            raise ExecutionError(f"Path escapes workspace: {relative_path}")
        return target

    def write_files(self, workspace: Path, files: list[dict]) -> list[str]:
        workspace = Path(workspace).resolve()
        written = []
        total = 0
        for item in files[:MAX_FILES]:
            path = str(item.get("path") or "")
            content = item.get("content")
            if not isinstance(content, str):
                continue
            target = self._safe_target(workspace, path)
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise ExecutionError(f"File exceeds per-file limit: {path}")
            total += len(encoded)
            if total > MAX_TOTAL_BYTES:
                raise ExecutionError("Workspace output exceeds total size limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            written.append(str(target.relative_to(workspace)).replace("\\", "/"))
        return written

    def collect_artifacts(self, workspace: Path) -> list[dict]:
        workspace = Path(workspace).resolve()
        artifacts = []
        total = 0
        for current_root, _dirs, files in os.walk(workspace):
            for name in files:
                full_path = Path(current_root) / name
                try:
                    if full_path.is_symlink():
                        continue
                    size = full_path.stat().st_size
                    if size > MAX_FILE_BYTES:
                        continue
                    total += size
                    if total > MAX_TOTAL_BYTES:
                        break
                    content = full_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relative = str(full_path.relative_to(workspace)).replace("\\", "/")
                artifacts.append({"path": relative, "content": content, "size": size})
            if total > MAX_TOTAL_BYTES:
                break
        artifacts.sort(key=lambda item: item["path"])
        return artifacts
