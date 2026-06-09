# evals/parity/ — `solx` behavioral parity matrix

A black-box regression harness for the `solx` CLI. It runs one `solx`
binary through **67 cases** covering the whole command surface — meta
(`--version`, help, unknown commands), `job list/start/stop/jump/time`,
`jump`, `keep`, `config`, `init`, `completions`, aliases, `--json` in
both positions, and error paths — and captures stdout, stderr, and exit
code per case. Comparing two captured runs byte-for-byte proves (or
disproves) that two `solx` builds behave identically, which is what
makes a dispatch-layer or runtime rewrite safe to ship.

Each case runs in a **fresh fake `$HOME`** (plus `XDG_CONFIG_HOME`) with
**deterministic SLURM mocks** on `PATH`, under `env -i` with
`USER=sparky` and `TERM=dumb` — so the output is reproducible and
independent of the node, the real queue, or your real config.

## Layout

```
evals/parity/
├── bin/                # mock squeue / salloc / srun / scancel / hostname
│                       #   env toggles: MOCK_SQUEUE_EMPTY=1, MOCK_SQUEUE_FAIL=1,
│                       #   MOCK_SQUEUE_TWORUNNING=1 select canned squeue variants
├── fixtures/           # config.toml variants, ~/.solkeep variants, warning CSVs
├── run_matrix.sh       # run the 67 cases against one solx binary
└── compare_runs.py     # compare two captured runs (stdlib python3 only)
```

Captured runs (`golden-*/`, scratch output dirs) are **not committed** —
see below.

## Capturing a golden

A golden is the captured behavior of a reference `solx` version:

```shell
cd evals/parity
./run_matrix.sh "$(command -v solx)" golden-v0.4.0
```

Each case lands as `golden-v0.4.0/<case>.{out,err,code}` with per-case
tempdir paths normalized to `__HOME__`. Goldens are
**environment-captured, not committed**: capture the reference version's
golden on the same machine (and Python) you'll run the candidate on, so
the diff isolates the code change rather than the environment.

## Comparing a candidate

```shell
./run_matrix.sh /path/to/candidate/solx out-candidate
./compare_runs.py golden-v0.4.0 out-candidate          # add --json for machine output
```

`compare_runs.py` exits 0 when no strict case fails. Case classes:

- **STRICT** (the default): exit code, stdout, and stderr must match
  byte-for-byte.
- **RELAXED** (help/usage text and completion scripts): only the exit
  code must match, and stdout is smoke-checked for key content — help
  and completion text is allowed to differ across implementations.
- **VERSION_CASES** (`--version`, `version`): exit code must match and
  stdout must be a bare semver; the value itself may differ.
- **EXPECTED_DIFF**: known deliberate divergences (e.g. the trailing
  `--json` acceptance case, the `~/.solkeep` deprecation message's
  version string). Reported, but never fail the run.

The class membership lives at the top of `compare_runs.py`; when a
behavior change is intentional, move its case into `EXPECTED_DIFF` in
the same change that introduces it, with a comment saying why.

## Requirements

A POSIX shell + `bash` for `run_matrix.sh`, any `python3` for
`compare_runs.py` (stdlib only), and a runnable `solx` for each side of
the comparison. The mocks shadow the real SLURM tools via `PATH`, so the
harness is safe to run anywhere — it never talks to a real scheduler.
