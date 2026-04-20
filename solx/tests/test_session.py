from pathlib import Path

import pytest

from solx import session as sess


def test_load_returns_none_when_file_missing(session_path: Path):
    assert sess.load(session_path) is None


def test_save_and_load_round_trip(session_path: Path):
    original = sess.Session.new(
        profile="debug",
        kind="bare",
        job_id="12345",
        node="cg001",
        ports=[8888, 6006],
    )
    sess.save(original, session_path)
    loaded = sess.load(session_path)
    assert loaded == original


def test_save_creates_parent_directory(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "session.json"
    s = sess.Session.new(
        profile="debug", kind="bare", job_id="1", node="cg001", ports=[]
    )
    sess.save(s, path)
    assert path.exists()


def test_load_raises_on_malformed_json(session_path: Path):
    session_path.write_text("{ not valid json")
    with pytest.raises(sess.SessionError, match="Malformed"):
        sess.load(session_path)


def test_clear_removes_file(session_path: Path):
    s = sess.Session.new(
        profile="debug", kind="bare", job_id="1", node="cg001", ports=[]
    )
    sess.save(s, session_path)
    assert session_path.exists()
    sess.clear(session_path)
    assert not session_path.exists()


def test_clear_is_idempotent_when_file_absent(session_path: Path):
    sess.clear(session_path)  # no exception


def test_is_job_alive_true_when_squeue_returns_a_line():
    assert sess.is_job_alive("12345", _runner=lambda _: "12345\n")


def test_is_job_alive_false_when_squeue_returns_empty():
    assert not sess.is_job_alive("12345", _runner=lambda _: "")


def test_is_job_alive_false_when_squeue_returns_only_whitespace():
    assert not sess.is_job_alive("12345", _runner=lambda _: "  \n")


def test_session_started_at_is_iso_utc():
    s = sess.Session.new(
        profile="debug", kind="bare", job_id="1", node="cg001", ports=[]
    )
    # ISO-8601 UTC ends with +00:00
    assert s.started_at.endswith("+00:00")
