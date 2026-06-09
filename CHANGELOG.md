# Changelog

All notable changes to **solx** — the CLI and its agent skill — are
recorded here.

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

From v0.4.0 the CLI and the skill share **one version line**: each entry's
version matches `solx/src/solx/__init__.py`, the `version` field in
[`skills/sol-skill/SKILL.md`](skills/sol-skill/SKILL.md), and the git tag,
and a pushed `vX.Y.Z` tag builds and publishes the release.

## [Unreleased]

## [0.4.0] — 2026-06-08

`solx` becomes the supported path for interactive jobs and scratch
renewal, and the skill is rewritten to install and drive it. The CLI and
skill move to one version line, and a pushed `vX.Y.Z` tag now builds the
single-file `solx.pyz` and publishes a GitHub Release via CI.

### Added

- `solx config import-solkeep` — migrate a legacy `~/.solkeep` into the
  config `[keep]` block.
- CI: `.github/workflows/ci.yml` (lint + test on push/PR) and
  `release.yml` (on a `vX.Y.Z` tag: verify the tag matches `solx
  --version`, build `solx.pyz`, publish a GitHub Release with `solx.pyz`
  + `install.sh` attached). `curl … install.sh | sh` is the recommended
  install/upgrade on Sol.
- `skills/sol-skill/references/solx.md` — agent-facing `solx` reference.
- Situational job management (closes #9): SKILL.md + `references/slurm.md`
  teach checking `myfairshare` before submitting and backing off when
  it's low (≲0.05 — don't spam the scheduler and waste fairshare),
  tracking the wall-time left on the current allocation, and wrapping up
  / handing off before Slurm reclaims the node; plus a helpful-Sol-commands
  table.

### Changed

- SKILL.md rewritten around `solx`: detect it, install it on first use
  (required for job/scratch work), drive the `solx job` lifecycle and
  `solx keep`, and fall back to raw Slurm only when `solx` is unavailable.
- `references/scratch.md`: renewal via `solx keep` and the config
  `[keep]` block; keep-list pattern syntax retained.
- Repository renamed `Shu-Wan/sol-skills` → `Shu-Wan/solx`; all hardcoded
  install URLs updated. Version reconciled to one line (was: skill 0.3.0,
  solx 0.3.4).
- `solx keep` prints a deprecation notice when it falls back to
  `~/.solkeep`.
- `docs/coverage.md`, `DEVELOPMENT.md`, `docs/ROADMAP.md`: updated for the
  above.

### Deprecated

- `~/.solkeep` and the standalone renewal script are superseded by `solx
  keep` + the `[keep]` block. `solx keep` still reads a `~/.solkeep` if
  present; **support is removed in 0.5.0**. Migrate with `solx config
  import-solkeep`.

### Removed

- `skills/sol-skill/scripts/sol_renew.py` — the bundled renewal script;
  use `solx keep`.
- `evals/runner/run_l2_renew.py` — its target was the bundled script; the
  renewal mechanism is now covered by `solx/tests/test_keep.py`
  (including an end-to-end real-touch test).

### Earlier `solx` changes, folded into this release

These landed on `main` after the skill's v0.3.0 tag but were never
released on their own; they ship as part of v0.4.0.

#### solx 0.3.4 — zsh completions work installed on fpath (#26)

- `solx completions zsh` now ends with Click's dual-mode footer instead
  of Typer's bare `compdef`: installed on `fpath`
  (`solx completions zsh > ~/.zfunc/_solx`), the autoloaded script
  *calls* the completer, so the first Tab of a session completes instead
  of returning nothing. The eval/source install keeps working unchanged;
  if Typer's template ever stops matching, the script is emitted
  unmodified. bash/fish output untouched.
- Docs (`solx/README.md`, `docs/solx.md`): both zsh install modes, with
  the fpath two-liner as the recommended one.

#### solx 0.3.3 — cold-start latency, single-line help aliases, zipapp scripts

- **Cold-start latency on Sol's NFS home** (where every module import is
  a network round-trip): command implementations, `rich`, and `pathspec`
  now load inside command bodies, so `solx --version`, `--help`, and each
  tab-completion exec import ~150 modules (~780 metadata syscalls)
  instead of ~280 (~1,400).
- `--help` lists each command once: `jobs` is a hidden alias of `job`,
  noted inline on the `job` line.
- New `solx/scripts/build-pyz.sh` + `install.sh`: build and install
  `solx` as a single-file zipapp (`.pyz`) with precompiled bytecode —
  measured 1.6s cold / 0.12s warm vs 4.4s / 0.19s for the venv install
  on Sol. Publishing the artifacts to GitHub Releases is Stage 3
  (v0.4.0); see [`docs/ROADMAP.md`](docs/ROADMAP.md).

#### solx 0.3.2 — runtime tab completion + `jump --overlap` (#23)

#### solx 0.3.1 — completions under Typer's vendored Click; `version`/`help` aliases (#22)

#### solx 0.3.0 (sub-package)

The `solx/` CLI: agent-friendly output, verb-aware job-id resolution, and a
file-level-sharded `keep` that also reads the skill's `~/.solkeep`. The skill
(`skills/sol-skill/`) is untouched, and `solx` versions independently while
its changes accumulate here — the repo is skill-first until Stage 3 (v0.4.0),
when the two version lines reconcile.

- **Agent-friendly output** (issue #16 / [10 principles for agent-native
  CLIs](https://trevinsays.com/p/10-principles-for-agent-native-clis)): output
  auto-detects — Rich tables on a terminal, JSON when stdout is not a TTY; the
  global `--json` flag forces JSON anywhere. Results go to stdout, all
  diagnostics to stderr. Destructive commands (`job stop`, `keep`) and
  `init`-over-existing **refuse with exit 2** in a non-interactive session
  instead of hanging on a prompt; `-y`/`--yes` and `-f`/`--force` are
  interchangeable for skipping a prompt. `keep`'s JSON is **bounded** — counts +
  a ≤100-item sample + a `full_plan_path` temp-file spill. New
  `solx/src/solx/output.py` (`Out`).
- **Verb-aware job-id resolution**: `job time`/`job jump` auto-pick the most
  recent job (highest job id) when several match; `job stop` never guesses and
  exits 2 to disambiguate. Acting from inside an allocation warns about nesting
  (`jump`, `-q/--quiet` to silence) or self-cancel (`stop`). `slurm.py` returns
  a `Resolution`; adds `most_recent()`.
- **`solx keep`**: file-level sharding (closes #17) mirroring `sol_renew.py`
  PR #18 (streaming pipeline, `fd`/`rg`/`find` enumeration, bounded window);
  plus a keep-list resolved from `--solkeep <file>` > the `[keep]` config block
  > an auto-detected `~/.solkeep`, so the skill's existing `.solkeep` works
  under `solx keep` even with no `solx` config file.
- **`solx init`**: on a terminal, offers a short interactive walkthrough —
  confirm importing an existing `~/.solkeep` into `[keep]`, then pick the shell
  `solx job jump` opens. Skipped in a non-interactive session (which writes
  plain defaults; `solx keep` still reads `~/.solkeep` at runtime).
- **Hardening** (Copilot review): `completions` generated from Click so it
  works under `python -m solx`; `config edit` shlex-splits `$EDITOR`; an
  unreadable config surfaces a clean error; `keep` validates `--csv-dir`.
- New human manual [`docs/solx.md`](docs/solx.md) — the single source of truth
  for `solx` behavior; retired the pre-implementation contract
  `docs/stage-2-solx.md`. Root `README.md` now features `solx`; dropped the
  "Sol-first" phrasing. Test suite 103 → 161.

## [0.3.0] — 2026-05-28

File-level scratch renewal, faster enumeration, and skill guidance for
where to run it. Closes #17 (see #18).

### Changed

- `scripts/sol_renew.py`: shard the touch pass at the **file** level
  instead of per directory, as a streaming pipeline — enumerate a kept
  directory, then `touch` its files in evenly-sized batches across the
  worker pool, with a bounded in-flight window so peak memory stays a
  small multiple of `-j` regardless of total file count. A single huge
  directory now spreads across the whole pool, so `-j` scales the
  slowest single directory, not just the count of directories.
  Plan/dry-run output is unchanged; exit codes stay `0`/`1`/`2`, with
  `1` now reflecting an enumeration or touch-batch failure rather than
  a per-directory one.
- `scripts/sol_renew.py`: enumeration prefers `fd` (then `rg --files`)
  when on `PATH`, falling back to `find` — the multithreaded walk is
  faster on a large directory. Run with `--hidden --no-ignore` so the
  fast listers match `find -type f` exactly (they skip dotfiles /
  honor `.gitignore` by default, which would otherwise under-protect
  files).
- `skills/sol-skill/SKILL.md`: add a "Where to run it" decision rule —
  a renewal is metadata-heavy I/O that Sol login nodes throttle, so on
  a login node run the heavy pass on the DTN (`ssh soldtn`), a compute
  node, or a short `htc` job; match `-j` to the node's cores.
- `skills/sol-skill/SKILL.md`: rewrite the frontmatter `description`
  for triggering — cover the scratch-renewal flow (previously omitted)
  alongside the other domains, with an explicit near-miss guard
  (generic SLURM/HPC on Phoenix/NERSC, cloud GPUs, local-laptop
  tasks). Add a load-bearing safety rule: preview with `--dry-run` or
  confirm scope before the mutating run.
- `skills/sol-skill/SKILL.md`: the example `.solkeep` now carves out
  regenerable trees (`.venv`/`.git`/`__pycache__`/`node_modules`)
  under every kept path, with the reasoning — renewing them spends the
  pass on files that rebuild for free.
- `references/scratch.md`: document the streaming/file-level design,
  the `fd`/`rg`/`find` lister selection, the per-batch failure
  granularity, and the non-interactive `uv`-on-`PATH` gotcha for
  `ssh soldtn`.

### Added

- `evals/runner/run_l2_renew.py`: a runnable L2 eval that builds its
  own sandbox (real files + stale mtimes, incl. `.venv`/`__pycache__`)
  and asserts the renewal's filesystem mutations — dry-run touches
  nothing, kept files refresh recursively, `.solkeep` carve-outs are
  left alone, non-kept dirs are skipped. Closes the gap where the
  static mock CSVs (absolute `/scratch` paths) couldn't prove real
  touching.

### Deferred

- Mirror the file-level sharding into `solx keep` (`solx/src/solx/keep.py`)
  once the `solx` CLI lands on `main`. Tracked in #17.

## [0.2.1] — 2026-04-28

Partition rename: ASU Research Computing retired the `general`
partition in favor of `public`. All SBATCH examples,
`interactive` wrapper variants, and partition-choice guidance now
use `-p public` for non-`htc` workloads. Closes #12.

### Changed

- `skills/sol-skill/SKILL.md`, `references/slurm.md`,
  `references/sessions.md`: replace `-p general` with `-p public`
  in serial / MPI / job-array templates and interactive-shell
  examples; update the "save the larger partition for real
  workloads" guidance to name `public` instead of `general`.
- `evals/evals.example.json`: GPU PyTorch eval expects `-p public`.
- `docs/PLAN.md`: planned `solx` `[gpu]` profile uses
  `partition = "public"`.

## [0.2.0] — 2026-04-23

Substantive content release: situation-first SKILL refactor,
expanded behavior coverage (cluster status queries, OnDemand path,
DTN routing, SLURM-side environment detection), and the eval harness
that gates future releases.

### Skill content

- New "What this skill helps with" overview at the top of `SKILL.md`,
  with cross-cutting conventions (`whoami` substitution, never read
  `~/.ssh/config`, always preview destructive operations) called out
  explicitly.
- New "Disclaimer" section at the end of `SKILL.md` (personal
  toolkit, not affiliated with ASU Research Computing,
  <https://docs.rc.asu.edu/> is authoritative, use cautiously, no
  warranty).
- Situation-first organization throughout: each section opens with
  the user situation it addresses, not the technique it employs.
- New consolidated section **"Getting the Software You Need on
  Sol"** covering `module load`, Python via
  [`uv`](https://docs.astral.sh/uv/) (with `UV_CACHE_DIR` pointed at
  `/scratch`), R `tinytex` for LaTeX, and `~/.local`/`~/opt` for
  everything else. Replaces the previous separate Modules / Python /
  LaTeX sections.
- New section **"Using a Service That Runs on Sol, From Your
  Laptop"** covering Open OnDemand (lowest friction for casual
  GPU/Jupyter use) and the manual `ssh -L … -J …` chain for
  terminal-driven workflows. Replaces the previous "Sessions and
  Tunneling".
- New section **"Asking the Cluster About Yourself and Your Jobs"** —
  situational map from common status questions ("what jobs do I
  have?", "what's my fairshare?", "why is my job pending?", "what
  accounts can I use?") to the right SLURM command, with Sol
  wrappers (`myjobs`, `mysacct`, `myaccounts`, `myfairshare`,
  `summary`, `sq`, `showparts`, `showgpus`, `showlimited`, `seff`)
  named where they save real formatting work or encapsulate
  non-trivial calculation.
- "Detecting the Environment" rewritten around three SLURM-side
  signals: `command -v sacctmgr` empty → not on Sol;
  `sacctmgr -n show cluster format=cluster` returns the cluster
  name; `$SLURM_JOB_ID` distinguishes login node (unset) from
  compute node inside an allocation (set). Drops `hostname` parsing
  in favor of cleaner Slurm-only signals.
- "Submitting Jobs" teaches partition choice explicitly: route
  "lightweight / quick / debug" workloads to `htc`, save `general`
  for jobs that genuinely need larger nodes. Calls out that the
  `interactive` wrapper already defaults to `-p htc -q public -c 1
  -t 0-4`. Points at the prebuilt SBATCH templates under
  `/packages/public/sol-sbatch-templates/templates/` (serial, MPI in
  five MPI-stack variants, Python, R, MATLAB, rclone).
- "Transferring Data" routes large transfers through the Sol Data
  Transfer Node (`soldtn` / `dtn` shortcut) instead of the login
  node.
- `references/sessions.md`: ASCII tunnel diagram replaced with a
  Mermaid `flowchart LR`.

### Repo / contributor surface

- [`DEVELOPMENT.md`](DEVELOPMENT.md): the layered (L0–L3) eval
  harness, the mock Sol environment, the `CLAUDE_CONFIG_DIR` sandbox
  trick for fair baseline comparisons, and skill-design guidelines
  (situation first; load-bearing decisions in `SKILL.md`, detail in
  `references/`).
- [`docs/coverage.md`](docs/coverage.md): traffic-light coverage
  matrix grouped by SKILL.md section. Status of each behavior
  (🟢 tested / 🟡 documented / 🔴 gap / ⚪ roadmap) is updated
  manually before each release.
- `evals/`: harness scaffolding — sanitized example evals, mock Sol
  environment (PATH shims for `hostname`, `module`, `srun`, `sbatch`,
  `scancel`, `squeue`, `ssh`; fake `$HOME` with synthetic Sol warning
  CSVs), runner stub, and `build_sandbox_home.sh`.

### Verified

- 23 of 51 tracked behaviors are 🟢 tested for v0.2.0 (up from 0 in
  v0.1.0). Verification was hand-orchestrated across five eval
  iterations on a Sol login. See `docs/coverage.md` for the matrix
  and `DEVELOPMENT.md` for methodology.

### Removed

- `references/solx.md` (was a stub; deferred along with the rest of
  the `solx` surface).
- `docs/stage-1-skill.md` — Stage 1 deliverables shipped; the
  `solx`-conditional items were superseded by the no-solx design.

### Notes

- `solx`, an optional one-command laptop CLI, is on the roadmap (see
  [`docs/PLAN.md`](docs/PLAN.md)) but is **not** part of this
  release. The skill works end-to-end without it.

## [0.1.0] — 2026-04-19

Initial public release. Restructured the repo to follow the
agentskills.io-compatible layout (skill content under
`skills/sol-skill/`), added the `sol_renew.py` script for
CSV-driven `/scratch` renewal, and shipped the original references
(`module.md`, `scratch.md`, `sharing.md`, `slurm.md`).

[Unreleased]: https://github.com/Shu-Wan/solx/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Shu-Wan/solx/releases/tag/v0.4.0
[0.3.0]: https://github.com/Shu-Wan/solx/releases/tag/v0.3.0
[0.2.1]: https://github.com/Shu-Wan/solx/releases/tag/v0.2.1
[0.2.0]: https://github.com/Shu-Wan/solx/releases/tag/v0.2.0
[0.1.0]: https://github.com/Shu-Wan/solx/releases/tag/v0.1.0
