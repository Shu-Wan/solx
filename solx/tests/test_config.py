from pathlib import Path

import pytest

from solx import config as cfg


def test_merge_scalars_profile_overrides_shared():
    shared = {"qos": "public", "partition": "lightwork"}
    profile = {"qos": "private"}
    result = cfg._merge(shared, profile)
    assert result["qos"] == "private"
    assert result["partition"] == "lightwork"


def test_merge_lists_concatenate_shared_first():
    shared = {"srun_args": ["--mail-type=END"]}
    profile = {"srun_args": ["--mem=64G"]}
    result = cfg._merge(shared, profile)
    assert result["srun_args"] == ["--mail-type=END", "--mem=64G"]


def test_merge_list_only_in_shared_passes_through():
    shared = {"srun_args": ["--mail-type=END"]}
    profile = {"partition": "htc"}
    result = cfg._merge(shared, profile)
    assert result["srun_args"] == ["--mail-type=END"]


def test_merge_list_only_in_profile_passes_through():
    shared = {"qos": "public"}
    profile = {"forward": [8888, 6006]}
    result = cfg._merge(shared, profile)
    assert result["forward"] == [8888, 6006]


def test_load_profiles_round_trip(write_config, sample_profiles_toml):
    path = write_config(sample_profiles_toml)
    profiles = cfg.load_profiles(path=path)
    assert set(profiles) == {"default", "gpu"}


def test_load_profiles_applies_shared_qos_when_profile_omits_it(
    write_config, sample_profiles_toml
):
    path = write_config(sample_profiles_toml)
    profiles = cfg.load_profiles(path=path)
    assert profiles["default"].qos == "public"


def test_load_profiles_profile_qos_overrides_shared_qos(
    write_config, sample_profiles_toml
):
    path = write_config(sample_profiles_toml)
    profiles = cfg.load_profiles(path=path)
    assert profiles["gpu"].qos == "private"


def test_load_profiles_concatenates_srun_args_shared_first(
    write_config, sample_profiles_toml
):
    path = write_config(sample_profiles_toml)
    profiles = cfg.load_profiles(path=path)
    assert profiles["gpu"].srun_args == ("--mail-type=END", "--mem=64G")


def test_load_profiles_promotes_unknown_keys_into_extra(write_config):
    path = write_config(
        """
[default]
kind = "bare"
partition = "htc"
weird_field = "value"
"""
    )
    profiles = cfg.load_profiles(path=path)
    assert profiles["default"].extra == {"weird_field": "value"}


def test_load_profiles_raises_when_missing(tmp_path: Path):
    with pytest.raises(cfg.ConfigError, match="Run `solx config init`"):
        cfg.load_profiles(path=tmp_path / "nope.toml")


def test_load_profiles_raises_on_malformed_toml(write_config):
    path = write_config("this is = not [valid toml")
    with pytest.raises(cfg.ConfigError, match="Failed to parse"):
        cfg.load_profiles(path=path)


def test_resolve_returns_named_profile(write_config, sample_profiles_toml):
    path = write_config(sample_profiles_toml)
    profile = cfg.resolve("default", path=path)
    assert profile.name == "default"
    assert profile.partition == "lightwork"


def test_resolve_lists_alternatives_when_profile_missing(
    write_config, sample_profiles_toml
):
    path = write_config(sample_profiles_toml)
    with pytest.raises(cfg.ConfigError, match="Available: default, gpu"):
        cfg.resolve("does-not-exist", path=path)


def test_write_starter_creates_file_and_parses(tmp_path: Path):
    path = tmp_path / "profiles.toml"
    written = cfg.write_starter(path=path)
    assert written == path
    assert path.exists()
    profiles = cfg.load_profiles(path=path)
    assert {"default", "gpu", "debug"} <= set(profiles)


def test_write_starter_refuses_to_overwrite_without_force(tmp_path: Path):
    path = tmp_path / "profiles.toml"
    path.write_text("# user content")
    with pytest.raises(cfg.ConfigError, match="--force"):
        cfg.write_starter(path=path)
    # Original content untouched
    assert path.read_text() == "# user content"


def test_write_starter_overwrites_with_force(tmp_path: Path):
    path = tmp_path / "profiles.toml"
    path.write_text("# user content")
    cfg.write_starter(path=path, force=True)
    assert "[default]" in path.read_text()
