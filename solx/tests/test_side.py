from solx import side


def test_matches_sol_login_node():
    assert side._matches_sol("login02.sol.rc.asu.edu")


def test_matches_sol_compute_node_with_alias_list():
    # `hostname -a` typically returns space-separated aliases.
    assert side._matches_sol("sc045 sc045.sol.rc.asu.edu")


def test_matches_sol_rejects_bare_short_hostname():
    assert not side._matches_sol("sc045")


def test_matches_sol_rejects_unrelated_host():
    assert not side._matches_sol("my-laptop.local laptop")


def test_matches_sol_rejects_empty():
    assert not side._matches_sol("")


def test_detect_uses_runner_to_decide_sol():
    assert side.detect(_runner=lambda: "login02.sol.rc.asu.edu") == "sol"


def test_detect_returns_not_sol_when_runner_yields_unrelated_host():
    assert side.detect(_runner=lambda: "macbook-pro.local") == "not-sol"


def test_detect_returns_not_sol_when_runner_yields_empty():
    assert side.detect(_runner=lambda: "") == "not-sol"
