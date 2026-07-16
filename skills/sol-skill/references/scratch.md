# Scratch renewal reference

Renewal is done with **`solx keep`** - the command, flags, and exit codes are in
[solx.md](solx.md), and a worked walkthrough is in
[../../docs/scratch.md](../../docs/scratch.md). This reference covers only the
**Sol-specific** parts: the deletion pipeline, what the keep-list matches, and
where to run a large pass.

## Sol's deletion pipeline

ASU Research Computing runs a layered deletion policy on `/scratch`. The exact
thresholds, cadence, and CSV filenames are defined upstream and authoritative:
<https://docs.rc.asu.edu/scratch>.

At the time of writing, Sol drops per-stage warning CSVs in each user's `$HOME`:

| CSV file                            | `--stage` key |
|-------------------------------------|---------------|
| `scratch-dirs-pending-removal.csv`  | `pending`     |
| `scratch-dirs-over-90days.csv`      | `over90`      |
| `scratch-dirs-inactive.csv`         | `inactive`    |
| `scratch-dirs-removed.csv`          | (post-facto)  |

`solx keep` reads only the `Directory` column (the flagged leaf directories). If
upstream renames a CSV or changes the schema, the tool needs updating - check
the docs link before filing a bug. A dry-run may still list a directory a prior
run already refreshed but Sol hasn't dropped from the CSV yet; that's expected.

## What the keep-list matches

`solx keep` renews a directory only when it is **both** flagged by Sol **and**
matched by your keep-list (the `[keep]` block in the config). Patterns are
gitignore-style and match the
**directory paths** in the CSVs - so matching decides which *whole flagged
directories* get touched, not individual files within them. A bare path matches
that directory and everything under it; `**` matches any depth. Carve out
regenerable trees (`.venv`, `.git`, `__pycache__`, `node_modules`) with
`exclude`: renewing them wastes the pass on files that rebuild for free.

## Where to run a big pass

A renewal is metadata-heavy I/O - the load Sol's **login nodes throttle**.

- **Compute node** (`$SLURM_JOB_ID` set): run it directly.
- **Login node**: move a large pass to the **DTN** (`ssh soldtn`, many cores,
  not throttled), a compute node, or a short `htc` batch job. Raise `-j` only
  where the cores exist (a 4-core node can't feed many workers; the DTN can).

`solx` installs to `~/.local/bin`, which a non-interactive `ssh soldtn '...'` may
not have on `PATH`, so prepend it:

```shell
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; solx keep --stage inactive -j 24 -y'
```

## Emergency single-path touch (no `solx`)

To refresh one path outside the CSV workflow - the same primitive `solx keep`
uses internally:

```shell
find /scratch/$USER/my_dir -type f -print0 | xargs -0 -r -n 500 touch -a -m -c --
```
