# Scratch renewal reference

Renewal is done with **`solx keep`** - the command, flags, and exit codes are in
[solx.md](solx.md), and a worked walkthrough is in
[../../../docs/scratch.md](../../../docs/scratch.md). This reference covers only the
**Sol-specific** parts: the deletion pipeline, what the keep-list matches, and
where to run a large pass.

## Sol's deletion pipeline

ASU Research Computing runs a layered deletion policy on `/scratch`. The exact
thresholds, cadence, and CSV filenames are defined upstream and authoritative:
<https://docs.rc.asu.edu/scratch>.

`solx keep` reads `sol-scratch-cleanup.csv` in `$HOME`. Its `Action` column
selects the stage, `Path` names the flagged entry, and `Type` is `directory`
or `file`. The legacy per-stage files are also supported:

| Unified `Action` | Legacy CSV | `--stage` key |
| --- | --- | --- |
| `MARKED FOR REMOVAL` | `scratch-dirs-pending-removal.csv` | `pending` |
| `Final Warning` | `scratch-dirs-over-90days.csv` | `over90` |
| `Warning` | `scratch-dirs-inactive.csv` | `inactive` |

The parser uses `Directory` when present, otherwise `Path`. Repeated paths
across files are renewed once, at the first selected stage. Unknown unified
actions or types cause an error. Post-deletion reports are not read.
A dry-run may still list a path that a prior run refreshed until Sol updates
the warning CSV.

## What the keep-list matches

`solx keep` renews a path only when it is **both** flagged by Sol **and**
matched by your keep-list (the `[keep]` block in the config). Patterns are
gitignore-style and match the **flagged paths** in the CSVs. A bare path matches
that path and its descendants; `**` matches any depth. A kept regular file is
touched directly. A kept directory is walked recursively, including hidden
entries and ignored files; symlinks are skipped.

Excludes apply only when selecting flagged rows. They do not prune a kept
directory's walk: keeping `/scratch/$USER/results` also renews its `.cache`
subtree even if `**/.cache` is excluded. To avoid renewing regenerable trees,
select more specific flagged paths.

Permission failures remain failures (exit code 1). The summary groups them by
owner and reports whether all entries are empty directories, with unknown
emptiness when a directory cannot be read. Ask the owner to renew them or
grant group write permission. `keep` never deletes, recreates, or changes
ownership of entries.

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
