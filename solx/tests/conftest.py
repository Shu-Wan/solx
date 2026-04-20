from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Path to a profiles.toml inside a per-test tmpdir."""
    return tmp_path / "profiles.toml"


@pytest.fixture
def session_path(tmp_path: Path) -> Path:
    """Path to a session.json inside a per-test tmpdir."""
    return tmp_path / "session.json"


@pytest.fixture
def sample_profiles_toml() -> str:
    """A minimal multi-profile config exercising [shared] merge semantics."""
    return """\
[shared]
qos = "public"
srun_args = ["--mail-type=END"]

[default]
kind = "bare"
partition = "lightwork"
time = "1-0"
forward = [8888]

[gpu]
kind = "bare"
partition = "general"
gres = "gpu:a100:1"
time = "0-4"
qos = "private"
forward = [8888, 6006]
srun_args = ["--mem=64G"]
"""


@pytest.fixture
def write_config(config_path: Path):
    """Helper that writes a profiles.toml and returns its path."""

    def _write(contents: str) -> Path:
        config_path.write_text(contents)
        return config_path

    return _write
