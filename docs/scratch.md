# Keeping `/scratch` files alive with `solx keep`

Sol deletes inactive `/scratch` files on a layered schedule and warns you first,
dropping per-stage CSV files in your home directory. `solx keep` renews the
directories you still need — and *only* those — so you don't lose work to a
purge, and don't abuse a shared filesystem by blanket-touching everything.

## The idea

`solx keep` renews a directory only when it is **both**:

1. **flagged by Sol** — listed in one of the warning CSVs Sol writes to `$HOME`
   (`scratch-dirs-pending-removal.csv`, `…-over-90days.csv`, `…-inactive.csv`),
   **and**
2. **matched by your keep-list** — the `[keep]` block in your config.

So there is nothing to do until Sol actually flags something, and a stray
keep-list can't keep arbitrary files alive forever. It only ever touches
timestamps (`atime`/`mtime`) — never file contents.

## 1. Set up your keep-list

```shell
solx config edit
```

Add a `[keep]` block (replace `sparky` with your ASURITE):

```toml
[keep]
include = ["/scratch/sparky/my-project", "/scratch/sparky/experiments/**"]
# Don't spend the pass on regenerable trees — they rebuild for free.
exclude = ["**/.venv", "**/.git", "**/__pycache__", "**/node_modules"]
```

Patterns are gitignore-style; `**` matches any depth. A bare path means that
directory and everything under it.

## 2. Preview, then renew

```shell
# Always preview first — shows exactly which flagged directories would renew.
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

## 3. Run a big pass off the login node

A renewal is metadata-heavy I/O — the load Sol's **login nodes throttle**. For
a large pass, run it on the Data Transfer Node or a compute node instead:

```shell
# From a login node — hand the heavy pass to the DTN (many cores, not throttled):
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; solx keep -j 24 -y'

# Already inside an allocation? Just run it directly:
solx keep
```

## Migrating an old `~/.solkeep`

If you used the older `sol_renew.py` script you have a `~/.solkeep` keep-list.
`solx keep` still reads it (with a deprecation notice; support ends in a future
release), so migrate it into your config once:

```shell
solx config import-solkeep      # folds ~/.solkeep into [keep]
solx config show                # review the result
```

If your keep-list re-includes a path *under* an earlier `!` carve-out, the
`[keep]` form (include minus exclude) can't reproduce that ordering — the
command tells you and asks you to confirm with `-f`. Compare
`solx keep --dry-run` before and after to be sure.

---

Full command reference: [solx.md](solx.md). CSV schema and performance notes:
[`../skills/sol-skill/references/scratch.md`](../skills/sol-skill/references/scratch.md).
