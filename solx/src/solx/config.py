"""Single-file config under $XDG_CONFIG_HOME/solx/config.toml.

The user runs `solx init` to write a starter file; everything else just
reads it. No `[shared]` merge — each `[jobs.<name>]` table is
self-contained, which keeps the schema obvious at the cost of repeating
a flag across templates if someone really wants that.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pathspec


CONFIG_FILENAME = "config.toml"
DEFAULT_START_TIMEOUT = "10m"


class ConfigError(Exception):
    """Raised for any user-facing config problem (missing file, bad schema)."""


@dataclass(frozen=True)
class JobTemplate:
    """One `[jobs.<name>]` table."""

    name: str
    partition: str
    time: str
    qos: str | None = None
    gres: str | None = None
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeepRules:
    """Resolved `[keep]` include/exclude as compiled pathspecs."""

    include: pathspec.PathSpec
    exclude: pathspec.PathSpec
    raw_include: tuple[str, ...] = ()
    raw_exclude: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        """Return True if `path` is included and not excluded."""
        if not self.include.match_file(path):
            return False
        return not self.exclude.match_file(path)


@dataclass(frozen=True)
class Config:
    default_shell: str
    default_template: str
    start_timeout_seconds: int
    templates: dict[str, JobTemplate] = field(default_factory=dict)
    keep: KeepRules | None = None

    def template(self, name: str) -> JobTemplate:
        """Look up a template by name; raise ConfigError if missing."""
        if name not in self.templates:
            available = ", ".join(sorted(self.templates)) or "(none)"
            raise ConfigError(
                f"unknown job template {name!r}. defined: {available}"
            )
        return self.templates[name]


def config_path() -> Path:
    """Resolve the config path honoring XDG_CONFIG_HOME with the usual fallback."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "solx" / CONFIG_FILENAME


def load(path: Path | None = None) -> Config:
    """Load and validate the config from `path` (defaults to `config_path()`)."""
    p = path or config_path()
    if not p.exists():
        raise ConfigError(
            f"no config at {p}. run `solx init` to write a starter file."
        )
    try:
        with p.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {p}: {e}") from e
    except OSError as e:
        # Unreadable file (permissions, a directory in its place, I/O error):
        # surface a clean config error instead of a traceback.
        raise ConfigError(f"cannot read config {p}: {e}") from e
    return _parse(raw, source=str(p))


def _parse(raw: dict, *, source: str) -> Config:
    default_shell = _require_str(raw, "default_shell", source)
    default_template = _require_str(raw, "default_template", source)
    timeout_str = raw.get("start_timeout", DEFAULT_START_TIMEOUT)
    if not isinstance(timeout_str, str):
        raise ConfigError(
            f"{source}: `start_timeout` must be a string like \"10m\""
        )
    start_timeout_seconds = parse_duration(timeout_str)

    jobs_raw = raw.get("jobs", {})
    if not isinstance(jobs_raw, dict) or not jobs_raw:
        raise ConfigError(
            f"{source}: at least one [jobs.<name>] table is required"
        )
    templates = {
        name: _parse_template(name, body, source)
        for name, body in jobs_raw.items()
    }
    if default_template not in templates:
        raise ConfigError(
            f"{source}: default_template={default_template!r} is not defined "
            f"under [jobs.*]"
        )

    keep = _parse_keep(raw.get("keep"), source)

    return Config(
        default_shell=default_shell,
        default_template=default_template,
        start_timeout_seconds=start_timeout_seconds,
        templates=templates,
        keep=keep,
    )


def _parse_template(name: str, body: object, source: str) -> JobTemplate:
    if not isinstance(body, dict):
        raise ConfigError(f"{source}: [jobs.{name}] must be a table")
    partition = _require_str(body, "partition", f"{source}:[jobs.{name}]")
    time = _require_str(body, "time", f"{source}:[jobs.{name}]")
    qos = _optional_str(body, "qos", f"{source}:[jobs.{name}]")
    gres = _optional_str(body, "gres", f"{source}:[jobs.{name}]")
    extra_args = _optional_str_list(body, "extra_args", f"{source}:[jobs.{name}]")
    return JobTemplate(
        name=name,
        partition=partition,
        time=time,
        qos=qos,
        gres=gres,
        extra_args=tuple(extra_args),
    )


def _parse_keep(body: object, source: str) -> KeepRules | None:
    if body is None:
        return None
    if not isinstance(body, dict):
        raise ConfigError(f"{source}: [keep] must be a table")
    include = _optional_str_list(body, "include", f"{source}:[keep]")
    exclude = _optional_str_list(body, "exclude", f"{source}:[keep]")
    if not include:
        raise ConfigError(
            f"{source}: [keep].include must be a non-empty array"
        )
    return KeepRules(
        include=pathspec.GitIgnoreSpec.from_lines(include),
        exclude=pathspec.GitIgnoreSpec.from_lines(exclude),
        raw_include=tuple(include),
        raw_exclude=tuple(exclude),
    )


def _require_str(body: dict, key: str, ctx: str) -> str:
    if key not in body:
        raise ConfigError(f"{ctx}: required key `{key}` is missing")
    val = body[key]
    if not isinstance(val, str) or not val:
        raise ConfigError(f"{ctx}: `{key}` must be a non-empty string")
    return val


def _optional_str(body: dict, key: str, ctx: str) -> str | None:
    if key not in body:
        return None
    val = body[key]
    if not isinstance(val, str) or not val:
        raise ConfigError(f"{ctx}: `{key}` must be a non-empty string")
    return val


def _optional_str_list(body: dict, key: str, ctx: str) -> list[str]:
    if key not in body:
        return []
    val = body[key]
    if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
        raise ConfigError(f"{ctx}: `{key}` must be an array of strings")
    return list(val)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Parse a string like "10m" / "30s" / "1h" into seconds."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ConfigError(
            f"invalid duration {text!r}; use forms like \"30s\", \"10m\", \"1h\""
        )
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * _DURATION_UNITS[unit]


def starter_config_text() -> str:
    """The text that `solx init` writes to a fresh config.toml.

    No maintainer name baked in — uses `sparky` as the placeholder per the
    project convention. Comments tell the user to replace.
    """
    return _STARTER_CONFIG


_STARTER_CONFIG = """\
# solx config — see https://github.com/Shu-Wan/sol-skills/blob/main/solx/README.md
#
# Used by `solx job jump` when dropping into a shell on a compute node.
default_shell = "bash"

# Default template for `solx job start` when invoked without an argument.
default_template = "default"

# Cap on how long `solx job start` waits for the queue. CLI flag --timeout
# overrides per-run.
start_timeout = "10m"


# Job templates. Run `solx job start <name>` to allocate one.
# Each table is self-contained; repeat flags across templates if needed.

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"


# Scratch paths to keep alive when Sol flags them in a warning CSV
# *and* `solx keep` runs. Replace `sparky` with your ASURITE.
# Patterns use gitignore-style globs (** for recursion).
# Uncomment + edit to enable:
#
# [keep]
# include = ["/scratch/sparky/your-project", "/scratch/sparky/experiments/**"]
# exclude = ["**/__pycache__", "**/.venv"]
"""
