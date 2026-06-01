from __future__ import annotations

import pytest

from solx import side


@pytest.mark.parametrize(
    "hostname_output, expected",
    [
        ("login02.sol.rc.asu.edu", "sol"),
        ("sg045.sol.rc.asu.edu", "sol"),
        ("sg045 sg045.sol.rc.asu.edu sg045-ib", "sol"),
        ("my-laptop.local", "not-sol"),
        ("", "not-sol"),
        ("login02.example.com", "not-sol"),
    ],
)
def test_detect_branches(hostname_output: str, expected: str) -> None:
    assert side.detect(_runner=lambda: hostname_output) == expected


def test_detect_uses_runner_injection() -> None:
    """The runner is what determines the result, not the live host."""
    sentinel = "fake-host.sol.rc.asu.edu"
    assert side.detect(_runner=lambda: sentinel) == "sol"


def test_current_node_returns_short_name() -> None:
    """Best-effort, non-crashing on any host."""
    node = side.current_node()
    assert isinstance(node, str)
    assert "." not in node  # short form, no FQDN
