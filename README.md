# solx

A command-line tool for daily work on ASU's **Sol** supercomputer, with an
agent skill that teaches an AI coding assistant to use it.

- **`solx`** — the CLI. List jobs, request an interactive allocation, open
  a shell on the compute node, cancel, check remaining time, and renew
  `/scratch` files Sol has flagged for deletion. You SSH to Sol yourself,
  then run `solx` there — no laptop-side magic, no `~/.ssh/*` reads.
- **the agent skill** (`skills/sol-skill/`) — teaches an assistant to
  operate on Sol the careful way: detect the environment, install and use
  `solx`, choose partitions, stay fairshare- and time-aware, manage
  modules and data, and reach Sol-side services from a laptop.

The official doc for every Sol policy and convention referenced here is
the ASU Research Computing site: <https://docs.rc.asu.edu/>.

## `solx` — the CLI

`solx` provisions its own Python via [`uv`](https://docs.astral.sh/uv/)
(Sol's system `python3` is too old). On Sol:

```shell
# Recommended: single-file install — fast cold start on the NFS home.
curl -fsSL https://github.com/Shu-Wan/solx/releases/latest/download/install.sh | sh

# Alternative: as a uv tool (isolated venv, on $PATH automatically).
uv tool install git+https://github.com/Shu-Wan/solx.git#subdirectory=solx

solx --version
solx init                 # write ~/.config/solx/config.toml
solx config edit          # set up your job templates and [keep] paths
solx job start debug      # request an interactive allocation
solx job jump             # open a shell on the compute node
solx keep --dry-run       # preview scratch renewal
```

The `install.sh` channel is also the upgrade path — re-running it fetches
the latest release. Both channels need `uv` on your `$PATH`; install it
from [astral.sh/uv](https://docs.astral.sh/uv/) first if missing.

- Full manual: [`docs/solx.md`](docs/solx.md)
- Install + command reference: [`solx/README.md`](solx/README.md)

### Scratch renewal, briefly

`solx keep` is the supported way to keep `/scratch` files alive. Sol
deletes inactive files on a layered schedule and drops warning CSVs in
your `$HOME`; `solx keep` reads those, intersects them with the `[keep]`
block in your config, and `touch`es **only** the directories that are
both flagged by Sol and in your keep-list. It never walks `/scratch`
wholesale.

That bound is deliberate. `solx keep` is for **extending the life of
files you still actively need** — source trees, paper drafts, in-progress
datasets — not for defeating Sol's retention policy. Scratch is a shared,
finite resource. Don't keep-list directories you no longer work on (move
them off `/scratch` or let them age out), and don't schedule renewals on
an aggressive cron. Use `[keep]` to describe what matters, not what you
happen to have. If you're unsure whether a file should stay, contact ASU
Research Computing.

> The older standalone `sol_renew.py` script and the `~/.solkeep`
> keep-list are **deprecated**. `solx keep` still reads a `~/.solkeep` if
> present (with a deprecation notice), but support is removed in solx
> 0.5.0 — migrate with `solx config import-solkeep`.

## Agent skill

[`skills/sol-skill/SKILL.md`](skills/sol-skill/SKILL.md) is the entry
point an AI coding assistant reads when this directory is installed as a
skill. It tells the assistant how to detect whether it's on Sol, install
and drive `solx`, manage Environment Modules, where to keep data, how to
submit Slurm jobs, how to stay fairshare- and time-aware, and how to
reach a Sol-side service from a laptop.

### Install

```shell
gh skill install Shu-Wan/solx sol-skill
```

Any installer following the
[Agent Skills specification](https://agentskills.io/specification) works
the same way (e.g. `npx skills add Shu-Wan/solx -g`).

## Who is this for

- **Sol users** comfortable on the command line who want fewer
  repetitive Slurm steps and a safer, auditable way to keep `/scratch`
  files alive.
- Users who let an AI assistant help with cluster work and want it to
  follow Sol conventions consistently.

If you do not run code on Sol, this repo will not be useful to you.

## Assumptions and risks

Read these before installing.

- **`uv`-first.** `solx` (and the renewal mechanism) need a modern Python.
  System `python3` on HPC systems is frequently too old; this repo uses
  `uv` to provision its own interpreter, so you don't manage a virtualenv
  — but you do need `uv` on your `$PATH`.
- **`solx keep` changes file timestamps** (`atime` + `mtime`) on files
  under `/scratch`. It never deletes, moves, or reads file contents, and
  only walks directories that **both** appear in Sol's warning CSVs and
  match your `[keep]`. Always run `--dry-run` once to verify the plan.
- **Sol's deletion policy is set by ASU Research Computing, not this
  tool.** Thresholds, CSV filenames, and cadence are documented at
  <https://docs.rc.asu.edu/scratch>. If upstream changes them, this tool
  follows — upstream docs are authoritative.
- **HPC shared filesystems can be slow.** A renewal walks each flagged
  directory and touches every file. On millions of small files this takes
  time. See "Performance notes" in
  [`skills/sol-skill/references/scratch.md`](skills/sol-skill/references/scratch.md)
  before scaling parallelism up.
- **No warranty.** A personal toolkit, published in case others find it
  useful. Review the code before running it on data you care about.

## Layout

```text
solx/                            # the repo
├── README.md                    # You are here
├── DEVELOPMENT.md               # Contributor guide + eval harness internals
├── .github/workflows/           # ci.yml (lint+test) · release.yml (.pyz + GH release on tag)
├── docs/
│   ├── ROADMAP.md               # roadmap
│   ├── solx.md                  # solx user manual
│   └── coverage.md              # public test methodology + coverage matrix
├── solx/                        # solx CLI — installable Python package
│   ├── README.md                # install + command reference
│   ├── DEVELOPMENT.md           # architecture + tests
│   ├── scripts/                 # build-pyz.sh, install.sh
│   └── src/solx/                # the package
├── skills/sol-skill/            # agent skill (agentskills.io layout)
│   ├── SKILL.md                 # skill entry point
│   └── references/              # solx, module, scratch, sharing, slurm, sessions
└── evals/                       # eval harness (not shipped with the skill)
    ├── mocks/                   # userland Sol mock environment
    └── runner/                  # wrapper over skill-creator (in development)
```

## Verification and contributing

- **Version history** — see [`CHANGELOG.md`](CHANGELOG.md). Current
  release: **v0.4.0**.
- **What's tested and how** — see [`docs/coverage.md`](docs/coverage.md)
  for the public methodology and per-area coverage matrix.
- **Contributing / eval harness internals** — see
  [`DEVELOPMENT.md`](DEVELOPMENT.md) for the layered (L0–L3) eval
  framework, the mock Sol environment, the release process, and
  [`solx/DEVELOPMENT.md`](solx/DEVELOPMENT.md) for the CLI's architecture.

## References

Cleaned-up notes on the ASU Research Computing docs, for quick lookup:

- [solx.md](skills/sol-skill/references/solx.md) — the `solx` CLI workflow
- [module.md](skills/sol-skill/references/module.md) — loading/unloading software modules
- [scratch.md](skills/sol-skill/references/scratch.md) — scratch deletion pipeline, keep-list syntax, `solx keep` internals
- [slurm.md](skills/sol-skill/references/slurm.md) — submitting/managing Slurm jobs, situational awareness, helpful commands
- [sessions.md](skills/sol-skill/references/sessions.md) — manual ssh tunnels to Sol-side services
- [sharing.md](skills/sol-skill/references/sharing.md) — sharing files between users

Official doc (authoritative): <https://docs.rc.asu.edu/>.

## License

MIT. See [`LICENSE`](LICENSE).
