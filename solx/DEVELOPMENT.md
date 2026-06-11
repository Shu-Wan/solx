# solx — development

Contributor + maintainer guide for the `solx` CLI. End-user docs live
in [`README.md`](README.md). The agent skill at `../skills/sol-skill/`
drives `solx` and ships on the same version line; see
[`../DEVELOPMENT.md`](../DEVELOPMENT.md) for the skill + eval harness.

## Architecture

Small Python modules, each with one job:

```
solx/src/solx/
├── __init__.py         # version constant
├── __main__.py         # `python -m solx` entry
├── main.py             # entry point (solx.main:main): argparse tree + dispatch
├── _completions.py     # static bash/zsh/fish completion scripts, rendered
│                       #   from one description of the command surface
├── config.py           # XDG TOML loader + dataclasses + pathspec compilation
├── output.py           # Out: JSON-vs-Rich auto-detect + stdout/stderr split
├── side.py             # Sol-vs-not-Sol guard (each subcommand asks require_sol)
├── slurm.py            # squeue/scancel/salloc/srun wrappers + verb-aware resolution
├── jobs.py             # `solx job *` command bodies
├── keep.py             # `solx keep` (CSV-driven renewal, file-level sharded)
└── init.py             # `solx init` (write starter config.toml)
```

Runtime dependencies: `rich` (human tables and prompts only) and
`pathspec` (keep-list globs), plus the `tomli` backport on Python 3.10.
The dispatch layer itself is stdlib `argparse`. Both entry points —
`[project.scripts] solx = "solx.main:main"` and the zipapp's
`-m "solx.main:main"` — go through `main.py`.

### Design notes worth knowing about

- **Startup latency is a budget.** `solx`'s home is NFS, where every
  module import is a network round-trip, so the import graph on the hot
  path is deliberately tiny: importing `main.py` loads nothing beyond
  the interpreter baseline, `--version`/`version` return before the
  argparse tree is built, and each handler imports its own command body
  (with its `rich`/`pathspec` trees) on demand. `--json` and piped runs
  never import `rich`. `tests/test_main.py` guards the budget (e.g. a
  fresh-interpreter check that dispatch loads no third-party CLI
  framework); `evals/runner/bench_solx_latency.sh` measures the result
  on a real Sol node.
- **Completions are static, generated from one table.**
  `_completions.py::COMMANDS` describes the command surface (commands,
  flags, choices, help strings) once; `bash_script()` / `zsh_script()` /
  `fish_script()` render it into scripts that never exec `solx` at
  completion time. The zsh script's footer keys on `$zsh_eval_context`
  so the same output works both eval/sourced and autoloaded from
  `fpath`. When you add or rename a command or flag in `main.py`, update
  `COMMANDS` to match — `tests/test_completions.py` checks the surface.
- **No persistent state.** `solx` queries `squeue -u $USER` whenever it
  needs to know what jobs you have. There's no `session.json`, no
  stale-state class of bugs. Cost: one squeue call per command — fine
  on a login node.
- **Slurm is the source of truth.** Job-id resolution
  (`stop`/`jump`/`time`) reads `$SLURM_JOB_ID` if set (compute-node
  default), then asks squeue. It's **verb-aware** (`slurm.Resolution`):
  with ≥2 jobs, `time`/`jump` auto-pick the most recent (highest job id,
  `most_recent()`), while `stop` never guesses and exits 2 with the
  candidate list — a wrong cancel is irreversible. Acting from inside an
  allocation triggers a nesting heads-up (`jump`) or self-cancel confirm
  (`stop`). Rationale lives in the design panel synthesis; summary in
  [`../docs/solx.md`](../docs/solx.md#leaving-out-the-job-id).
- **Output is `Out` (`output.py`), not bare `print`/`Console`.** Each
  command body takes an `Out` that decides JSON vs Rich (auto: JSON when
  stdout isn't a TTY; global `--json` forces it) and splits streams —
  results to stdout, every diagnostic to stderr. Destructive commands
  refuse (`exit 2`) in a non-interactive session rather than hang on a
  prompt. Tests build an `Out` over `StringIO` consoles with an explicit
  mode (see `make_out` in `tests/test_jobs.py` / `test_keep.py`).
- **`Runner` injection** in `slurm.py`. Every subprocess call goes
  through a `Runner` callable that takes argv and returns
  `(returncode, stdout, stderr)`. Tests pass synthetic runners that
  return canned output without spawning subprocesses. The real runner
  is `slurm.real_runner`.
- **`salloc --no-shell`, not `sbatch --wrap='sleep infinity'`.** Sol
  has Slurm 25.x; the native primitive is available. Cleaner `seff`
  output, no `sleep` process billed against the allocation. Jobid is
  parsed from salloc's stderr (`Granted job allocation N`) — well-known
  Slurm output that's been stable for years.
- **No `[shared]` merge in config.** Each `[jobs.<name>]` is
  self-contained. The trade: simpler schema, slightly more typing if
  you want a flag in every template. Worth it; merge logic was
  contributing more confusion than savings.
- **`keep`** renews CSV-flagged scratch dirs filtered by the keep-list
  (`--stage`, `--csv-dir`, `-j`, `-n`, `-v`); it only renews what Sol has
  flagged. The keep-list lives in the `[keep]` config block; a legacy
  `~/.solkeep` is read as a **deprecated** fallback (warned, removed in
  1.0.0 — see `keep.SOLKEEP_REMOVED_IN`; `solx config import-solkeep`
  migrates it). Execution is **file-level sharded** (PR #18):
  `_pick_lister` (fd/rg/find) → `enumerate_dir` → `shard` → `touch_files`
  on a bounded streaming window, so `-j` scales the biggest single
  directory, not just the directory count. `_execute` has a serial
  `jobs_n<=1` fast path (no process pool) used by tests and the
  end-to-end real-touch test.
- **Top-level shortcut for `jump`.** `solx jump` and `solx job jump`
  both work. The verb you reach for most earns the shortcut. No other
  verbs get this treatment; it'd make help-text noisy.

### Aliases — what's wired

- `solx jobs *` → `solx job *` and `solx job ls` → `solx job list`:
  `main()` rewrites the tokens before parsing, so the aliases never
  appear in `--help`.
- `solx jump` (top-level) and `solx job jump` are separate subparsers
  sharing the same handler (`_cmd_jump`).
- `solx version` / `solx help` are subcommands aliasing `--version` /
  `--help`; `version` and `--version` short-circuit in `main()` before
  the parser tree is built.
- All exercised by `tests/test_main.py` (`test_*alias*`,
  `test_top_level_jump_*`, `test_version_*`) — if you change a command
  name, those tests fail loudly.

## Testing

```shell
cd solx
uv sync                # one-time
uv run pytest          # full suite
uv run pytest -v       # verbose
uv run pytest tests/test_jobs.py::test_start_passthrough_appended -v
```

The whole suite runs in a few seconds — the only subprocesses are the
shell syntax checks and the fresh-interpreter import-budget guards;
everything else stays in-process with no real disk other than
`tmp_path`.

For black-box regression of the whole CLI surface (stdout/stderr/exit
codes against deterministic SLURM mocks), see the parity matrix at
[`../evals/parity/`](../evals/parity/README.md).

### Coverage targets

| Module | What's tested |
|---|---|
| `side.py` | `detect()` parsing branches (Sol login, Sol compute, not-Sol, FQDN-only fallback). |
| `config.py` | TOML schema parse, every required-key error, type errors, `pathspec` glob compilation, `parse_duration`, XDG fallback, **starter config round-trips through `load()`** (so `solx init` output is always valid), **starter config has no maintainer name baked in** (`sparky` only). |
| `output.py` | `Out.auto` force/auto-detect, stdout/stderr split, clean JSON emission, `emit` json-vs-human branch. |
| `slurm.py` | `squeue` row parsing; verb-aware `resolve_jobid` (arg / env / single / zero, stop-ambiguous-no-autopick, time-most-recent, jump-running-only + no-running); `most_recent` (highest id, array ids); every argv builder; `parse_granted_jobid`; `run_salloc` success + failure. |
| `jobs.py` | `cmd_list` (empty / populated / squeue-fail / **JSON**), `cmd_start` (default template, dry-run, passthrough, salloc failure, unknown template, **JSON jobid**), `cmd_stop` (`-y`/`-n` mutex, dry-run, prompt proceed/abort, **non-interactive refuse**, ambiguous-no-autopick + JSON candidates, self-cancel warning, JSON), `cmd_jump` (arg, **inside warn-and-proceed**, `-q` suppress, most-recent, no-running), `cmd_time` (arg, JSON, most-recent). |
| `keep.py` | CSV parsing, `build_plan` filter + dedup + exclude carve-out, `shard`/`enumerate_dir`/`touch_files` units, `cmd_keep` `-y`/`-n` mutex, no-`[keep]` exit 2, dry-run no-execute, prompt branches, **non-interactive refuse**, single-stage filter, failure propagation, JSON summary + dry-run plan, **end-to-end real-touch** (recursion + carve-out + non-kept). |
| `init.py` | Fresh write, parent-dir creation, mode 0600, refuse-existing-without-force, `--force` overwrite, prompt-and-confirm. |
| `main.py` | Every command + alias path dispatches (bodies mocked — wiring, not behavior); `--json` in both positions; the `job start` tail parser (passthrough, `--timeout`, template after `--`, trailing `--json` stays passthrough); no option abbreviation; import-budget guards (entry module stays lean, dispatch loads no third-party CLI framework). |
| `_completions.py` | Every command/flag appears in each shell's script; zsh dual-mode footer; path-valued flags complete files/dirs; each script passes its shell's syntax check (`zsh -n` / `bash -n` / `fish --no-execute`, skipped when the shell is absent). |

### Test fixtures

- `tests/conftest.py::_isolate_slurm_env` (autouse) clears `SLURM_*`
  env vars before each test. The dev machine is Sol itself; pytest
  may be invoked from inside an allocation. Tests that *want*
  `$SLURM_JOB_ID` set must `monkeypatch.setenv` it explicitly.
- `config_path` / `write_config` for TOML round-trip tests.
- `SAMPLE_CONFIG_TOML` exports a known-good full config.

## Building and installing locally

Build the single-file zipapp from the worktree and install it under a
throwaway prefix, so it never shadows the `solx` you already have on
`PATH`:

```shell
cd solx
bash scripts/build-pyz.sh                                   # -> dist/solx.pyz
SOLX_INSTALL_DIR="$HOME/.local/bin-test" bash scripts/install.sh dist/solx.pyz
"$HOME/.local/bin-test/solx" --version
```

`install.sh` re-stamps the shebang with the destination machine's
interpreter, so always install the `.pyz` through it — running the raw
`dist/solx.pyz` relies on whatever interpreter path was baked in at build
time.

**From a PR, without building.** Every push and pull request runs the
`build` job in `.github/workflows/ci.yml`, which attaches the zipapp as
the `solx-pyz` artifact. Download it from the PR's *Checks → Artifacts*,
then install it the same way (the CI build's shebang points at a runner
path, so `install.sh` re-stamping it is required, not optional):

```shell
SOLX_INSTALL_DIR="$HOME/.local/bin-test" bash solx/scripts/install.sh ~/Downloads/solx.pyz
```

## Manual smoke on Sol

The unit tests cover every code path that doesn't require real
cluster round-trips. The smoke checklist below validates the round
trips. `htc`/`debug` queues in seconds, so a full lifecycle takes
under two minutes.

After `ssh sparky@sol.asu.edu` (with your ASURITE):

1. **Install fresh**:
   ```shell
   uv tool install --reinstall git+https://github.com/Shu-Wan/solx.git#subdirectory=solx
   # or the single-file channel: curl -fsSL .../releases/latest/download/install.sh | sh
   solx --version
   ```

2. **Init + show**:
   ```shell
   solx init
   solx config show
   solx config show --json | jq .
   ```

3. **Edit config** to add a real `[keep]` include path you actually
   own:
   ```shell
   solx config edit
   ```

4. **Dry-run before any live allocation**:
   ```shell
   solx job start debug --dry-run
   # prints the salloc argv; verify partition/time/qos look right
   ```

5. **Live allocation lifecycle**:
   ```shell
   solx job start debug
   # waits a few seconds for queue grant, prints "allocated job N"
   solx job list
   # table shows the new job, state RUNNING
   solx job time
   # prints D-HH:MM:SS remaining (no-arg path: sole running job)
   solx job jump
   # drops into your default_shell on the compute node
   exit
   # back to login shell; allocation still alive
   solx job stop
   # prompts "Cancel job N? [y/N]" — type y
   solx job list
   # the job is gone
   ```

6. **`-y` skip + `-n` preview** for `solx job stop`:
   ```shell
   solx job start debug
   jid=$(solx job list | awk 'NR==2 {print $1}')   # or eyeball it
   solx job stop "$jid" -n
   # prints scancel argv; nothing happens
   solx job stop "$jid" -y
   # cancels without prompting
   ```

7. **Verb-aware job-id resolution edge cases**:
   ```shell
   # no jobs:        solx job time  → exit 1, "no jobs found"
   # start two debug jobs, then with NO arg:
   #   solx job time → picks the most recent (higher jobid), note on stderr, exit 0
   #   solx job jump → attaches to the most recent running job, exit 0
   #   solx job stop → prints the candidate table, exit 2 (never guesses)
   # inside an allocation (after `solx job jump`):
   #   solx job stop → "Cancel job N (the one you're inside)?" self-cancel confirm
   #   solx job jump → warns about nesting, still attaches (-q silences)
   ```

7a. **Agent / non-interactive behavior** (no TTY):
   ```shell
   solx job list | jq .                 # JSON array (auto-detected off-TTY)
   solx job time </dev/null             # bare D-HH:MM:SS on stdout
   solx job stop 999 </dev/null         # exit 2: "non-interactive — pass -y…"
   solx --json job time                 # {"jobid":…,"time_left":…} even on a TTY
   ```

8. **Compute-node default-jobid**:
   ```shell
   solx job start debug
   solx job jump
   # on the compute node now:
   solx job time          # uses $SLURM_JOB_ID, no arg needed
   exit
   solx job stop -y
   ```

9. **Scratch renewal** (only if Sol has flagged something for you;
   otherwise skip — there's nothing to demo):
   ```shell
   ls ~/scratch-dirs-*.csv 2>/dev/null
   # if any exist:
   solx keep --dry-run -v
   # plan summary; verify the kept list looks right
   solx keep
   # prompts "Touch mtimes on N directories? [y/N]" — type y
   ```

10. **Wrong-side guard** (run on a laptop, not Sol):
    ```shell
    solx --version    # works
    solx --help       # works
    solx job list     # exit 2 with "solx is Sol-only — SSH first"
    solx keep         # same
    ```

11. **Aliases**:
    ```shell
    solx jobs list    # same as solx job list
    solx job ls       # same
    solx jump 12345   # same as solx job jump 12345
    ```

12. **Completions**:
    ```shell
    solx completions zsh > /tmp/solx.zsh
    source /tmp/solx.zsh
    solx <TAB>        # subcommands appear
    solx job s<TAB>   # start, stop appear
    ```

## Releasing

The CLI and the skill share one version line, and CI publishes the
release. To cut `vX.Y.Z`:

1. Bump `solx/src/solx/__init__.py::__version__`,
   `solx/pyproject.toml::version`, and the `version:` field in
   `../skills/sol-skill/SKILL.md` (keep all three matched), then refresh
   the lock (`uv lock`).
2. Move the `[Unreleased]` notes under a `## [X.Y.Z]` heading in
   `../CHANGELOG.md`; update `../docs/coverage.md`.
3. Run the full test suite + at least the smoke flow above.
4. Tag `vX.Y.Z` and push it. `.github/workflows/release.yml` verifies the
   tag matches `solx --version`, builds `solx.pyz`, and publishes a
   GitHub Release with `solx.pyz` + `install.sh` attached.

## When in doubt

- The user-facing behavior of `solx` lives in the manual
  [`../docs/solx.md`](../docs/solx.md); the roadmap and design decisions are
  in [`../docs/ROADMAP.md`](../docs/ROADMAP.md). When code and docs disagree, raise
  it — usually the code is right and the doc needs an update, but check.
- The agent skill at `../skills/sol-skill/` drives `solx`
  (`references/solx.md` is its CLI reference). Keep the skill's user
  guidance there and `solx` architecture/test detail here.
- The repo root `README.md` and `DEVELOPMENT.md` cover the whole project
  (CLI + skill + evals); this file is the `solx` package's internals.
