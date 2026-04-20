"""Profile config: load TOML, merge `[shared]` into each profile."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "solx"


def default_profiles_path() -> Path:
    return _config_dir() / "profiles.toml"


# Lists merge by concatenation (shared first, then profile).
# Everything else is treated as a scalar: profile overrides shared.
LIST_KEYS = frozenset({"forward", "srun_args"})


class ConfigError(Exception):
    """Raised when the config is missing, malformed, or names a missing profile."""


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str = "bare"  # only "bare" is implemented in Stage 2a
    partition: str | None = None
    qos: str | None = None
    time: str | None = None
    gres: str | None = None
    forward: tuple[int, ...] = field(default_factory=tuple)
    srun_args: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)


def load_profiles(path: Path | None = None) -> dict[str, Profile]:
    """Load every profile from the TOML file, with `[shared]` merged in."""
    path = path or default_profiles_path()
    if not path.exists():
        raise ConfigError(
            f"No config at {path}. Run `solx config init` to create a starter file."
        )
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}") from exc

    shared = raw.pop("shared", {}) or {}
    if not isinstance(shared, dict):
        raise ConfigError(f"`[shared]` in {path} must be a table.")

    profiles: dict[str, Profile] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            # Skip stray top-level scalars (TOML allows them; we ignore).
            continue
        merged = _merge(shared, body)
        profiles[name] = _materialize(name, merged)
    return profiles


def resolve(profile_name: str, path: Path | None = None) -> Profile:
    """Load and return a single profile by name."""
    profiles = load_profiles(path)
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise ConfigError(
            f"Profile {profile_name!r} not found in {path or default_profiles_path()}. "
            f"Available: {available}."
        )
    return profiles[profile_name]


def _merge(shared: dict, profile: dict) -> dict:
    """Return a profile dict with `[shared]` keys folded in.

    - Lists in `LIST_KEYS` are concatenated: shared first, then profile.
    - Everything else: profile value overrides shared value.
    """
    result = dict(shared)
    for key, value in profile.items():
        if key in LIST_KEYS:
            shared_list = list(shared.get(key, []) or [])
            result[key] = shared_list + list(value or [])
        else:
            result[key] = value
    return result


def _materialize(name: str, merged: dict) -> Profile:
    known = {f.name for f in fields(Profile)} - {"name", "extra"}
    init: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in merged.items():
        if key in known:
            init[key] = value
        else:
            extra[key] = value
    if "forward" in init:
        init["forward"] = tuple(init["forward"] or ())
    if "srun_args" in init:
        init["srun_args"] = tuple(init["srun_args"] or ())
    return Profile(name=name, extra=extra, **init)


STARTER_PROFILES_TOML = """\
# solx profile configuration
#
# `[shared]` keys apply to every profile below.
#   * Scalars (partition, qos, time, gres) — profile overrides shared.
#   * Lists (forward, srun_args) — concatenated; shared first, then profile.
#
# Stage 2a supports kind = "bare" only. Other kinds (vscode, sbatch-script)
# will raise a clear error until a future release wires them in.

[shared]
qos = "public"
srun_args = [
  "--mail-type=TIME_LIMIT_90,END,FAIL",
  # Replace with your address before relying on email notifications:
  # "--mail-user=you@asu.edu",
]

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
forward = [8888, 6006]
srun_args = ["--mem=64G", "--cpus-per-task=8"]

[debug]
kind = "bare"
partition = "htc"
time = "0-1"
forward = [8000, 8888]
"""


def write_starter(path: Path | None = None, *, force: bool = False) -> Path:
    """Write the starter profiles.toml. Refuses to overwrite without `force`."""
    path = path or default_profiles_path()
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists. Pass --force to overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_PROFILES_TOML)
    return path
