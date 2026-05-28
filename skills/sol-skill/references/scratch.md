# Scratch renewal reference

## Sol's deletion pipeline

ASU Research Computing runs a layered deletion policy on `/scratch`.
The exact thresholds, warning cadence, and CSV filenames are defined
upstream — treat that page as authoritative:

<https://docs.rc.asu.edu/scratch>

At the time this tool was written, Sol wrote four CSV warnings into
each user's `$HOME`, one per stage (most-urgent to post-facto):

| CSV file                            | Stage key   |
|-------------------------------------|-------------|
| `scratch-dirs-pending-removal.csv`  | `pending`   |
| `scratch-dirs-over-90days.csv`      | `over90`    |
| `scratch-dirs-inactive.csv`         | `inactive`  |
| `scratch-dirs-removed.csv`          | (post-facto) |

All CSVs share a `Directory` column listing the flagged leaf
directories. Other columns (`Last Used`, `File Count`, `Size (GiB)`,
…) vary per file; `sol_renew.py` only reads `Directory`.

If upstream renames a CSV or changes the schema, the tool will need
updating. Check the Sol docs link above before filing a bug.

These CSVs are regenerated on Sol's own cadence. A dry-run may list
directories that have already been refreshed in a previous run but
not yet dropped from the CSV — this is expected.

## `.solkeep` syntax

Lives at `$HOME/.solkeep`. Gitignore-style patterns; a matched path
is *kept* (touched by `sol_renew.py`). Directories that do not match
any rule are skipped.

**Patterns match directory paths**, not files. `sol_renew.py` applies
`.solkeep` rules to the `Directory` column of Sol's CSVs — each row
is a directory Sol has flagged. You can't ask the tool to keep only
`*.log` inside a directory while discarding the rest; matching decides
which *whole flagged directories* get touched.

Patterns are literal — no shell expansion — so write your real
username, not `$USER`.

| Pattern                              | Meaning                                             |
|--------------------------------------|-----------------------------------------------------|
| `# comment`                          | ignored                                             |
| `/scratch/sparky/project`             | bare path = directory prefix; matches `path` and `path/**` |
| `/scratch/sparky/logs/*.log`          | glob; `*` does not cross `/`                        |
| `/scratch/sparky/data/**`             | `**` matches any depth                              |
| `!/scratch/sparky/data/tmp/**`        | `!` negates — carve out a subtree                   |
| `/scratch/sparky/cache/`              | trailing `/` matches only if it's a directory       |

Rules are evaluated top-to-bottom; the last matching rule wins (same
as gitignore).

## `sol_renew.py` invocations

```shell
# preview the plan
sol_renew.py --dry-run -v

# default: .solkeep ∩ all non-removed stages
sol_renew.py

# only chase the most urgent bucket
sol_renew.py --stage pending

# override input locations
sol_renew.py --csv-dir /tmp/sol-csvs --solkeep /tmp/my-keep-list

# raise parallelism explicitly
sol_renew.py -j 16
```

Exit codes:

- `0` — every flagged-and-kept directory was touched successfully
- `1` — at least one directory failed
- `2` — no rules loaded (empty or missing `.solkeep`)

## Where to run it

A renewal is metadata-heavy I/O — the kind of load Sol's login nodes
throttle. Decision rule (see SKILL.md's "Where to run it"):

- **Compute node** (`$SLURM_JOB_ID` set): run it directly.
- **Login node** (`$SLURM_JOB_ID` unset): move the heavy pass to the
  **DTN** (`ssh soldtn`, many cores, not throttled), a compute node
  via `interactive`, or a short `htc` batch job.

**`uv`-on-`PATH` gotcha over `ssh soldtn`.** The script's shebang is
`#!/usr/bin/env -S uv run --script`, so `uv` must be on `PATH`. A
non-interactive `ssh soldtn '<cmd>'` may not source the profile that
adds `~/.local/bin`, so prepend it explicitly:

```shell
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; \
  export UV_CACHE_DIR=/scratch/$USER/.cache/uv; \
  /path/to/sol_renew.py --stage inactive -j 24'
```

## Performance notes

- Work is sharded at the **file** level, in two phases, both run
  across the `-j` worker pool: (1) enumerate every kept directory,
  then (2) `touch -a -m -c` the resulting files in evenly-sized
  batches. A single 50k-file directory becomes many batches spread
  over all workers instead of one work unit pinned to one worker — so
  `-j` scales the slowest single directory, not just the count of
  directories.
- Enumeration prefers [`fd`](https://github.com/sharkdp/fd) (then
  `rg --files`) when on `PATH`, falling back to `find`. `fd`/`rg`
  walk a large tree multithreaded and beat single-threaded `find` on
  the one giant directory whose enumeration would otherwise serialize
  a worker. The tool is run with `--hidden --no-ignore` so it lists
  *every* file — without those flags `fd`/`rg` skip dotfiles and
  honor `.gitignore`, which would silently under-protect files. The
  touch phase is always `touch` via `xargs`.
- Scope stays bounded by what Sol flagged ∩ `.solkeep`; the tool does
  not start from `/scratch` and recurse. An overly broad keep-list or
  a large CSV-listed subtree still produces a large touch pass.
- `touch -a -m -c` refreshes both `atime` and `mtime` (`-c` avoids
  creating files that do not exist). A file deleted between
  enumeration and touch is silently skipped (`-c` exits 0 on a
  missing path), so it is not counted as a failure; a per-batch
  failure means a real error such as a permission or I/O problem.
- Progress is reported per file-batch as each completes; a single
  line may sit on screen for minutes on a large batch. Do not cancel
  the run based on a silent stretch.
- The default parallelism is conservative to avoid hammering the
  shared filesystem. Raise it with `-j N` once you know the node it
  runs on has the cores to feed the workers (a 4-core compute node
  can't, the DTN can).

## Emergency single-path touch

To refresh a single path outside the CSV workflow:

```shell
find /scratch/$USER/my_dir -type f -print0 \
  | xargs -0 -r -n 500 touch -a -m -c --
```

That is the same primitive `sol_renew.py` runs internally.
