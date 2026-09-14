# Keeping `/scratch` files alive with `solx keep`

Sol writes scratch cleanup warnings to `~/sol-scratch-cleanup.csv`.
`solx keep` reads it alongside the legacy per-stage CSVs and renews flagged
files and directories that match your keep-list.

## The idea

`solx keep` renews a path only when it is **both**:

1. **flagged by Sol** - listed in one of the warning CSVs Sol writes to `$HOME`
   (`sol-scratch-cleanup.csv` or the legacy `scratch-dirs-*.csv` warning files),
   **and**
2. **matched by your keep-list** - the `[keep]` block in your config.

So there is nothing to do until Sol actually flags something, and a stray
keep-list can't keep arbitrary files alive forever. It only ever touches
timestamps (`atime`/`mtime`) - never file contents.

## Set up your keep-list

```shell
solx config edit
```

Add a `[keep]` block (replace `sparky` with your ASURITE):

```toml
[keep]
include = ["/scratch/sparky/my-project", "/scratch/sparky/experiments/**"]
# Exclude flagged rows for regenerable trees.
exclude = ["**/.venv", "**/.git", "**/__pycache__", "**/node_modules"]
```

Patterns are gitignore-style; `**` matches any depth. A bare path means that
directory and everything under it. Includes and excludes filter the flagged
rows. Once a directory is kept, its entire tree is renewed, including subtrees
that match an exclude pattern. A flagged regular file is renewed individually.
Symlinks are skipped.

## Preview, then renew

```shell
# Preview which flagged paths would renew.
solx keep --dry-run -v

# Renew them (prompts once; -y skips the prompt for scripts).
solx keep

# Chase only the most-urgent bucket.
solx keep --stage pending
```

Piping or `--json` gives a machine-readable plan for an agent:

```shell
solx --json keep --dry-run | jq .
```

## Run a big pass off the login node

A renewal is metadata-heavy I/O - the load Sol's **login nodes throttle**. For
a large pass, run it on the Data Transfer Node or a compute node instead:

```shell
# From a login node - hand the heavy pass to the DTN (many cores, not throttled):
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; solx keep -j 24 -y'

# Already inside an allocation? Just run it directly:
solx keep
```

## Check the result

The summary reports touched files and directories, failures, and skipped paths.
Missing paths and unsupported types are skipped; permission errors still cause
exit code 1. Permission failures are grouped by owner, with a capped path sample
and an indication of whether every entry is an empty directory. If a directory
cannot be read, its emptiness is reported as unknown.

Ask the owner to renew inaccessible entries or grant your group write permission
with `chmod g+w`. `solx keep` only refreshes timestamps; it never deletes,
recreates, or changes ownership of an entry.

---

Full command reference: [solx.md](solx.md). Sol's deletion pipeline + CSV
schema: [`../skills/sol-skill/references/scratch.md`](../skills/sol-skill/references/scratch.md).
