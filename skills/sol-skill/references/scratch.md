# Scratch renewal reference

Renewal is done with **`solx keep`** (see [solx.md](solx.md) for the
CLI as a whole). This reference covers Sol's deletion pipeline, the
keep-list pattern syntax, `solx keep` invocations, where to run a big
pass, and the performance characteristics.

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
…) vary per file; `solx keep` only reads `Directory`.

If upstream renames a CSV or changes the schema, the tool will need
updating. Check the Sol docs link above before filing a bug.

These CSVs are regenerated on Sol's own cadence. A dry-run may list
directories that have already been refreshed in a previous run but
not yet dropped from the CSV — this is expected.

## Keep-list pattern syntax

The keep-list decides which **flagged directories** get renewed.
Patterns are gitignore-style and are matched against the `Directory`
column of Sol's CSVs (directory paths, not individual files) — a
matched directory is *kept* (its files are touched); an unmatched one is
skipped. You can't keep only `*.log` inside a directory while discarding
the rest; matching decides which *whole flagged directories* get
touched.

The same syntax is used in two places:

- the config **`[keep]` block** — `include` patterns keep, `exclude`
  patterns carve back out. **This is the supported home.**
- a legacy **`~/.solkeep`** file — one list where `!` negates. Deprecated
  (see below).

Patterns are literal — no shell expansion — so write your real
username, not `$USER`.

| Pattern                              | Meaning                                             |
|--------------------------------------|-----------------------------------------------------|
| `# comment`                          | ignored                                             |
| `/scratch/sparky/project`             | bare path = directory prefix; matches `path` and `path/**` |
| `/scratch/sparky/logs/*.log`          | glob; `*` does not cross `/`                        |
| `/scratch/sparky/data/**`             | `**` matches any depth                              |
| `**/__pycache__`                      | an `exclude` carve-out (or `!`-prefixed in `~/.solkeep`) |
| `/scratch/sparky/cache/`              | trailing `/` matches only if it's a directory       |

In `[keep]`, put keep patterns in `include` and carve-outs in `exclude`.
In a `~/.solkeep`, both live in one list and a `!` prefix marks a
carve-out; rules are evaluated top-to-bottom, last match wins (same as
gitignore). Carve out regenerable trees (`.venv`, `.git`, `__pycache__`,
`node_modules`): renewing them spends the pass on files that rebuild for
free, and letting them expire costs nothing.

## `solx keep` invocations

```shell
# preview the plan (run this first)
solx keep --dry-run -v

# default: [keep] ∩ all non-removed stages
solx keep

# only chase the most urgent bucket
solx keep --stage pending

# override input locations
solx keep --csv-dir /tmp/sol-csvs --solkeep /tmp/my-keep-list

# raise parallelism explicitly where the cores exist
solx keep -j 16

# machine-readable plan (counts + a capped sample; full plan spilled to a temp file)
solx --json keep --dry-run
```

Exit codes:

- `0` — renewed successfully (or nothing flagged matched `[keep]`)
- `1` — at least one directory failed to enumerate, or a touch batch failed
- `2` — no keep-list found, mutually-exclusive flags, non-interactive
  without `-y`/`-n`, bad `--csv-dir`, or off-Sol

`solx keep` prompts before touching unless `-y`; `-n` previews without
prompting; in a non-interactive session it refuses rather than hang.

## Where to run it

A renewal is metadata-heavy I/O — the kind of load Sol's login nodes
throttle. Decision rule (see SKILL.md's "Where to run it"):

- **Compute node** (`$SLURM_JOB_ID` set): run it directly.
- **Login node** (`$SLURM_JOB_ID` unset): move the heavy pass to the
  **DTN** (`ssh soldtn`, many cores, not throttled), a compute node
  via `solx job start` / `interactive`, or a short `htc` batch job.

**`PATH` gotcha over `ssh soldtn`.** `solx` installs to `~/.local/bin`.
A non-interactive `ssh soldtn '<cmd>'` may not source the profile that
puts `~/.local/bin` on `PATH`, so prepend it explicitly:

```shell
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; \
  export UV_CACHE_DIR=/scratch/$USER/.cache/uv; \
  solx keep --stage inactive -j 24 -y'
```

## Performance notes

- Execution is a streaming pipeline on one `-j`-sized worker pool:
  enumerate a kept directory, split its files into evenly-sized
  batches, and `touch -a -m -c` the batches across the pool. A bounded
  window of in-flight tasks keeps peak memory a small multiple of `-j`
  regardless of the total file count. A large directory spreads its
  batches over every worker, so `-j` sets the parallelism of the whole
  run including its largest directory.
- Enumeration uses [`fd`](https://github.com/sharkdp/fd) (then
  `rg --files`) when on `PATH`, falling back to `find`. `fd`/`rg`
  walk a tree multithreaded, so they enumerate a large directory
  faster than `find`. They are run with `--hidden --no-ignore` so they
  list *every* file — without those flags `fd`/`rg` skip dotfiles and
  honor `.gitignore`, which would under-protect files. The touch step
  always uses `touch` via `xargs`.
- Scope stays bounded by what Sol flagged ∩ your keep-list; the tool
  does not start from `/scratch` and recurse. An overly broad keep-list
  or a large CSV-listed subtree still produces a large touch pass.
- `touch -a -m -c` refreshes both `atime` and `mtime` (`-c` avoids
  creating files that do not exist). A file deleted between
  enumeration and touch is silently skipped (`-c` exits 0 on a
  missing path), so it is not counted as a failure; a per-batch
  failure means a real error such as a permission or I/O problem.
- Progress is reported as the run proceeds: a live bar on a terminal,
  or one line per completed file-batch in non-interactive output (e.g.
  over `ssh`).
- The default parallelism is conservative to avoid hammering the
  shared filesystem. Raise it with `-j N` once you know the node it
  runs on has the cores to feed the workers (a 4-core compute node
  can't, the DTN can).

## Migrating off the legacy `~/.solkeep`

The standalone `sol_renew.py` script (removed from this skill) and the
`~/.solkeep` keep-list it read are **deprecated**. `solx keep` still
reads a `~/.solkeep` when no `[keep]` block is configured, but prints a
deprecation notice and **drops support in solx 0.5.0**. Migrate once:

```shell
solx config import-solkeep    # folds ~/.solkeep into the [keep] block
solx config show              # review
```

## Emergency single-path touch

To refresh a single path outside the CSV workflow (no `solx` needed):

```shell
find /scratch/$USER/my_dir -type f -print0 \
  | xargs -0 -r -n 500 touch -a -m -c --
```

That is the same primitive `solx keep` runs internally.
