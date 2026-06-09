# ☀️ solx

A command-line tool for daily work on ASU's
[Sol supercomputer](https://docs.rc.asu.edu/). `solx` wraps the
handful of Slurm operations a terminal-driven user actually does: list
jobs, request an interactive allocation, drop into a shell on the
compute node, cancel, query remaining time, and renew `/scratch` files
that Sol has flagged for deletion.

SSH to Sol, then run `solx` from a login or compute node.

## Status

This is a personal toolkit. Active development; expect breaking changes
between minor versions until 1.0. The project is **not affiliated with
or endorsed by ASU Research Computing**. The authoritative docs for Sol
are at <https://docs.rc.asu.edu/>.

## Install

`solx` provisions its own Python via [`uv`](https://docs.astral.sh/uv/)
(Sol's system `python3` is older than the Python ≥ 3.10 `solx` needs).
Install `uv` from [astral.sh/uv](https://docs.astral.sh/uv/) first if it
isn't on your `$PATH`.

```shell
# Recommended on Sol: single-file install — one file open at cold start on
# the NFS home, so startup stays fast. Re-run it to upgrade.
curl -fsSL https://github.com/Shu-Wan/solx/releases/latest/download/install.sh | sh

# Alternative: as a uv tool — isolated venv, on $PATH automatically.
uv tool install git+https://github.com/Shu-Wan/solx.git#subdirectory=solx

solx --version
solx init                        # writes ~/.config/solx/config.toml
solx config edit                 # tune partitions, [keep] paths, etc.
solx config show                 # sanity-check
```

### Shell completion

`solx completions <shell>` prints a completion script. Add it to your shell's
startup file, then restart your shell:

```shell
# bash — add to ~/.bashrc
eval "$(solx completions bash)"

# zsh — add to ~/.zshrc (after compinit)
eval "$(solx completions zsh)"

# fish — add to ~/.config/fish/config.fish
solx completions fish | source
```

## Quick start

```shell
solx init                       # one-time: write ~/.config/solx/config.toml
solx config edit                # tune templates + [keep] paths
solx job start debug            # request an interactive allocation
solx job list                   # see it (RUNNING)
solx job time                   # how much time is left
solx job jump                   # drop into a shell on the compute node
# ... do work ...
exit                            # back to login node; allocation still alive
solx job stop                   # cancel (prompts; -y to skip)
solx keep --dry-run             # preview which scratch files would be renewed
solx keep                       # renew them (prompts)
```

## Design philosophy

`solx` is designed to be usable by both a person at a terminal and an agent
running shell commands on their behalf. The CLI keeps behavior explicit and
machine-readable without hiding Slurm as the source of truth.

- **Run on Sol.** `solx` is a Sol-side tool. It does not construct SSH chains,
  read `~/.ssh/*`, or manage laptop state.
- **Prefer declared state.** One TOML config defines shells, job templates, and
  scratch keep paths. Job state comes from Slurm, not a persistent session file.
- **Expose parseable output.** TTY output is human-readable; piped output or
  `--json` is JSON. Results go to stdout, diagnostics to stderr, and exit codes
  distinguish success, operational no-op, and under-specified input.
- **Make destructive operations explicit.** `job stop` and `keep` show the plan
  first, support `--dry-run`, prompt by default, and refuse non-interactive runs
  unless `--yes` or `--dry-run` is supplied.
- **Bound filesystem changes.** `keep` only updates timestamps for directories
  that are both configured by the user and flagged by Sol's warning CSVs. It
  never blanket-touches `/scratch`, and it never reads, moves, or deletes file
  contents.
- **Do not replace every Slurm command.** `solx` wraps repeated interactive
  workflows. For one-off status reads or known-job cancellation, raw Slurm can
  still be the right tool.

## Command reference

`solx` is a flat-ish CLI. Common ergonomics: noun-verb subgroups for
related operations, top-level shortcuts where they earn it.

| Command | What it does |
|---|---|
| `solx init [-f]` | Write a starter `config.toml`. On a terminal, offers a short walkthrough — pick your shell and (if present) import your `~/.solkeep` into `[keep]`. Refuses to overwrite without `-f` (or interactive `y`). |
| `solx job list` | List my Sol jobs (Rich table on a TTY, JSON when piped). Aliases: `solx jobs list`, `solx job ls`, `solx jobs ls`. |
| `solx job start [TEMPLATE] [-n] [--timeout T] [-- ...]` | Request an interactive allocation via `salloc --no-shell`. `TEMPLATE` defaults to `default_template`; tail after `--` is appended verbatim to `salloc`. |
| `solx job stop [JOBID] [-y] [-n]` | Cancel a job. Prompts unless `-y`; `-n` previews the `scancel` invocation. |
| `solx job jump [JOBID] [-q]` | Drop into `default_shell` on the compute node via `srun --pty`. Also reachable as `solx jump [JOBID]`. `-q/--quiet` silences the nesting / most-recent heads-up. |
| `solx job time [JOBID]` | Print remaining time in Slurm's `D-HH:MM:SS` format. |
| `solx keep [--solkeep F] [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]` | Renew CSV-flagged scratch files. Keep-list source: `--solkeep` > the `[keep]` config block > `~/.solkeep` (auto-detected, so an existing `.solkeep` from the skill just works). |
| `solx config show [--json]` | Print the resolved config. |
| `solx config edit` | Open `config.toml` in `$EDITOR`. |
| `solx config import-solkeep` | Migrate a legacy `~/.solkeep` into the config's `[keep]` block. |
| `solx completions <bash\|zsh\|fish>` | Emit a shell completion script. |
| `solx --version`, `--help` | — |

The global `--json` flag goes **before** the subcommand
(`solx --json job list`). See the full manual at
[`docs/solx.md`](../docs/solx.md).

### Aliases

- The `job` subgroup is also reachable as `jobs`. Both `solx job list`
  and `solx jobs list` work.
- The `list` verb is also reachable as `ls`.
- `solx jump` is shorthand for `solx job jump`. The verb you reach for
  most often earns the top-level slot.

### Default-jobid resolution (verb-aware)

When you omit `[JOBID]`: an explicit arg wins, else `$SLURM_JOB_ID` (you're
inside an allocation), else `squeue -u $USER`. With **≥2 matching jobs** the
verbs differ — `time`/`jump` auto-pick the **most recent** (highest job id),
while `stop` **never** guesses and exits 2 to disambiguate. Acting from inside
an allocation warns about nesting (`jump`, `-q` to silence) or self-cancel
(`stop`). Full rules: [`docs/solx.md`](../docs/solx.md#leaving-out-the-job-id).

### Destructive-command confirmation contract

`solx job stop` and `solx keep` mutate state — cancel a running
allocation, or `touch` mtimes under `/scratch`. Both follow:

| Flag | Behavior |
|---|---|
| (none) | Print what's about to happen, then prompt `Proceed? [y/N]`. Default no. |
| `-y`/`--yes` (or `-f`/`--force`) | Skip the prompt and execute. For scripts. |
| `-n`, `--dry-run` | Print the plan without executing. **No prompt** — nothing destructive is about to happen. |

`-y` and `-n` together exit 2 (mutually exclusive). In a **non-interactive
session** (no stdin TTY) without `-y`/`-n`, both commands **refuse with exit 2**
rather than hang on a prompt — safe to drive from an agent or cron.

### Output: human or CLI agent

Output auto-detects — **JSON when stdout is not a TTY**, Rich tables on a
terminal; the global `--json` (before the subcommand) forces JSON anywhere. A
human at a terminal gets tables with no flag. Results go to stdout, all
diagnostics to stderr, so `solx --json job list | jq …` and `solx job time`
(bare duration) both pipe cleanly. Exit codes: `0` success,
`1` operational/nothing-to-do, `2` under-specified or unconfirmed. This is the
[issue #16](https://github.com/Shu-Wan/solx/issues/16) "design for
agents" behavior; details in [`docs/solx.md`](../docs/solx.md#output-for-scripts).

Other commands (`init`, `job start`, `job list`, `job jump`, `job time`,
`config show`, `config edit`) don't prompt. `solx init` has its own
overwrite prompt for an existing `config.toml`.

## Configuration

A single TOML file at `$XDG_CONFIG_HOME/solx/config.toml` (fallback
`~/.config/solx/config.toml`), created mode `0600` by `solx init`.

```toml
default_shell = "bash"
default_template = "default"
start_timeout = "10m"          # cap on `job start` polling; --timeout overrides

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"

[jobs.gpu]
partition = "public"
gres = "gpu:a100:1"
time = "0-4"
extra_args = ["--mem=64G", "--cpus-per-task=8"]

# Scratch paths to keep alive when Sol flags them in a warning CSV
# *and* `solx keep` runs. Replace `sparky` with your ASURITE.
[keep]
include = ["/scratch/sparky/your-project", "/scratch/sparky/experiments/**"]
exclude = ["**/__pycache__", "**/.venv"]
```

### Schema

| Key | Type | Required | Notes |
|---|---|---|---|
| `default_shell` | string | yes | Used by `solx job jump` when dropping into the compute node. |
| `default_template` | string | yes | Template name for `solx job start` when invoked without an argument. Must match one of `[jobs.*]`. |
| `start_timeout` | string (e.g. `"10m"`) | no, default `"10m"` | Cap on how long `solx job start` waits for the queue. CLI flag `--timeout` overrides per-run. |
| `[jobs.<name>]` | table | yes (≥1) | Interactive job templates. |
| `[jobs.<name>].partition` | string | yes | `-p` |
| `[jobs.<name>].time` | string | yes | `-t` |
| `[jobs.<name>].qos` | string | no | `-q` |
| `[jobs.<name>].gres` | string | no | `--gres=` |
| `[jobs.<name>].extra_args` | array of strings | no | Verbatim Slurm flags passed to `salloc` (e.g. `["--mem=64G", "--mail-type=END"]`). |
| `[keep]` | table | no | Scratch renewal config. If absent, `solx keep` exits 2 with a redirect message. |
| `[keep].include` | array of glob strings | yes when `[keep]` present | Recursive globs (`**` supported via `pathspec`). Gitignore-style. |
| `[keep].exclude` | array of glob strings | no | Carve-outs from `include` (e.g. `**/__pycache__`). |

There is no `[shared]` merge — each `[jobs.<name>]` table is
self-contained. Repeat flags across templates if you need them in
multiple places. Trade: simpler config; slightly more typing.

CLI passthrough: anything after `--` on `solx job start` is appended to
the underlying `salloc` command after `extra_args`. Slurm's
last-flag-wins lets the tail override template defaults for one run:

```shell
solx job start gpu -- --mem=128G --time=8:00:00
```

## Under the hood

The headless-allocation model behind `solx job start` / `jump` and the
CSV-∩-keep-list mechanism behind `solx keep` are documented in the manual:
[`docs/solx.md`](../docs/solx.md#under-the-hood). A legacy `~/.solkeep` still
works but is deprecated (support removed in 0.5.0) — migrate with
`solx config import-solkeep`.

## Contributing / development

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for architecture, testing
approach, and the manual smoke checklist.

## License

MIT. See repo root.
