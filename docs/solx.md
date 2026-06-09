# ☀️ `solx` — user manual

`solx` is a command-line tool for daily work on ASU's
[Sol supercomputer](https://docs.rc.asu.edu/). SSH to Sol, then run `solx` from
a login or compute node. It keeps the routine Slurm loop in the terminal: list
your jobs, request an interactive allocation from a template, open a shell on
the compute node, cancel, check remaining time, and renew `/scratch` files that
Sol has flagged for deletion.

The motivation is practical: you should not have to memorize Slurm flags for
common interactive work, and you should not have to jump between a website
portal and the terminal just to keep a session moving. Open OnDemand is still
the right tool for browser-first workflows; `solx` is the terminal-native path
for humans, scripts, and CLI agents. The companion
[`sol-skill`](../skills/sol-skill/SKILL.md) teaches an AI assistant when to use
`solx`, when raw Slurm is faster, and how to stay within Sol conventions.

Install instructions are in [`solx/README.md`](../solx/README.md). The short
version, on Sol:

```shell
# Recommended on Sol — single-file install (re-run to upgrade):
curl -fsSL https://github.com/Shu-Wan/solx/releases/latest/download/install.sh | sh
# Or as a uv tool:
uv tool install git+https://github.com/Shu-Wan/solx.git#subdirectory=solx

solx --version
solx init        # writes ~/.config/solx/config.toml
```

---

## Commands at a glance

| Command | What it does |
|---|---|
| `solx init [-f]` | Write a starter `config.toml`. |
| `solx job list` (alias `ls`) | List your Sol jobs. |
| `solx job start [TEMPLATE]` | Request an interactive allocation. |
| `solx job stop [JOBID]` | Cancel a job. |
| `solx job jump [JOBID]` | Open a shell on the job's compute node. Also `solx jump`. |
| `solx job time [JOBID]` | Print the time remaining on a job. |
| `solx keep` | Renew `/scratch` files Sol flagged for deletion. |
| `solx config show` / `edit` | Show or edit your config. |
| `solx config import-solkeep` | Migrate a legacy `~/.solkeep` into `[keep]`. |
| `solx completions <bash\|zsh\|fish>` | Print a shell-completion script. |
| `solx version` (alias `--version`) | Print the version. |
| `solx help` (alias `--help`) | Show help. |

`job` is also spelled `jobs`, `list` is also `ls`, and `solx jump` is short
for `solx job jump`.

A typical session:

```shell
solx config edit           # set up your templates and [keep] paths
solx job start debug       # request an allocation; prints the job id
solx job jump              # open a shell on the compute node
# … do your work …
exit                       # back to the login node; the allocation stays alive
```

## For a one-off, raw Slurm is faster

A `solx job` command pays Python startup on Sol's NFS home (≈1 s) where the
underlying Slurm command returns in ≈0.05 s. So for a quick **status**,
**time-left**, or **cancel**, skip `solx` and run Slurm (or Sol's wrappers)
directly:

```shell
squeue --me                              # not `solx job list`     (also: myjobs, sq)
squeue -h -j "$SLURM_JOB_ID" -o %L       # time left, inside a job (not `solx job time`)
scancel <jobid>                          # cancel a known job      (not `solx job stop`)
myfairshare                              # scheduling priority
```

`solx` earns its cost on the multi-step operations: `job start` (allocate from a
template and wait), `job jump` (drop a shell onto the node), and `keep`. That
gap is being closed — see [`ROADMAP.md`](ROADMAP.md).

---

## Shell completion

`solx completions <shell>` prints a completion script for **bash**, **zsh**, or
**fish**. Add it to your shell's startup file, then restart your shell:

```shell
# bash — add to ~/.bashrc
eval "$(solx completions bash)"

# zsh — add to ~/.zshrc (after compinit)
eval "$(solx completions zsh)"

# fish — add to ~/.config/fish/config.fish
solx completions fish | source
```

---

## Configuration

`solx` reads one file: `~/.config/solx/config.toml` (or
`$XDG_CONFIG_HOME/solx/config.toml`). Run `solx init` to create a starter,
then `solx config edit` to fill it in. On a terminal, `solx init` offers a
short walkthrough — pick the shell `solx job jump` opens, and (if you have a
`~/.solkeep`) confirm importing its patterns into `[keep]`. A complete
example:

```toml
# The shell `solx job jump` opens on the compute node.
default_shell = "bash"

# Which template `solx job start` uses when you don't name one.
default_template = "default"

# How long `solx job start` waits for the queue before giving up.
start_timeout = "10m"

# Job templates — run `solx job start <name>` to allocate one.
[jobs.default]
partition = "lightwork"
time = "1-0"          # 1 day. Slurm time format: D-HH:MM:SS
qos = "public"

[jobs.debug]
partition = "htc"     # the fast queue — good for quick tests
time = "0-1"          # 1 hour

[jobs.gpu]
partition = "public"
gres = "gpu:a100:1"
time = "0-4"
extra_args = ["--mem=64G", "--cpus-per-task=8"]

# Scratch paths to renew when Sol flags them. Replace `sparky` with your
# ASURITE. Patterns are gitignore-style; ** matches any depth.
[keep]
include = ["/scratch/sparky/my-project", "/scratch/sparky/experiments/**"]
exclude = ["**/__pycache__", "**/.venv"]
```

### What each setting means

| Setting | Required | Meaning |
|---|---|---|
| `default_shell` | yes | Shell opened by `solx job jump` on the compute node. |
| `default_template` | yes | Template used by `solx job start` with no argument. Must match a `[jobs.*]` name. |
| `start_timeout` | no (default `"10m"`) | How long `solx job start` waits for the queue. `--timeout` overrides it per run. |
| `[jobs.<name>]` | at least one | A job template you allocate with `solx job start <name>`. |
| `[jobs.<name>].partition` | yes | Slurm partition (`-p`). |
| `[jobs.<name>].time` | yes | Wall-time limit (`-t`), e.g. `1-0`, `0-4`, `8:00:00`. |
| `[jobs.<name>].qos` | no | Quality of service (`-q`). |
| `[jobs.<name>].gres` | no | Generic resource, e.g. `gpu:a100:1`. |
| `[jobs.<name>].extra_args` | no | Extra flags passed straight to `salloc`, e.g. `["--mem=64G"]`. |
| `[keep]` | no | Scratch-renewal settings. Without it, `solx keep` does nothing. |
| `[keep].include` | yes (if `[keep]` present) | Paths to renew. gitignore-style globs; `**` for any depth. |
| `[keep].exclude` | no | Carve-outs from `include`, e.g. `**/__pycache__`. |

`solx config show` prints the resolved config so you can sanity-check it.

---

## Starting and using a job

```shell
solx job start              # uses default_template
solx job start gpu          # uses the [jobs.gpu] template
solx job start gpu -n       # dry run: print the salloc command, submit nothing
solx job start gpu -- --mem=128G --time=8:00:00   # override flags for one run
```

`solx job start` requests the allocation and **waits until the queue grants
it**, then prints the job id. The allocation keeps running in the background —
attach to it whenever you like with `solx job jump`. Anything after `--` is
passed straight to `salloc`, and the last flag wins, so you can override a
template's defaults for a single run.

`solx job start` is for **interactive** allocations. For batch jobs, use
`sbatch your-script.sbatch` directly.

---

## Leaving out the job id

`solx job stop`, `jump`, and `time` all take an optional `[JOBID]`. When you
leave it out, `solx` works out which job you mean:

1. If you pass a `JOBID`, that wins.
2. Otherwise, if you're on a compute node inside a job (`$SLURM_JOB_ID` is
   set), that job is used.
3. Otherwise `solx` looks at your jobs in the queue:
   - **No jobs** → it tells you and stops.
   - **One job** → it uses that one.
   - **Several jobs** → it depends on the command (below).

With several jobs and no id:

- **`solx job time`** and **`solx job jump`** use your **most recent** job
  (the highest job id — usually the one you just started). They print a note
  saying which one they picked.
- **`solx job stop`** will **not** guess — it lists your jobs and asks you to
  name one, so you can't cancel the wrong job by accident.

`solx job jump` only attaches to a **running** job. If you run it from *inside*
an allocation it warns you that you're nesting a shell (pass `-q` to silence
the note), and still attaches. `solx job stop` on the job you're currently
inside warns you that cancelling it will end your session before it asks to
confirm.

---

## Cancelling and renewing — confirmations

`solx job stop` and `solx keep` change things (cancel a job, or update file
timestamps), so they ask before acting:

| Flag | Behavior |
|---|---|
| (none) | Show what will happen, then ask `… ? [y/N]`. |
| `-y` / `--yes` (or `-f` / `--force`) | Skip the question and do it. |
| `-n` / `--dry-run` | Show the plan and do nothing — no question. |

`-y` and `-n` can't be used together. If you run a confirm-needing command
where there's no terminal to ask on (a pipe, a script, a cron job) and you
didn't pass `-y` or `-n`, `solx` stops and tells you to add one of them rather
than hang waiting for an answer. `solx init` works the same way when a config
already exists — pass `-f` (or `-y`) to overwrite it.

---

## `solx keep` — renew flagged scratch files

When `/scratch` files of yours are aging out, Sol drops warning CSVs in your
home directory (`scratch-dirs-pending-removal.csv`,
`scratch-dirs-over-90days.csv`, `scratch-dirs-inactive.csv`). `solx keep` reads
those, keeps only the directories that match your **keep-list**, and refreshes
their timestamps with `touch`. It only ever touches directories that are
**both** flagged by Sol **and** in your keep-list — so there's nothing for it
to do until Sol actually flags something.

**Where the keep-list comes from**, in precedence order:

1. `--solkeep <file>` — a specific gitignore-style keep-list, if you pass one.
2. the `[keep]` block in your `solx` config (`include` / `exclude`).
3. `~/.solkeep` — a **deprecated** legacy keep-list. `solx keep` still reads it
   if present (so existing setups keep working) but prints a deprecation notice;
   **support is removed in solx 0.5.0**. Migrate it once with `solx config
   import-solkeep`. (Format: one pattern per line, `!` carves a subtree out, a
   bare path means that directory and everything under it — last match wins.)

```shell
solx keep --dry-run         # preview exactly which directories would be renewed
solx keep                   # renew them (asks to confirm; -y to skip)
solx keep --stage pending   # only the most-urgent CSV
```

| Flag | Meaning |
|---|---|
| `--solkeep FILE` | Use a specific gitignore-style keep-list (overrides `[keep]`). |
| `--stage {pending,over90,inactive,all}` | Which warning CSVs to read. Default `all`. |
| `--csv-dir DIR` | Where Sol's CSVs live. Default your home directory. |
| `-j N`, `--jobs N` | How many parallel workers. The default is small on purpose — `/scratch` is networked storage. |
| `-y` / `-n` / `-v` | Confirm / dry-run / show the full kept and skipped lists. |

This does a lot of small filesystem operations, which Sol's login nodes
throttle. For a big renewal, run it on a compute node or the data-transfer
node (`ssh soldtn`).

If there's no keep-list anywhere — no `[keep]` block and no `~/.solkeep` —
`solx keep` stops and points you to `solx config edit`.

---

## Output for scripts

On a terminal, `solx` prints readable tables. If you **pipe** the output or
pass **`--json`** (before the subcommand, e.g. `solx --json job list`), the
data commands print JSON instead, so you can post-process with `jq`. The
actual result goes to standard output and all messages go to standard error,
so a value pipes cleanly on its own:

```shell
solx job time 12345              # -> 00:54:37
solx --json job list | jq '.[].job_id'
```

`solx keep --json` summarizes the plan with counts and a short sample rather
than printing thousands of paths; when the list is long, the complete plan is
written to a temp file and its path is included in the output.

---

## Under the hood

### `solx job start` — headless allocations

Sol runs Slurm 25.x, which supports `salloc --no-shell` natively.
`solx job start`:

1. Builds the `salloc --no-shell -J solx-<template> -p <partition> -t <time> …`
   argv from your template.
2. Runs `salloc`, which **blocks until the queue grants the allocation** (no
   polling needed).
3. Parses the granted jobid from `salloc`'s stderr (`Granted job allocation N`).
4. Returns. The allocation keeps running in the background as a "headless"
   reservation — nothing consumes it until you attach.
5. You attach with `solx job jump`, which execs
   `srun --jobid=N --overlap --pty $default_shell` to drop you onto the node.

If the queue stalls, `start_timeout` (CLI `--timeout` overrides) caps the wait
so a stuck request surfaces instead of hanging forever.

### `solx keep` — CSV ∩ keep-list

Sol drops warning CSVs in `$HOME` as files age out
(`scratch-dirs-pending-removal.csv`, `scratch-dirs-over-90days.csv`,
`scratch-dirs-inactive.csv`). `solx keep`:

1. Reads those CSVs from `--csv-dir` (default `$HOME`).
2. Filters the flagged directories through your keep-list (`--solkeep` file >
   the `[keep]` config block > `~/.solkeep`), compiled with `pathspec`
   gitignore-style.
3. Runs `touch -a -m -c` on the intersection — only directories that **both**
   appear in a CSV **and** match the keep-list. It never walks `/scratch`
   wholesale.

So `solx keep` can't be used to keep arbitrary files alive on a cron — there's
nothing to do until Sol drops a warning CSV.
