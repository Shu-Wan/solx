"""Session state: read/write `~/.local/share/solx/session.json` and check job liveness."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "share")) / "solx"


def default_session_path() -> Path:
    return _state_dir() / "session.json"


class SessionError(Exception):
    """Raised when session.json is malformed or in an unexpected state."""


@dataclass
class Session:
    profile: str
    kind: str
    job_id: str
    node: str
    ports: list[int]
    started_at: str  # ISO-8601 UTC

    @classmethod
    def new(
        cls,
        *,
        profile: str,
        kind: str,
        job_id: str,
        node: str,
        ports: list[int],
    ) -> "Session":
        return cls(
            profile=profile,
            kind=kind,
            job_id=job_id,
            node=node,
            ports=list(ports),
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


def load(path: Path | None = None) -> Session | None:
    """Read session.json. Returns None if absent."""
    path = path or default_session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SessionError(f"Malformed session.json at {path}: {exc}") from exc
    return Session(**data)


def save(session: Session, path: Path | None = None) -> Path:
    """Write session.json (creating parent dir if needed)."""
    path = path or default_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(session), indent=2) + "\n")
    return path


def clear(path: Path | None = None) -> None:
    """Remove session.json. Idempotent."""
    path = path or default_session_path()
    path.unlink(missing_ok=True)


def is_job_alive(job_id: str, *, _runner=None) -> bool:
    """Return True iff `squeue -h -j <job_id>` reports the job."""
    runner = _runner or _run_squeue
    return bool(runner(job_id).strip())


def _run_squeue(job_id: str) -> str:
    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%i"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""
