# solx — development

Contributor + maintainer guide for the `solx` CLI. End-user docs live
in [`README.md`](README.md). The Sol-skill agent skill at
`../skills/sol-skill/` is intentionally untouched by `solx` work in
this stage; that integration is Stage 3 (deferred — see
[`../docs/stage-3-integration.md`](../docs/stage-3-integration.md)).

## Architecture

Eight Python modules. Each is small and has one job:

```
solx/src/solx/
├── __init__.py         # version constant
├── __main__.py         # `python -m solx` entry
├── cli.py              # Typer wiring; nothing happens in here, just dispatch
├── config.py           # XDG TOML loader + dataclasses + pathspec compilation
├── side.py             # Sol-vs-not-Sol guard (each subcommand asks require_sol)
├── slurm.py            # squeue/scancel/salloc/srun wrappers + jobid resolution
├── jobs.py             # `solx job *` command bodies
├── keep.py             # `solx keep` (port of sol_renew.py mechanism)
└── init.py             # `solx init` (write starter config.toml)
```

### Design notes worth knowing about

- **No persistent state.** `solx` queries `squeue -u $USER` whenever it
  needs to know what jobs you have. There's no `session.json`, no
  stale-state class of bugs. Cost: one squeue call per command — fine
  on a login node.
- **Slurm is the source of truth.** Default-jobid resolution
  (`stop`/`jump`/`time`) reads `$SLURM_JOB_ID` if set (compute-node
  default), then asks squeue. Ambiguous cases print a table and exit
  2; a confirmation prompt would make cancelling the wrong job too
  easy.
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
- **`keep` mirrors `sol_renew.py`** — same CSV-driven mechanism, same
  flag surface (`--stage`, `--csv-dir`, `-j`, `-n`, `-v`). Only
  difference: the keep list lives in `[keep]` config now instead of
  `~/.solkeep`. The original ethical posture (only renew what Sol has
  flagged) carries over.
- **Top-level shortcut for `jump`.** `solx jump` and `solx job jump`
  both work. The verb you reach for most earns the shortcut. No other
  verbs get this treatment; it'd make help-text noisy.

### Aliases — what's wired

- `solx job *` and `solx jobs *` resolve to the same Typer subgroup
  (registered twice in `cli.py`).
- `solx job ls` and `solx job list` are separate commands sharing the
  same body (`hidden=True` on `ls`).
- `solx jump` (top-level) and `solx job jump` are separate commands
  sharing the same body.
- All exercised by `tests/test_cli.py::test_*alias*` — if you change a
  command name, those tests fail loudly.

## Testing

```shell
cd solx
uv sync                # one-time
uv run pytest          # full suite
uv run pytest -v       # verbose
uv run pytest tests/test_jobs.py::test_start_passthrough_appended -v
```

The whole suite runs in well under a second. We aim to keep it that
way — no real subprocess spawning, no real disk other than `tmp_path`.

### Coverage targets

| Module | What's tested |
|---|---|
| `side.py` | `detect()` parsing branches (Sol login, Sol compute, not-Sol, FQDN-only fallback). |
| `config.py` | TOML schema parse, every required-key error, type errors, `pathspec` glob compilation, `parse_duration`, XDG fallback, **starter config round-trips through `load()`** (so `solx init` output is always valid), **starter config has no maintainer name baked in** (`sparky` only). |
| `slurm.py` | `squeue` row parsing, `resolve_jobid` (arg / env / single / zero / ambiguous), every argv builder, `parse_granted_jobid` round-trip, `run_salloc` success + failure via injected runner. |
| `jobs.py` | `cmd_list` (empty / populated / squeue-fail), `cmd_start` (default template, dry-run, passthrough, salloc failure, unknown template), `cmd_stop` (`-y`/`-n` mutex, dry-run no-op, prompt-and-proceed, prompt-and-abort, ambiguous), `cmd_jump` (env jobid + arg), `cmd_time`. |
| `keep.py` | CSV parsing, `build_plan` filter + dedup + exclude carve-out, `cmd_keep` `-y`/`-n` mutex, no-`[keep]` exit 2, dry-run no-touch, prompt branches, single-stage filter, error propagation. |
| `init.py` | Fresh write, parent-dir creation, mode 0600, refuse-existing-without-force, `--force` overwrite, prompt-and-confirm. |
| `cli.py` | Every command + alias path dispatches via `CliRunner`. Body itself is mocked — `cli.py` tests verify wiring, not behavior. |

### Test fixtures

- `tests/conftest.py::_isolate_slurm_env` (autouse) clears `SLURM_*`
  env vars before each test. The dev machine is Sol itself; pytest
  may be invoked from inside an allocation. Tests that *want*
  `$SLURM_JOB_ID` set must `monkeypatch.setenv` it explicitly.
- `config_path` / `write_config` for TOML round-trip tests.
- `SAMPLE_CONFIG_TOML` exports a known-good full config.

## Manual smoke on Sol

The unit tests cover every code path that doesn't require real
cluster round-trips. The smoke checklist below validates the round
trips. `htc`/`debug` queues in seconds, so a full lifecycle takes
under two minutes.

After `ssh sparky@sol.asu.edu` (with your ASURITE):

1. **Install fresh**:
   ```shell
   uv tool install --reinstall git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx
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

7. **Default-jobid resolution edge cases**:
   ```shell
   # no jobs: solx job time → exit 1, "no jobs found"
   # multiple jobs: start two debug jobs, then `solx job time` →
   # prints disambiguation table, exit 2.
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

There is no published release cadence yet — `solx` is pre-1.0 and
shipped via `uv tool install` from the Git repo directly. When we cut
a tag:

1. Bump `solx/src/solx/__init__.py::__version__` and
   `solx/pyproject.toml::version` (keep them matched).
2. Run the full test suite + at least the smoke flow above.
3. Tag at the repo root: `git tag solx-vX.Y.Z` (prefix to distinguish
   from skill tags, which are unprefixed `vX.Y.Z`).
4. Push the tag.

## When in doubt

- The contract for what `solx` does and doesn't lives in
  [`../docs/stage-2-solx.md`](../docs/stage-2-solx.md). When code and
  spec disagree, raise it — usually the code is right and the spec
  needs an update, but check.
- The agent skill at `../skills/sol-skill/` is hands-off in this
  stage. Don't add `solx` references there; that's Stage 3.
- The repo root `README.md` and `DEVELOPMENT.md` describe the **agent
  skill**, not `solx`. Keep `solx`-specific content here.
