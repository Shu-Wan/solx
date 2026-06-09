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
from typing import TYPE_CHECKING

# pathspec is imported where the [keep] specs are compiled (not here) so that
# importing this module stays cheap on NFS; most commands load config without
# ever touching keep rules.
if TYPE_CHECKING:
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
        raise ConfigError(f"unable to read config at {p}: {e}") from e
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
    import pathspec

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


def load_solkeep(path: Path) -> KeepRules | None:
    """Load a gitignore-style `~/.solkeep` keep-list into `KeepRules`.

    The legacy `~/.solkeep` format: each line is a keep pattern, `!` negates
    (carves a subtree out), `#`/blank lines are ignored, a bare path matches
    that directory *and everything under it*, and the last matching rule wins.
    `pathspec`'s `GitIgnoreSpec` implements those semantics, so the whole file
    becomes a single keep matcher (with an empty exclude). Returns None if the
    file is missing or has no effective rules — so `solx keep` can fall through
    to its "nothing to match" handling. `~/.solkeep` is a deprecated fallback
    (see `keep.SOLKEEP_REMOVED_IN`); the supported home is the config `[keep]`.
    """
    if not path.exists():
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    import pathspec

    effective = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not effective:
        return None
    return KeepRules(
        include=pathspec.GitIgnoreSpec.from_lines(lines),
        exclude=pathspec.GitIgnoreSpec.from_lines([]),
        raw_include=tuple(effective),
        raw_exclude=(),
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


def import_solkeep(path: Path) -> tuple[list[str], list[str]] | None:
    """Split a `~/.solkeep` file into `([keep].include, [keep].exclude)`.

    `.solkeep` is one gitignore-style list; `solx init` imports it into the new
    config's `[keep]` block so an existing keep-list carries over without
    rewriting. Plain lines become `include`, `!`-prefixed lines become
    `exclude` (the `!` dropped); `#`/blank lines are skipped. Returns None if
    the file is missing or has no `include` patterns. This is a best-effort
    import of the common "broad includes + `!` carve-outs" shape — review the
    result with `solx config show`.
    """
    if not path.exists():
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    include: list[str] = []
    exclude: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("!"):
            exclude.append(s[1:].strip())
        else:
            include.append(s)
    if not include:  # a keep-list with no keep patterns is nothing to import
        return None
    return include, exclude


def starter_config_text(
    keep: tuple[list[str], list[str]] | None = None,
    default_shell: str = "bash",
) -> str:
    """The text that `solx init` writes to a fresh config.toml.

    With no `keep`, the `[keep]` block is a commented placeholder using the
    `sparky` placeholder (no maintainer name baked in). When `keep` is given
    (imported from `~/.solkeep` via `import_solkeep`), an active `[keep]` block
    is written instead. `default_shell` sets the `default_shell` value (the
    `solx init` walkthrough can pick it).
    """
    base = _STARTER_CONFIG_BASE.replace(
        'default_shell = "bash"', f'default_shell = {_toml_str(default_shell)}'
    )
    block = _render_keep_block(*keep) if keep else _KEEP_PLACEHOLDER
    return base + block


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_keep_block(include: list[str], exclude: list[str]) -> str:
    """Public: render a `[keep]` TOML block from include/exclude pattern lists.

    Used by `solx config import-solkeep` to append a migrated keep-list to an
    existing config.toml. The leading comment notes the gitignore-style syntax.
    """
    return _render_keep_block(include, exclude)


def _render_keep_block(include: list[str], exclude: list[str]) -> str:
    lines = [
        "# [keep] imported from ~/.solkeep — directories `solx keep` renews",
        "# when Sol flags them. Patterns are gitignore-style (** for recursion).",
        "[keep]",
        "include = [",
        *(f"  {_toml_str(p)}," for p in include),
        "]",
    ]
    if exclude:
        lines += ["exclude = [", *(f"  {_toml_str(p)}," for p in exclude), "]"]
    return "\n".join(lines) + "\n"


_STARTER_CONFIG_BASE = """\
# solx config — see https://github.com/Shu-Wan/solx/blob/main/solx/README.md
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


"""

_KEEP_PLACEHOLDER = """\
# Scratch paths to keep alive when Sol flags them in a warning CSV
# *and* `solx keep` runs. Replace `sparky` with your ASURITE.
# Patterns use gitignore-style globs (** for recursion).
# Uncomment + edit to enable:
#
# [keep]
# include = ["/scratch/sparky/your-project", "/scratch/sparky/experiments/**"]
# exclude = ["**/__pycache__", "**/.venv"]
"""
