"""Tests for solx.sol_cmds — argv construction, polling, session lifecycle."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from solx import config as cfg
from solx import session as sess
from solx import sol_cmds


# ---------------------------------------------------------------------------
# Argv construction
# ---------------------------------------------------------------------------


def test_build_sbatch_argv_with_minimal_profile():
    profile = cfg.Profile(name="debug", kind="bare", partition="htc", time="0-1")
    argv = sol_cmds.build_sbatch_argv(profile)
    assert argv == [
        "sbatch",
        "--parsable",
        "--job-name=solx-debug",
        "--partition=htc",
        "--time=0-1",
        "--wrap=sleep infinity",
    ]


def test_build_sbatch_argv_includes_qos_gres_and_srun_args():
    profile = cfg.Profile(
        name="gpu",
        kind="bare",
        partition="general",
        qos="public",
        time="0-4",
        gres="gpu:a100:1",
        srun_args=("--mail-type=END", "--mem=64G"),
    )
    argv = sol_cmds.build_sbatch_argv(profile)
    assert argv == [
        "sbatch",
        "--parsable",
        "--job-name=solx-gpu",
        "--partition=general",
        "--qos=public",
        "--time=0-4",
        "--gres=gpu:a100:1",
        "--mail-type=END",
        "--mem=64G",
        "--wrap=sleep infinity",
    ]


def test_build_sbatch_argv_appends_passthrough_after_srun_args():
    profile = cfg.Profile(
        name="gpu",
        kind="bare",
        partition="general",
        srun_args=("--mem=64G",),
    )
    argv = sol_cmds.build_sbatch_argv(profile, passthrough=["--mem=128G"])
    # `--mem=128G` appears AFTER `--mem=64G` so sbatch's last-wins semantics
    # let the CLI tail override the profile.
    assert argv.index("--mem=64G") < argv.index("--mem=128G")
    assert argv[-1] == "--wrap=sleep infinity"


# ---------------------------------------------------------------------------
# wait_for_running polling loop
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def test_wait_for_running_returns_node_on_running():
    clock = FakeClock()
    states = iter([("PENDING", ""), ("CONFIGURING", ""), ("RUNNING", "cg001")])
    node = sol_cmds.wait_for_running(
        "1",
        poll=lambda _: next(states),
        sleep=clock.sleep,
        now=clock.now,
        timeout=60,
        interval=1,
    )
    assert node == "cg001"


def test_wait_for_running_raises_when_job_fails():
    clock = FakeClock()
    states = iter([("PENDING", ""), ("FAILED", "")])
    with pytest.raises(RuntimeError, match="state FAILED"):
        sol_cmds.wait_for_running(
            "1",
            poll=lambda _: next(states),
            sleep=clock.sleep,
            now=clock.now,
            timeout=60,
            interval=1,
        )


def test_wait_for_running_times_out():
    clock = FakeClock()
    with pytest.raises(RuntimeError, match="did not start within"):
        sol_cmds.wait_for_running(
            "1",
            poll=lambda _: ("PENDING", ""),
            sleep=clock.sleep,
            now=clock.now,
            timeout=5,
            interval=1,
        )


# ---------------------------------------------------------------------------
# session_start happy path
# ---------------------------------------------------------------------------


def _write_starter(config_path: Path) -> Path:
    return cfg.write_starter(path=config_path)


def test_session_start_dry_run_does_not_submit(config_path, session_path, capsys):
    _write_starter(config_path)
    submitted: list[list[str]] = []

    code = sol_cmds.session_start(
        "debug",
        dry_run=True,
        config_path=config_path,
        session_path=session_path,
        submit=lambda argv: (submitted.append(argv), "999")[1],
    )
    assert code == 0
    assert submitted == []
    assert not session_path.exists()
    out = capsys.readouterr().out
    assert "sbatch" in out
    assert "--partition=htc" in out


def test_session_start_writes_session_json(config_path, session_path):
    _write_starter(config_path)
    clock = FakeClock()
    code = sol_cmds.session_start(
        "debug",
        config_path=config_path,
        session_path=session_path,
        submit=lambda _: "12345",
        poll=lambda _: ("RUNNING", "cg001"),
        sleep=clock.sleep,
        wait_timeout=60,
        poll_interval=1,
    )
    assert code == 0
    assert session_path.exists()

    saved = json.loads(session_path.read_text())
    assert saved["job_id"] == "12345"
    assert saved["node"] == "cg001"
    assert saved["profile"] == "debug"
    assert saved["kind"] == "bare"
    assert saved["ports"] == [8000, 8888]


def test_session_start_returns_2_on_unknown_profile(config_path, session_path):
    _write_starter(config_path)
    code = sol_cmds.session_start(
        "no-such-profile",
        config_path=config_path,
        session_path=session_path,
    )
    assert code == 2


def test_session_start_returns_2_on_unsupported_kind(config_path, session_path):
    config_path.write_text(
        """
[default]
kind = "vscode"
partition = "lightwork"
"""
    )
    code = sol_cmds.session_start(
        "default",
        config_path=config_path,
        session_path=session_path,
    )
    assert code == 2


# ---------------------------------------------------------------------------
# Stale-session detection
# ---------------------------------------------------------------------------


def test_session_start_clears_stale_session_then_submits(
    config_path, session_path, monkeypatch
):
    _write_starter(config_path)
    # Pre-existing session.json pointing at a job that no longer exists.
    sess.save(
        sess.Session.new(
            profile="debug",
            kind="bare",
            job_id="OLD",
            node="cg999",
            ports=[8888],
        ),
        session_path,
    )
    # Force is_job_alive() to report the old job is dead.
    monkeypatch.setattr(
        sol_cmds.sess, "is_job_alive", lambda jid, _runner=None: False
    )
    clock = FakeClock()
    code = sol_cmds.session_start(
        "debug",
        config_path=config_path,
        session_path=session_path,
        submit=lambda _: "NEW",
        poll=lambda _: ("RUNNING", "cg100"),
        sleep=clock.sleep,
        wait_timeout=60,
        poll_interval=1,
    )
    assert code == 0
    saved = json.loads(session_path.read_text())
    assert saved["job_id"] == "NEW"
    assert saved["node"] == "cg100"


def test_session_start_refuses_when_existing_session_alive(
    config_path, session_path, monkeypatch, capsys
):
    _write_starter(config_path)
    sess.save(
        sess.Session.new(
            profile="debug",
            kind="bare",
            job_id="ALIVE",
            node="cg100",
            ports=[8888],
        ),
        session_path,
    )
    monkeypatch.setattr(
        sol_cmds.sess, "is_job_alive", lambda jid, _runner=None: True
    )
    submitted: list[list[str]] = []
    code = sol_cmds.session_start(
        "debug",
        config_path=config_path,
        session_path=session_path,
        submit=lambda argv: (submitted.append(argv), "NEW")[1],
    )
    assert code == 2
    assert submitted == []
    # Existing session.json is preserved.
    assert json.loads(session_path.read_text())["job_id"] == "ALIVE"
    err = capsys.readouterr().err
    assert "already running" in err


def test_session_start_clears_malformed_session_and_proceeds(
    config_path, session_path
):
    _write_starter(config_path)
    session_path.write_text("{not json")
    clock = FakeClock()
    code = sol_cmds.session_start(
        "debug",
        config_path=config_path,
        session_path=session_path,
        submit=lambda _: "1",
        poll=lambda _: ("RUNNING", "cg001"),
        sleep=clock.sleep,
        wait_timeout=60,
        poll_interval=1,
    )
    assert code == 0
    assert json.loads(session_path.read_text())["job_id"] == "1"


# ---------------------------------------------------------------------------
# session_info
# ---------------------------------------------------------------------------


def test_session_info_returns_1_when_no_session(session_path, capsys):
    code = sol_cmds.session_info(session_path=session_path)
    assert code == 1
    assert "No active session" in capsys.readouterr().err


def test_session_info_json_output(session_path, capsys):
    sess.save(
        sess.Session.new(
            profile="debug",
            kind="bare",
            job_id="42",
            node="cg001",
            ports=[8888],
        ),
        session_path,
    )
    code = sol_cmds.session_info(json_output=True, session_path=session_path)
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["job_id"] == "42"


def test_session_info_pretty_output_includes_key_fields(session_path, capsys):
    sess.save(
        sess.Session.new(
            profile="debug",
            kind="bare",
            job_id="42",
            node="cg001",
            ports=[8888, 6006],
        ),
        session_path,
    )
    code = sol_cmds.session_info(session_path=session_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "42" in out
    assert "cg001" in out
    assert "8888" in out and "6006" in out


# ---------------------------------------------------------------------------
# session_stop
# ---------------------------------------------------------------------------


def test_session_stop_calls_scancel_and_clears(session_path):
    sess.save(
        sess.Session.new(
            profile="debug",
            kind="bare",
            job_id="42",
            node="cg001",
            ports=[],
        ),
        session_path,
    )
    cancelled: list[str] = []
    code = sol_cmds.session_stop(
        session_path=session_path,
        scancel=cancelled.append,
    )
    assert code == 0
    assert cancelled == ["42"]
    assert not session_path.exists()


def test_session_stop_idempotent_when_no_session(session_path):
    cancelled: list[str] = []
    code = sol_cmds.session_stop(
        session_path=session_path,
        scancel=cancelled.append,
    )
    assert code == 0
    assert cancelled == []


# ---------------------------------------------------------------------------
# config_init / config_show
# ---------------------------------------------------------------------------


def test_config_init_writes_starter(config_path, capsys):
    code = sol_cmds.config_init(config_path=config_path)
    assert code == 0
    assert "[default]" in config_path.read_text()
    assert str(config_path) in capsys.readouterr().out


def test_config_init_refuses_to_overwrite_without_force(config_path, capsys):
    config_path.write_text("# user content")
    code = sol_cmds.config_init(config_path=config_path)
    assert code == 2
    assert config_path.read_text() == "# user content"
    assert "--force" in capsys.readouterr().err


def test_config_show_json_output_includes_merged_keys(
    write_config, sample_profiles_toml, capsys
):
    path = write_config(sample_profiles_toml)
    code = sol_cmds.config_show(json_output=True, config_path=path)
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    # default's qos comes from [shared]
    assert parsed["default"]["qos"] == "public"
    # gpu overrides qos but inherits the [shared] mail-type
    assert parsed["gpu"]["qos"] == "private"
    assert parsed["gpu"]["srun_args"] == ["--mail-type=END", "--mem=64G"]


def test_config_show_returns_2_when_config_missing(tmp_path, capsys):
    code = sol_cmds.config_show(config_path=tmp_path / "absent.toml")
    assert code == 2
    # Rich may line-wrap the error; collapse whitespace before comparing.
    err_normalized = " ".join(capsys.readouterr().err.split())
    assert "Run `solx config init`" in err_normalized
