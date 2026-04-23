# Changelog

All notable changes to `sol-skill` are recorded here.

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
and the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

The version in each entry below matches the `version` field in
[`skills/sol-skill/SKILL.md`](skills/sol-skill/SKILL.md) and the git
tag for that release.

## [Unreleased]

### Added

- "Detecting the Environment" rewritten around three SLURM-side
  signals: `command -v sacctmgr` empty → not on Sol;
  `sacctmgr -n show cluster format=cluster` returns the cluster
  name; `$SLURM_JOB_ID` distinguishes login node (unset) from
  compute node inside an allocation (set). Drops `hostname` parsing
  in favor of cleaner Slurm-only signals.
- "Submitting Jobs" calls out that the `interactive` wrapper already
  defaults to `-p htc -q public -c 1 -t 0-4` (so bare `interactive`
  is the right thing for most debug shells), and points at the
  prebuilt SBATCH templates under
  `/packages/public/sol-sbatch-templates/templates/` (serial, MPI in
  five MPI-stack variants, Python, R, MATLAB, rclone).
- New section **"Asking the Cluster About Yourself and Your Jobs"** —
  situational map from common status questions ("what jobs do I
  have?", "what's my fairshare?", "why is my job pending?", "what
  accounts can I use?") to the right SLURM command, with Sol
  wrappers (`myjobs`, `mysacct`, `myaccounts`, `myfairshare`,
  `summary`, `sq`, `showparts`, `showgpus`, `showlimited`, `seff`)
  named where they save real formatting work or encapsulate
  non-trivial calculation.
- "Transferring Data" now routes large transfers through the Sol
  Data Transfer Node (`soldtn` / `dtn` shortcut) instead of the
  login node.

### Verified

- Iteration 5 of the eval harness promoted 14 rows in
  `docs/coverage.md` from 🟡 documented to 🟢 tested: all three new
  Detection signals (`command -v sacctmgr` / `sacctmgr show cluster`
  / `$SLURM_JOB_ID`), all 9 rows in the new "Asking the Cluster"
  section, SBATCH header generation, and DTN routing for large
  transfers. Total 🟢 tested behaviors: 9 → 23.

### Removed

- `docs/stage-1-skill.md` — Stage 1 deliverables (manual SSH
  flow, sessions reference, `whoami` substitution discipline) are
  shipped; the `solx`-conditional items in the original sub-plan
  were superseded by the no-solx design and won't be implemented
  in this version.

## [0.1.0] — 2026-04-21

Initial public release.

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
- SLURM section now teaches partition choice explicitly: route
  "lightweight / quick / debug" workloads to the `htc` partition;
  save `general` for jobs that genuinely need larger nodes.
- `references/sessions.md`: ASCII tunnel diagram replaced with a
  Mermaid `flowchart LR`.

### CLI

- `scripts/sol_renew.py`: per-stage scratch renewal driven by Sol's
  warning CSVs and a user-maintained `.solkeep` (gitignore-style
  syntax). PEP 723 inline-metadata shebang — self-bootstraps via
  `uv`. `--dry-run`, `--stage`, `-j` parallelism, exit codes
  documented.

### Repo / contributor surface

- [`DEVELOPMENT.md`](DEVELOPMENT.md): the layered (L0–L3) eval
  harness, the mock Sol environment, the `CLAUDE_CONFIG_DIR` sandbox
  trick for fair baseline comparisons, and skill-design guidelines
  (situation first; load-bearing decisions in `SKILL.md`, detail in
  `references/`).
- [`docs/coverage.md`](docs/coverage.md): public list of tested
  behaviors, documented-but-not-yet-auto-tested behaviors, and known
  gaps.
- `evals/`: harness scaffolding — sanitized example evals, mock Sol
  environment (PATH shims for `hostname`, `module`, `srun`, `sbatch`,
  `scancel`, `squeue`, `ssh`; fake `$HOME` with synthetic Sol warning
  CSVs), runner stub, and `build_sandbox_home.sh`.

### Notes for this release

- `solx`, an optional one-command laptop CLI, is on the roadmap (see
  [`docs/PLAN.md`](docs/PLAN.md)) but is **not** part of this
  release. The skill works end-to-end without it.
- Verification for v0.1.0 was hand-orchestrated across four eval
  iterations against three discriminating prompts. See
  [`docs/coverage.md`](docs/coverage.md) for what's covered and what
  isn't.

[Unreleased]: https://github.com/Shu-Wan/sol-skills/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Shu-Wan/sol-skills/releases/tag/v0.1.0
