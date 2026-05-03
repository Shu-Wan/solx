from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_slurm_env(monkeypatch):
    """Clear SLURM_* env vars by default so tests are deterministic.

    The dev machine is Sol itself, and pytest may be invoked from inside an
    allocation. Tests that *want* `$SLURM_JOB_ID` set (e.g. compute-node
    behavior) must `monkeypatch.setenv` it themselves.
    """
    for k in list(monkeypatch.__dict__):
        pass  # noop placeholder to satisfy linters
    for var in [
        "SLURM_JOB_ID",
        "SLURM_JOBID",
        "SLURM_NODELIST",
        "SLURM_STEP_ID",
    ]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture
def write_config(config_path: Path):
    def _write(contents: str) -> Path:
        config_path.write_text(contents)
        return config_path

    return _write


SAMPLE_CONFIG_TOML = """\
default_shell = "zsh"
default_template = "default"
start_timeout = "5m"

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"

[jobs.gpu]
partition = "public"
gres = "gpu:a100:1"
time = "0-4"
extra_args = ["--mem=64G", "--cpus-per-task=8"]

[keep]
include = ["/scratch/sparky/proj-a", "/scratch/sparky/proj-b/**"]
exclude = ["**/__pycache__", "**/.venv"]
"""
