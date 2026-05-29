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
- **`run_l2_renew.py`** — a standalone, runnable L2 check for the
  renewal feature. It builds its own sandbox (real files with stale
  mtimes, including `.venv`/`__pycache__`), points a `.solkeep` and a
  warning CSV at it, runs `sol_renew.py`, and asserts the filesystem
  mutations: dry-run touches nothing, kept files (recursively) are
  refreshed, `.solkeep` carve-outs are left alone, non-kept dirs are
  skipped. Exits non-zero on any failure, so it works standalone, in
  CI, or as the L2 grader for the `scratch-renewal-*` evals
  (`check.l2_script`). Self-bootstraps via `uv`; needs no sandbox, no
  subagents, no `claude` CLI:

  ```shell
  evals/runner/run_l2_renew.py        # -v to echo the script's output
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
