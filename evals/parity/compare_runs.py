#!/usr/bin/env python3
"""Compare two solx parity-matrix runs (golden vs candidate).

Usage: compare_runs.py GOLDEN_DIR CANDIDATE_DIR [--json]

Classes of cases:
* STRICT (default): exit code + stdout + stderr must match byte-for-byte.
* RELAXED: only the exit code must match (help/usage text is allowed to
  differ across CLI frameworks); stdout is smoke-checked for key content.
* EXPECTED_DIFF: recorded and reported, but never fails the run (known,
  deliberate divergences — e.g. the dropped implicit ~/.solkeep fallback,
  or `job list --json` becoming accepted).

Exit 0 if no strict failures, 1 otherwise. Prints a human summary, or a
JSON document with --json.
"""
import json
import sys
from pathlib import Path

RELAXED = {
    "help-flag", "help-cmd", "no-args", "unknown-cmd",
    "job-noargs", "job-badsub",
    "completions-bash", "completions-zsh", "completions-fish",
    "completions-tcsh",
    # Dispatch edge cases: exit-code parity required; error wording may
    # differ from Click's.
    "js-dryrun-eq", "version-junk-arg", "version-junk-pre",
    "version-junk-post", "keep-j-zero", "help-job-arg",
}
# Smoke content every RELAXED stdout must still contain (when exit 0).
RELAXED_SMOKE = {
    "help-flag": ["init", "keep", "jump", "job", "config", "completions"],
    "help-cmd": ["init", "keep", "jump", "job", "config", "completions"],
    "completions-bash": ["solx"],
    "completions-zsh": ["#compdef", "solx"],
    "completions-fish": ["solx"],
}
EXPECTED_DIFF = {
    "leaf-json-position",   # v0.4.0 rejects trailing --json; later versions accept
    "keep-fallback",        # v0.5.0 read ~/.solkeep + warned; v1.0 dropped the fallback (errors)
    # -h is a documented v0.5.0 superset: v0.4.0 exits 2, v0.5.0 prints
    # help and exits 0.
    "dash-h-root",
    "dash-h-stop",
}
# Version output changes across versions by definition: exit code must match
# and stdout must look like a bare semver, but the value itself may differ.
VERSION_CASES = {"version-flag", "version-cmd"}
SEMVER = __import__("re").compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?\n$")


def read(d: Path, case: str, ext: str) -> str:
    p = d / f"{case}.{ext}"
    return p.read_text(errors="replace") if p.exists() else "<MISSING>"


def main() -> int:
    golden, cand = Path(sys.argv[1]), Path(sys.argv[2])
    as_json = "--json" in sys.argv[3:]
    cases = sorted(p.stem for p in golden.glob("*.code"))
    missing = [c for c in cases if not (cand / f"{c}.code").exists()]

    results = []
    for c in cases:
        g_code, c_code = read(golden, c, "code").strip(), read(cand, c, "code").strip()
        g_out, c_out = read(golden, c, "out"), read(cand, c, "out")
        g_err, c_err = read(golden, c, "err"), read(cand, c, "err")
        fields = []
        if g_code != c_code:
            fields.append(("code", g_code, c_code))
        if c in VERSION_CASES:
            if not SEMVER.match(c_out):
                fields.append(("stdout", g_out, c_out))
        elif c in RELAXED:
            for needle in RELAXED_SMOKE.get(c, []):
                if g_code == "0" == c_code and needle not in c_out:
                    fields.append(("smoke", needle, "absent"))
        else:
            if g_out != c_out:
                fields.append(("stdout", g_out, c_out))
            if g_err != c_err:
                fields.append(("stderr", g_err, c_err))
        status = "pass"
        if fields:
            status = "expected-diff" if c in EXPECTED_DIFF else "FAIL"
        results.append({"case": c, "status": status,
                        "diffs": [{"field": f, "golden": g[:2000], "candidate": x[:2000]}
                                  for f, g, x in fields]})

    fails = [r for r in results if r["status"] == "FAIL"]
    expected = [r for r in results if r["status"] == "expected-diff"]
    summary = {
        "total": len(cases),
        "pass": sum(1 for r in results if r["status"] == "pass"),
        "fail": len(fails),
        "expected_diff": len(expected),
        "missing_in_candidate": missing,
        "failures": fails,
        "expected_diffs": expected,
    }
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"parity: {summary['pass']}/{summary['total']} pass, "
              f"{len(fails)} fail, {len(expected)} expected-diff, "
              f"{len(missing)} missing")
        for r in fails:
            print(f"\nFAIL {r['case']}")
            for d in r["diffs"]:
                print(f"  [{d['field']}]")
                print(f"    golden:    {d['golden'][:400]!r}")
                print(f"    candidate: {d['candidate'][:400]!r}")
        for r in expected:
            print(f"\nexpected-diff {r['case']}: "
                  + ", ".join(d["field"] for d in r["diffs"]))
    return 1 if fails or missing else 0


if __name__ == "__main__":
    sys.exit(main())
