# ☀️ solx

[![CI](https://img.shields.io/github/actions/workflow/status/Shu-Wan/solx/ci.yml?branch=main&label=ci&logo=github)](https://github.com/Shu-Wan/solx/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Shu-Wan/solx?logo=github&color=blue)](https://github.com/Shu-Wan/solx/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](#installation)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Solx is a CLI for ASU's **Sol** supercomputer, designed for agent-assisted
work so you can stop babysitting Slurm.

- No memorizing SLURM commands.
- No surprise `/scratch` purges.
- Let your agent manage your SLURM jobs.

SSH to Sol, run `solx`, and keep the whole loop in your terminal.

## Installation

On Sol — `solx` provisions its own Python (≥ 3.10) via
[uv](https://docs.astral.sh/uv/):

```shell
curl -fsSL https://github.com/Shu-Wan/solx/releases/latest/download/install.sh | sh
```

Re-run that command to upgrade. Prefer a package manager?

```shell
uv tool install git+https://github.com/Shu-Wan/solx.git#subdirectory=solx
```

Both channels need `uv` on your `$PATH` — install it from
[astral.sh/uv](https://docs.astral.sh/uv/) first if you don't have it.

## Usage

```shell
solx init                 # one-time: write ~/.config/solx/config.toml
solx config edit          # define your job templates + [keep] paths
solx job start gpu        # request an interactive allocation (waits for the grant)
solx job jump             # open a shell on the compute node
solx keep                 # renew /scratch files Sol has flagged for deletion
```

What it's good at:

- **Interactive jobs from templates.** Define `[jobs.gpu]` once, then
  `solx job start gpu` allocates and waits and `solx job jump` drops you onto
  the node. Cancel with `solx job stop` (or raw `scancel`).
- **Keeping `/scratch` alive.** Sol purges inactive files on a schedule;
  `solx keep` renews only the directories you listed in `[keep]` that Sol has
  *actually flagged* — never a blanket `touch`.
  → walkthrough: **[docs/scratch.md](docs/scratch.md)**
- **Built for CLI agents.** Output auto-switches to JSON off a TTY, exit codes
  are meaningful, and destructive commands refuse rather than hang on a prompt.

**Learn more:** the full command manual is [docs/solx.md](docs/solx.md). Cached
reference notes on Sol conventions —
[the `solx` CLI](skills/sol-skill/references/solx.md),
[modules](skills/sol-skill/references/module.md),
[scratch policy](skills/sol-skill/references/scratch.md),
[Slurm jobs](skills/sol-skill/references/slurm.md),
[ssh tunnels](skills/sol-skill/references/sessions.md),
[file sharing](skills/sol-skill/references/sharing.md) — live with the skill.

## 🌵 The companion skill

[`skills/sol-skill/`](skills/sol-skill/SKILL.md) teaches an AI coding assistant
to operate on Sol the careful way. It turns natural requests like "start a GPU
session," "why is my job pending?", or "keep my scratch project alive" into the
right `solx` or raw Slurm command, while also handling environment detection,
partition choice, fairshare and wall-time awareness, modules, data movement, and
Sol-side services.

```shell
gh skill install Shu-Wan/solx sol-skill
```

Any [Agent Skills](https://agentskills.io/specification) installer works the
same way.

## Development

- **Changelog** — [CHANGELOG.md](CHANGELOG.md); current release **v0.4.0**.
- **Roadmap** — [docs/ROADMAP.md](docs/ROADMAP.md); next up is cutting `solx`'s
  startup latency.
- **Contributing, tests, and the eval harness** —
  [DEVELOPMENT.md](DEVELOPMENT.md) and
  [solx/DEVELOPMENT.md](solx/DEVELOPMENT.md), with the coverage matrix in
  [docs/coverage.md](docs/coverage.md).

## Disclaimer

A personal toolkit — **not affiliated with or endorsed by ASU Research
Computing.** The official documentation at <https://docs.rc.asu.edu/> is
authoritative on every Sol policy referenced here. `solx keep` changes file
*timestamps* under `/scratch` (it never reads, moves, or deletes content); the
job commands submit and cancel Slurm jobs. Preview with `--dry-run`, review
what an agent proposes before approving it, and verify against the official
docs. Provided as-is, with no warranty. MIT licensed — see [LICENSE](LICENSE).
