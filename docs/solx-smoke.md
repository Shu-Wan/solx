# `solx` manual smoke checklist (Sol side)

The unit tests cover everything except the actual cluster round-trip.
This checklist exercises the real thing on the cheapest queue (`htc`,
the `debug` profile) so a full lifecycle takes under two minutes.

Run on Sol after SSHing in. Fresh shell — no leftover `solx` state.

## 0. Pre-flight

```shell
ssh swan16@sol.asu.edu
hostname -a              # confirm something.sol.rc.asu.edu
uv --version             # confirm uv is on $PATH
```

If `uv` is missing, install it from
<https://docs.astral.sh/uv/>.

## 1. Install

```shell
uv tool install --force git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx
solx --version
```

Expected: prints the package version (e.g. `0.1.0`). If `solx` is not
on `$PATH`, run `uv tool update-shell` and reopen the shell.

## 2. Side detection

```shell
solx where
```

Expected: `sol mode (<short-hostname>)`. Anything else → fail.

## 3. Config init + show

```shell
solx config init
solx config show
```

Expected:
- `solx config init` writes `~/.config/solx/profiles.toml` and prints
  the path. Re-running without `--force` exits 2 with an "already
  exists" message.
- `solx config show` prints three Rich tables (`default`, `gpu`,
  `debug`). The `qos` row in each table reads `'public'`
  (inherited from `[shared]`); `srun_args` includes the
  `[shared]` mail-type entry plus any per-profile entries.

## 4. Dry-run before live run

```shell
solx session start debug --dry-run
```

Expected: prints

```
Profile: debug (kind=bare)
Command: sbatch --parsable --job-name=solx-debug --partition=htc --qos=public --time=0-1 "--mail-type=TIME_LIMIT_90,END,FAIL" "--wrap=sleep infinity"
(dry-run — not submitting)
```

(Field order matters; the test suite snapshots this format.) Confirms
the `[shared]` mail-type made it through the merge. Exit 0; nothing
queued.

## 5. Live lifecycle on `htc`

```shell
solx session start debug
solx session info --json
squeue -u "$(whoami)"
solx session stop
squeue -u "$(whoami)"
```

Expected:
- `session start` prints `Submitted job <ID>; waiting for
  allocation...`, then state transitions (e.g. `PENDING`,
  `CONFIGURING`), then `Session ready on <node> (job <ID>)`.
- `session info --json` returns valid JSON with `job_id`, `node`,
  `profile = "debug"`, `kind = "bare"`, `ports = [8000, 8888]`.
- `squeue` shows the job RUNNING.
- `session stop` cancels the job and clears `~/.local/share/solx/session.json`.
- Final `squeue` is empty (or shows no `solx-debug` job).

If allocation doesn't reach RUNNING within 10 minutes, the polling
loop times out with a clear error. Run `scancel <job_id>` if needed
and try again — the partition may be backed up.

## 6. Stale-session handling

```shell
solx session start debug                   # let it land
JOB=$(jq -r .job_id ~/.local/share/solx/session.json)
scancel "$JOB"                              # kill the job out-of-band, leaving session.json
solx session start debug                    # should detect the orphan
solx session stop
```

Expected: the second `start` prints `Found stale session.json (job
<ID> no longer queued); clearing it.` and proceeds to submit a new
allocation.

## 7. Refusal when a session is already alive

```shell
solx session start debug                   # land the first session
solx session start debug                   # second invocation refuses
solx session stop
```

Expected: the second `start` exits 2 with `A session is already
running: job <ID> on <node> (profile debug). Run solx session info
or solx session stop first.` First session.json is preserved.

## 8. Off-Sol guard (run on a laptop, not Sol)

```shell
solx where                                 # not-sol mode
solx session info                          # exit 2 with "ssh to Sol first"
solx config show                           # exit 2, same message
solx up                                    # exit 2 with deferral message
```

Expected: `where` prints `not-sol mode`; everything else exits 2
without trying to do real work.

## Cleanup

```shell
uv tool uninstall solx                     # if you don't want to keep it installed
rm -rf ~/.config/solx ~/.local/share/solx  # if you want a totally clean slate
```

---

## What this checklist deliberately doesn't cover

- The `gpu` profile — it would need a real `gpu:a100:1` allocation,
  adding queue-wait flake without exercising any code path that the
  `debug` profile doesn't already exercise.
- `kind = "vscode"` — not implemented in this release; the starter
  config uses `kind = "bare"` for the `default` profile.
- Laptop-side `solx up`/`down`/`forward`/`info` — those are stubs
  that exit 2 (covered in step 8).
- `srun --jobid=<id> --pty bash` to step into the allocation — works
  but is upstream Slurm, not `solx`.
