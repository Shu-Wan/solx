# evals/runner/

Wrapper around skill-creator's spawn / aggregate / view pipeline,
specialized for `sol-skill`. The runner itself (`__main__.py` /
`runner.py`) lands in iteration 1; this directory is reserved so
imports (`python -m evals.runner …`) line up with the structure
documented in [`../../DEVELOPMENT.md`](../../DEVELOPMENT.md).

## What's already here

- **`build_sandbox_home.sh`** — constructs a `CLAUDE_CONFIG_DIR`
  sandbox that mirrors the user's real `~/.claude/` but hides the
  user-scope `sol-skill` install. Run it once before each eval
  session. See `DEVELOPMENT.md` ("Baseline isolation") for the why
  and how.
- **`bench_solx_latency.sh`** — L3 latency benchmark (real Sol,
  read-only): times `solx job` commands against the equivalent raw
  SLURM call and reports the delta. Quantifies `solx`'s Python/NFS
  startup tax that informs the skill's "`solx` vs raw SLURM" rule and the
  startup-latency roadmap item. Usage: `evals/runner/bench_solx_latency.sh [N]`.
- **L2 renewal coverage lives in the `solx` package.**
  `solx/tests/test_keep.py::test_keep_end_to_end_real_touch` builds a
  real tree with stale mtimes (including `.venv`/`__pycache__`), runs
  `solx keep`, and asserts the filesystem mutations: kept files
  (recursively) are refreshed, carve-outs are left alone, non-kept dirs
  are skipped. It is the L2 grader for the `scratch-renewal-*` evals
  (`check.l2_script`). Run standalone or in CI:

  ```shell
  ( cd solx && uv run pytest tests/test_keep.py -q )
  ```

## What the runner will do (iteration 1)

- Read `evals/evals.json` (skill-creator schema + per-assertion
  `layer` and `check` extensions).
- For each eval:
  - Apply the `setup` block: write requested mock state
    (`MOCK_HOSTNAME`, `solx`-shim presence, fake CSVs, fake
    `.solkeep`).
  - Spawn the with-skill subagent (`--plugin-dir
    skills/sol-skill`) and the baseline subagent (no plugin-dir),
    both inheriting `CLAUDE_CONFIG_DIR` from the parent so neither
    sees the user-scope install.
  - Capture transcript, `$MOCK_LOG`, exit codes, files mutated under
    `evals/mocks/scratch/`.
- Grade per assertion using the `check` field — text patterns against
  transcript, exit codes against the L2 script run, mock-log greps,
  etc. Save `grading.json` per run dir matching skill-creator's
  schema.
- Hand off to `scripts.aggregate_benchmark` and the eval viewer
  unchanged.

## Invariants

- The runner must always be invoked from a `CLAUDE_CONFIG_DIR`
  sandboxed parent session. It refuses to start otherwise (so a
  forgotten sandbox doesn't silently produce a meaningless
  comparison).
- The runner never modifies `~/.claude/` or anything outside its
  workspace and `evals/mocks/scratch/`.
- Per-eval `setup` is reset between evals — no state leaks across
  runs.
