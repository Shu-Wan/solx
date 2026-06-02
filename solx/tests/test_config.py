from __future__ import annotations

import os
from pathlib import Path

import pytest

from solx import config as cfg
from solx.config import Config, ConfigError, JobTemplate
from tests.conftest import SAMPLE_CONFIG_TOML


def test_load_full_config(write_config) -> None:
    p = write_config(SAMPLE_CONFIG_TOML)
    c = cfg.load(p)
    assert c.default_shell == "zsh"
    assert c.default_template == "default"
    assert c.start_timeout_seconds == 300
    assert set(c.templates) == {"default", "debug", "gpu"}

    gpu = c.templates["gpu"]
    assert gpu.partition == "public"
    assert gpu.gres == "gpu:a100:1"
    assert gpu.time == "0-4"
    assert gpu.qos is None
    assert gpu.extra_args == ("--mem=64G", "--cpus-per-task=8")


def test_template_lookup_missing_raises(write_config) -> None:
    c = cfg.load(write_config(SAMPLE_CONFIG_TOML))
    with pytest.raises(ConfigError, match="unknown job template"):
        c.template("nonexistent")


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="run `solx init`"):
        cfg.load(tmp_path / "absent.toml")


def test_invalid_toml(write_config) -> None:
    p = write_config("default_shell = [unclosed array")
    with pytest.raises(ConfigError, match="invalid TOML"):
        cfg.load(p)


def test_required_default_shell(write_config) -> None:
    p = write_config(
        """default_template = "default"
[jobs.default]
partition = "x"
time = "1-0"
"""
    )
    with pytest.raises(ConfigError, match="default_shell"):
        cfg.load(p)


def test_required_default_template(write_config) -> None:
    p = write_config(
        """default_shell = "bash"
[jobs.default]
partition = "x"
time = "1-0"
"""
    )
    with pytest.raises(ConfigError, match="default_template"):
        cfg.load(p)


def test_at_least_one_jobs_table(write_config) -> None:
    p = write_config('default_shell = "bash"\ndefault_template = "x"\n')
    with pytest.raises(ConfigError, match="\\[jobs\\.<name>\\] table"):
        cfg.load(p)


def test_default_template_must_exist(write_config) -> None:
    p = write_config(
        """default_shell = "bash"
default_template = "missing"

[jobs.default]
partition = "x"
time = "1-0"
"""
    )
    with pytest.raises(ConfigError, match="not defined"):
        cfg.load(p)


def test_template_required_keys(write_config) -> None:
    p = write_config(
        """default_shell = "bash"
default_template = "default"

[jobs.default]
partition = "x"
"""
    )
    with pytest.raises(ConfigError, match="`time`"):
        cfg.load(p)


def test_extra_args_must_be_string_array(write_config) -> None:
    p = write_config(
        """default_shell = "bash"
default_template = "default"

[jobs.default]
partition = "x"
time = "1-0"
extra_args = [1, 2]
"""
    )
    with pytest.raises(ConfigError, match="extra_args"):
        cfg.load(p)


def test_keep_match_include_only() -> None:
    keep = cfg._parse_keep(
        {"include": ["/scratch/sparky/proj-a/**"]}, source="t"
    )
    assert keep is not None
    assert keep.matches("/scratch/sparky/proj-a/data.csv")
    assert not keep.matches("/scratch/sparky/proj-b/data.csv")


def test_keep_exclude_carve_out() -> None:
    keep = cfg._parse_keep(
        {
            "include": ["/scratch/sparky/proj-a/**"],
            "exclude": ["**/__pycache__/**", "**/.venv/**"],
        },
        source="t",
    )
    assert keep is not None
    assert keep.matches("/scratch/sparky/proj-a/run/data.csv")
    assert not keep.matches("/scratch/sparky/proj-a/run/__pycache__/x.pyc")
    assert not keep.matches("/scratch/sparky/proj-a/.venv/lib/x.py")


def test_keep_requires_include() -> None:
    with pytest.raises(ConfigError, match="non-empty array"):
        cfg._parse_keep({"exclude": ["x"]}, source="t")


def test_keep_absent_returns_none() -> None:
    assert cfg._parse_keep(None, source="t") is None


def test_parse_duration() -> None:
    assert cfg.parse_duration("30s") == 30
    assert cfg.parse_duration("10m") == 600
    assert cfg.parse_duration("1h") == 3600
    assert cfg.parse_duration(" 5M ") == 300


def test_parse_duration_invalid() -> None:
    with pytest.raises(ConfigError):
        cfg.parse_duration("never")


def test_config_path_honors_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert cfg.config_path() == tmp_path / "x" / "solx" / "config.toml"


def test_config_path_falls_back_to_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cfg.config_path() == tmp_path / ".config" / "solx" / "config.toml"


def test_starter_config_loads_clean(tmp_path: Path) -> None:
    """The text `solx init` writes must round-trip through `load()`."""
    p = tmp_path / "starter.toml"
    p.write_text(cfg.starter_config_text())
    c = cfg.load(p)
    assert c.default_shell == "bash"
    assert c.default_template == "default"
    assert "default" in c.templates
    assert "debug" in c.templates
    assert c.keep is None  # commented out in starter; user uncomments


def test_starter_config_no_maintainer_name() -> None:
    """Public starter must use `sparky`, never the maintainer's name."""
    text = cfg.starter_config_text()
    assert "swan16" not in text
    assert "<asurite>" not in text
    assert "sparky" in text  # in the commented [keep] example


def test_load_unreadable_raises_config_error(tmp_path: Path) -> None:
    """A directory where a file is expected -> OSError -> clean ConfigError."""
    p = tmp_path / "config.toml"
    p.mkdir()  # exists() is True, but open('rb') raises IsADirectoryError
    with pytest.raises(ConfigError, match="unable to read"):
        cfg.load(p)


def test_load_solkeep(tmp_path: Path) -> None:
    p = tmp_path / ".solkeep"
    p.write_text(
        "# comment\n"
        "/scratch/sparky/proj\n"
        "!/scratch/sparky/proj/**/__pycache__\n"
    )
    rules = cfg.load_solkeep(p)
    assert rules is not None
    assert rules.matches("/scratch/sparky/proj/src")              # kept (prefix)
    assert not rules.matches("/scratch/sparky/proj/a/__pycache__")  # negated
    assert not rules.matches("/scratch/sparky/other")            # not listed


def test_load_solkeep_missing(tmp_path: Path) -> None:
    assert cfg.load_solkeep(tmp_path / "nope") is None


def test_load_solkeep_comments_only(tmp_path: Path) -> None:
    p = tmp_path / ".solkeep"
    p.write_text("# just a comment\n\n")
    assert cfg.load_solkeep(p) is None
