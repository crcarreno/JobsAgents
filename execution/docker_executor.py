import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import SandboxUnavailableError
from core.logger import get_logger


@dataclass
class DockerResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class DockerExecutor:
    def __init__(
        self,
        image: str,
        memory_mb: int = 512,
        cpus: float = 0.5,
        pids_limit: int = 128,
        network_enabled: bool = False,
        user: str = "1000:1000",
        max_minutes: int = 15,
    ):
        self.image = image
        self.memory_mb = int(memory_mb)
        self.cpus = float(cpus)
        self.pids_limit = int(pids_limit)
        self.network_enabled = bool(network_enabled)
        self.user = user
        self.max_minutes = int(max_minutes)
        self.logger = get_logger("docker")

    @staticmethod
    def docker_path() -> str | None:
        return shutil.which("docker")

    def available(self) -> bool:
        docker = self.docker_path()
        if not docker:
            return False
        try:
            result = subprocess.run(
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def ensure_image(self) -> None:
        docker = self.docker_path()
        if not docker:
            raise SandboxUnavailableError("Docker executable not found")
        result = subprocess.run(
            [docker, "image", "inspect", self.image], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise SandboxUnavailableError(
                f"Docker image {self.image} is not available; build it with deployment/docker/sandbox.Dockerfile"
            )

    def run(self, workspace: Path, command: str) -> DockerResult:
        docker = self.docker_path()
        if not docker:
            raise SandboxUnavailableError("Docker is not installed")
        if not self.available():
            raise SandboxUnavailableError("Docker daemon is not reachable")
        self.ensure_image()

        container_name = f"jobagents-{uuid.uuid4().hex[:12]}"
        args = [
            docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "bridge" if self.network_enabled else "none",
            f"--memory={self.memory_mb}m",
            f"--memory-swap={self.memory_mb}m",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}",
            f"--user={self.user}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            "-v",
            f"{Path(workspace).resolve()}:/workspace:rw",
            "-w",
            "/workspace",
            self.image,
            "/bin/sh",
            "-lc",
            command,
        ]
        timeout = self.max_minutes * 60
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return DockerResult(
                exit_code=result.returncode,
                stdout=(result.stdout or "")[-20000:],
                stderr=(result.stderr or "")[-20000:],
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            subprocess.run([docker, "rm", "-f", container_name], capture_output=True, timeout=30)
            return DockerResult(exit_code=124, stdout="", stderr="Execution timed out", timed_out=True)
        except OSError as exc:
            raise SandboxUnavailableError(f"Unable to start Docker container: {exc}") from exc
