# `solx` - the CLI

`solx` is a small command-line tool for daily work on Sol. You SSH to
Sol yourself, then run `solx` from a login or compute node; it shells out
to Slurm and reads one config file. It does **not** touch your laptop,
construct ssh chains, or read `~/.ssh/*`. The skill prefers `solx` for
job and scratch work and installs it on first use.

This is the agent-facing reference. Everything `solx` does is local to
Sol and reported on stdout (results) / stderr (diagnostics).

## Install + first run

`solx` is one static binary - no Python, no `uv`, no toolchain. Install
is a download and a `chmod`:

```shell
mkdir -p ~/.local/bin
curl -fLo ~/.local/bin/solx https://github.com/Shu-Wan/solx/releases/latest/download/solx-x86_64-unknown-linux-musl
chmod +x ~/.local/bin/solx

solx --version
solx init            # writes ~/.config/solx/config.toml (mode 0600)
solx config edit     # fill in templates + [keep] paths
solx config show     # sanity-check the resolved config
```

The binary is fully static (musl), so it runs on Sol's RHEL 8 as-is.
Installing reaches the network and writes `~/.local/bin/solx` (make sure
that's on `$PATH`) - propose it and get the user's OK rather than
installing silently.

## When to use `solx` vs raw SLURM

For one-off reads the two are equivalent - use either. A warm `solx job`
read runs in ~0.12s on Sol, vs ~0.08s for raw `squeue` (measured -
`evals/runner/bench_solx_latency.sh`); the residual over `squeue` is just
the `squeue` subprocess `solx` spawns, and the native binary's startup
doesn't degrade under node load or a cold NFS cache. The raw equivalents,
for when `solx` isn't installed:
`squeue --me` (= `job list`), `squeue -h -j "$SLURM_JOB_ID" -o %L`
(= `job time`), `scancel <id>` (= `job stop -y <id>`). `solx` adds the
most on the multi-step ops: `job start` (templated allocation that
waits), `job jump` (pty onto the node), and `keep` (CSV-∩-keep-list
renewal).

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
--no-shell`). For batch work, use `sbatch your-script.sbatch` directly -
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
partition = "htc"              # htc carries A100s; a 4h GPU run fits its wall
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
`solx job start gpu -- -p public --time=8:00:00` jumps to `public` when a
run needs more than htc's 4-hour wall.

## Leaving out the job id (verb-aware resolution)

`stop`, `jump`, and `time` take an optional `[JOBID]`. When omitted:

1. An explicit `JOBID` wins.
2. Else, if inside an allocation (`$SLURM_JOB_ID` set), that job.
3. Else `solx` looks at `squeue -u $USER`:
   - **No jobs** -> it says so and stops.
   - **One job** -> it uses that one.
   - **Several jobs** -> `time`/`jump` auto-pick the **most recent**
     (highest job id) and note which; `stop` **refuses to guess** -
     it lists the candidates and exits 2 so you can't cancel the wrong
     job by accident.

`jump` attaches only to a **running** job. Acting from inside an
allocation warns about nesting (`jump`, `-q` silences) or self-cancel
(`stop`).

## Destructive-command confirmation contract

`solx job stop` and `solx keep` change state, so they confirm:

| Flag | Behavior |
|---|---|
| (none) | Show the plan, then prompt `... ? [y/N]`. |
| `-y` / `--yes` (or `-f` / `--force`) | Skip the prompt and do it. |
| `-n` / `--dry-run` | Show the plan, do nothing - no prompt. |

`-y` and `-n` are mutually exclusive (exit 2). In a **non-interactive
session** (no stdin TTY) without `-y`/`-n`, both **refuse with exit 2**
instead of hanging on a prompt - safe to drive from an agent.

## Output for agents

Output **auto-detects**: Rich tables on a terminal, **JSON when stdout
is not a TTY**. The `--json` flag forces JSON anywhere; it works before
the subcommand (`solx --json job list`) or after it (`solx job list
--json`) - except after `job start`, where post-command tokens pass
through to `salloc`. Results go to **stdout**, all
diagnostics/prompts/errors to **stderr**, so values pipe cleanly:

```shell
solx job time 12345                  # -> 00:54:37  (bare value)
solx --json job list | jq '.[].job_id'
solx --json keep --dry-run           # plan as JSON: exact counts + a capped sample
```

Exit codes: `0` success · `1` operational / nothing-to-do · `2`
under-specified, unconfirmed, or wrong-side (off-Sol). Every subcommand
exits 2 off-Sol with a redirect message, so a stray invocation on a
laptop is harmless.

## `solx keep` - renew flagged scratch files

`solx keep` reads Sol's warning CSVs from `$HOME`
(`scratch-dirs-pending-removal.csv`, `scratch-dirs-over-90days.csv`,
`scratch-dirs-inactive.csv`), keeps only directories that match your
keep-list, and `touch`es them. It only ever touches directories that are
**both** flagged by Sol **and** in your keep-list - nothing to do until
Sol flags something, and it never walks `/scratch` wholesale.

The keep-list is the `[keep]` block in the config (`include` / `exclude`),
matched gitignore-style. It's the only keep-list source.

```shell
solx keep --dry-run -v        # preview which directories would be renewed
solx keep                     # renew them (prompts; -y to skip)
solx keep --stage pending     # only the most-urgent CSV
```

Flags: `--stage {pending,over90,inactive,all}`, `--csv-dir DIR` (default
`$HOME`), `-j N` (parallel workers - default small on purpose; `/scratch`
is networked storage), `-y` / `-n` / `-v`.

This is metadata-heavy NFS I/O, which login nodes throttle - run a big
pass on a compute node or the DTN (`ssh soldtn`). See
[scratch.md](scratch.md) for the CSV schema and performance notes.

## Shell completion

`solx completions <shell>` prints a fully static completion script -
completing never runs `solx`, so the first Tab is instant. Add it to the
user's shell startup file:

```shell
# bash — ~/.bashrc
eval "$(solx completions bash)"

# zsh — ~/.zshrc (after compinit)
eval "$(solx completions zsh)"

# zsh, fpath install (no per-shell eval) — the same script works autoloaded:
#   mkdir -p ~/.zfunc && solx completions zsh > ~/.zfunc/_solx
#   then in ~/.zshrc, before compinit:  fpath+=(~/.zfunc)

# fish — ~/.config/fish/config.fish
solx completions fish | source
```
