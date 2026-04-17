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

## `.solignore` syntax

Lives at `$HOME/.solignore`. Gitignore-style patterns, but the
semantics are **inverted**: a matched path is *kept* (touched by
`sol_renew.py`). Directories that do not match any rule are skipped.

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

# default: .solignore ∩ all non-removed stages
sol_renew.py

# only chase the most urgent bucket
sol_renew.py --stage pending

# override input locations
sol_renew.py --csv-dir /tmp/sol-csvs --solignore /tmp/keep.ignore

# raise parallelism explicitly
sol_renew.py -j 16
```

Exit codes:

- `0` — every flagged-and-kept directory was touched successfully
- `1` — at least one directory failed
- `2` — no rules loaded (empty or missing `.solignore`)

## Performance notes

- Each CSV row is already a leaf directory identified by Sol's scan.
  `sol_renew.py` runs exactly one `find | xargs touch` pipeline per
  row. It does not walk `/scratch` wholesale.
- `touch -a -m -c` refreshes both `atime` and `mtime` (`-c` avoids
  creating files that do not exist).
- A `touch` pass over a directory with many small files on a shared
  cluster filesystem can take a long time. The script reports progress
  per-directory, not per-file — a single line may sit on screen for
  minutes. Do not cancel the run based on a silent stretch.
- The default parallelism is conservative to avoid hammering the
  shared filesystem. Raise it with `-j N` once you know the cluster
  can absorb the concurrent walks.

## Emergency single-path touch

To refresh a single path outside the CSV workflow:

```shell
find /scratch/$USER/my_dir -type f -print0 \
  | xargs -0 -r -n 500 touch -a -m -c --
```

That is the same primitive `sol_renew.py` runs internally.
