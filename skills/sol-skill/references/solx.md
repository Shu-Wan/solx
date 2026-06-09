# `solx` — the CLI

`solx` is a small command-line tool for daily work on Sol. You SSH to
Sol yourself, then run `solx` from a login or compute node; it shells out
to Slurm and reads one config file. It does **not** touch your laptop,
construct ssh chains, or read `~/.ssh/*`. The skill prefers `solx` for
job and scratch work and installs it on first use.

This is the agent-facing reference. Everything `solx` does is local to
Sol and reported on stdout (results) / stderr (diagnostics).

## Install + first run

```shell
# Recommended on Sol — single-file install (fast cold start on the NFS home):
curl -fsSL https://github.com/Shu-Wan/solx/releases/latest/download/install.sh | sh
# Alternative — as a uv tool (isolated venv on $PATH):
uv tool install git+https://github.com/Shu-Wan/solx.git#subdirectory=solx

solx --version
solx init            # writes ~/.config/solx/config.toml (mode 0600)
solx config edit     # fill in templates + [keep] paths
solx config show     # sanity-check the resolved config
```

Both paths use [`uv`](https://docs.astral.sh/uv/) to provision a Python
≥ 3.11 (Sol's system `python3` is too old). Installing reaches the
network and writes `~/.local/bin/solx` — propose it and get the user's
OK rather than installing silently.

## When to use `solx` vs raw SLURM

`solx` is built for a human at a keyboard; for an agent it pays a
Python/NFS startup cost on Sol (~1s+ per `job` command vs ~0.05s for raw
`squeue`/`scancel` — `evals/runner/bench_solx_latency.sh`). So **for a
one-off read, run the SLURM command directly** (`squeue --me`,
`squeue -h -j "$SLURM_JOB_ID" -o %L`, `scancel <id>`); don't loop `solx`
for status polling. Use `solx` for `job start` (templated allocation that
waits), `job jump` (pty onto the node), and `keep` (CSV-∩-keep-list
renewal) — the multi-step ops where it removes real friction. (Cutting
this startup cost is on the roadmap.)

## Commands at a glance

| Command | What it does |
|---|---|
| `solx init [-f]` | Write a starter `config.toml` (offers an interactive walkthrough on a TTY). `-f`/`-y` overwrites. |
| `solx job list` (alias `ls`) | List your Sol jobs. |
| `solx job start [TEMPLATE]` | Request an interactive allocation from a template. |
| `solx job stop [JOBID]` | Cancel a job (prompts unless `-y`). |
| `solx job jump [JOBID]` | Open a shell on the job's compute node. Also `solx jump`. |
| `solx job time [JOBID]` | Print remaining wall-time (`D-HH:MM:SS`). |
| `solx keep` | Renew `/scratch` files Sol flagged, filtered by `[keep]` (prompts unless `-y`). |
| `solx config show` / `edit` | Show / edit the config. |
| `solx config import-solkeep` | Migrate a legacy `~/.solkeep` into the `[keep]` block. |
| `solx completions <bash\|zsh\|fish>` | Print a shell-completion script. |
| `solx version` / `--version`, `solx help` / `--help` | Version / help. |

`job` is also spelled `jobs`; `list` is also `ls`; `solx jump` is short
for `solx job jump`.

A typical interactive session:

```shell
solx job start debug     # request an allocation; waits for the grant, prints the job id
solx job list            # see it (RUNNING)
solx job time            # how much time is left
solx job jump            # open a shell on the compute node
# … work …
exit                     # back to the login node; the allocation stays alive
solx job stop            # cancel it when done
```

`solx job start` is for **interactive** allocations (via `salloc
--no-shell`). For batch work, use `sbatch your-script.sbatch` directly —
`solx` deliberately doesn't wrap `sbatch`.

## Configuration

One file: `~/.config/solx/config.toml` (or
`$XDG_CONFIG_HOME/solx/config.toml`).

```toml
default_shell = "bash"          # shell `solx job jump` opens on the compute node
default_template = "default"    # template `solx job start` uses with no argument
start_timeout = "10m"           # cap on how long `job start` waits for the queue

[jobs.default]
partition = "lightwork"
time = "1-0"                    # Slurm time format: D-HH:MM:SS
qos = "public"

[jobs.debug]
partition = "htc"              # the fast queue — good for quick tests
time = "0-1"

[jobs.gpu]
partition = "public"
gres = "gpu:a100:1"
time = "0-4"
extra_args = ["--mem=64G", "--cpus-per-task=8"]

# Scratch paths to renew when Sol flags them. Replace `sparky` with your
# ASURITE. Gitignore-style globs; ** matches any depth.
[keep]
include = ["/scratch/sparky/my-project", "/scratch/sparky/experiments/**"]
exclude = ["**/.venv", "**/.git", "**/__pycache__", "**/node_modules"]
```

`default_shell`, `default_template`, and ≥1 `[jobs.<name>]` are
required; `qos`/`gres`/`extra_args`/`[keep]` are optional. Anything
after `--` on `solx job start` is appended to `salloc` (last flag wins):
`solx job start gpu -- --mem=128G --time=8:00:00`.

## Leaving out the job id (verb-aware resolution)

`stop`, `jump`, and `time` take an optional `[JOBID]`. When omitted:

1. An explicit `JOBID` wins.
2. Else, if inside an allocation (`$SLURM_JOB_ID` set), that job.
3. Else `solx` looks at `squeue -u $USER`:
   - **No jobs** → it says so and stops.
   - **One job** → it uses that one.
   - **Several jobs** → `time`/`jump` auto-pick the **most recent**
     (highest job id) and note which; `stop` **refuses to guess** —
     it lists the candidates and exits 2 so you can't cancel the wrong
     job by accident.

`jump` attaches only to a **running** job. Acting from inside an
allocation warns about nesting (`jump`, `-q` silences) or self-cancel
(`stop`).

## Destructive-command confirmation contract

`solx job stop` and `solx keep` change state, so they confirm:

| Flag | Behavior |
|---|---|
| (none) | Show the plan, then prompt `… ? [y/N]`. |
| `-y` / `--yes` (or `-f` / `--force`) | Skip the prompt and do it. |
| `-n` / `--dry-run` | Show the plan, do nothing — no prompt. |

`-y` and `-n` are mutually exclusive (exit 2). In a **non-interactive
session** (no stdin TTY) without `-y`/`-n`, both **refuse with exit 2**
instead of hanging on a prompt — safe to drive from an agent.

## Output for agents

Output **auto-detects**: Rich tables on a terminal, **JSON when stdout
is not a TTY**. The global `--json` flag (before the subcommand, e.g.
`solx --json job list`) forces JSON anywhere. Results go to **stdout**,
all diagnostics/prompts/errors to **stderr**, so values pipe cleanly:

```shell
solx job time 12345                  # -> 00:54:37  (bare value)
solx --json job list | jq '.[].job_id'
solx --json keep --dry-run           # plan as JSON: exact counts + a capped sample
```

Exit codes: `0` success · `1` operational / nothing-to-do · `2`
under-specified, unconfirmed, or wrong-side (off-Sol). Every subcommand
exits 2 off-Sol with a redirect message, so a stray invocation on a
laptop is harmless.

## `solx keep` — renew flagged scratch files

`solx keep` reads Sol's warning CSVs from `$HOME`
(`scratch-dirs-pending-removal.csv`, `scratch-dirs-over-90days.csv`,
`scratch-dirs-inactive.csv`), keeps only directories that match your
keep-list, and `touch`es them. It only ever touches directories that are
**both** flagged by Sol **and** in your keep-list — nothing to do until
Sol flags something, and it never walks `/scratch` wholesale.

Keep-list source, in precedence order:

1. `--solkeep <file>` — a specific gitignore-style keep-list, if passed.
2. the `[keep]` block in the config (`include` / `exclude`). **Preferred.**
3. `~/.solkeep` — the **deprecated** legacy keep-list. Still read if
   present (so existing setups keep working), but `solx keep` prints a
   deprecation notice and **support is removed in solx 0.5.0**.

```shell
solx keep --dry-run -v        # preview which directories would be renewed
solx keep                     # renew them (prompts; -y to skip)
solx keep --stage pending     # only the most-urgent CSV
```

Flags: `--solkeep FILE`, `--stage {pending,over90,inactive,all}`,
`--csv-dir DIR` (default `$HOME`), `-j N` (parallel workers — default
small on purpose; `/scratch` is networked storage), `-y` / `-n` / `-v`.

This is metadata-heavy NFS I/O, which login nodes throttle — run a big
pass on a compute node or the DTN (`ssh soldtn`). See
[scratch.md](scratch.md) for the CSV schema and performance notes.

## Migrating off `~/.solkeep`

The old standalone `sol_renew.py` and the `~/.solkeep` keep-list are
deprecated. Migrate an existing `~/.solkeep` into the config once:

```shell
solx config import-solkeep    # folds ~/.solkeep into the [keep] block
solx config show              # review the result
```

It appends a `[keep]` block to `config.toml` (refusing if one already
exists, since a second `[keep]` table is invalid TOML — merge by hand
with `solx config edit` in that case). After migrating, `solx keep` uses
`[keep]` and the deprecation notice goes away.

## Shell completion

`solx completions <shell>` prints a completion script. For zsh, install
on `fpath` (one-time, position-independent):

```zsh
mkdir -p ~/.zfunc                      # any dir on fpath before compinit
solx completions zsh > ~/.zfunc/_solx  # add `fpath=(~/.zfunc $fpath)` before compinit if new
```

bash / fish:

```shell
solx completions bash > ~/.local/share/bash-completion/completions/solx
solx completions fish > ~/.config/fish/completions/solx.fish
```
