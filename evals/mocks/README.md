# Mock Sol environment

A userland fake of just enough of Sol to exercise scripts and agent
decisions on a laptop. No privileges required.

## Source it

```shell
source evals/mocks/activate.sh
```

After sourcing, the following resolve to mock binaries that log every
invocation to `$MOCK_LOG` (default `/tmp/sol-skill-mock-$$.log`):

| Binary | What the mock does |
|---|---|
| `hostname` | Echoes `$MOCK_HOSTNAME` (default `sc001.sol.rc.asu.edu`). Honors `-s`, `-a`. |
| `module` | Implements `avail`/`load`/`list`/`unload`/`purge` against an in-memory state file. `module avail r` filters the canned list. |
| `srun` | Logs args, exits 0. Honors `--mock-fail=N` for synthetic failures. |
| `sbatch` | Logs args, copies `#SBATCH` headers from the script into the log, prints a fake `Submitted batch job <id>`. |
| `scancel` | Logs and exits 0. |
| `squeue` | Logs and prints one canned row owned by `$(whoami)`. |
| `ssh` | Logs the would-be invocation. **Never connects.** |

The fake `$HOME` (`evals/mocks/home/`) ships with:

- `.config/solx/config.toml` — sanitized config with a `[keep]` block using `sparky`
- `scratch-dirs-pending-removal.csv` — synthetic Sol warning
- `scratch-dirs-over-90days.csv` — synthetic Sol warning
- `scratch-dirs-inactive.csv` — synthetic Sol warning

The fake `/scratch` tree (`evals/mocks/scratch/sparky/`) has empty
`my-project/`, `experiments/`, and `old-stuff/` directories so
`solx keep` has somewhere to "touch" without affecting your real
filesystem.

## Toggle the side under test

```shell
# Pretend to be on a laptop instead of Sol
export MOCK_HOSTNAME=macbook.local
source evals/mocks/activate.sh
```

(Use `export` before sourcing — `MOCK_HOSTNAME=… source …` on a
single line does not reliably set the variable for `source`.)

## Toggle the solx-present branch

The mock deliberately ships **no** `solx` shim — that's the
not-yet-installed branch (the skill should detect the absence and prompt
to install `solx`). To test the `solx`-present branch, drop a shim into
`bin/`:

```shell
# Use the real solx if you have it installed
ln -s "$(command -v solx)" evals/mocks/bin/solx

# Or write a thin mock that records args
cat > evals/mocks/bin/solx <<'EOF'
#!/usr/bin/env bash
printf '%s solx %s\n' "$(date -u +%FT%TZ)" "$*" >> "$MOCK_LOG"
echo "[mock-solx] $*"
EOF
chmod +x evals/mocks/bin/solx
```

Remove the shim to flip back.

## Restore the real env

```shell
solskill_mock_deactivate
```

This restores `PATH` and `HOME` to their pre-`source` values. The
mock `$MOCK_LOG` file is left in place so you can inspect it after
deactivating.

## Extending

The mocks are deliberately small (~30 lines each) so they're easy to
read and extend. If you need a new binary, follow the pattern:

1. Create `evals/mocks/bin/<name>` with `#!/usr/bin/env bash`.
2. `chmod +x` it.
3. Log `$@` to `$MOCK_LOG` as the first line.
4. Print whatever canned output a real Sol-side invocation would
   produce, exit with the appropriate code.

Keep mocks dumb. The runner is responsible for orchestration; the
shims should never reach across to other shims or rely on global
state beyond `$MOCK_LOG` and `$MOCK_ROOT`.
