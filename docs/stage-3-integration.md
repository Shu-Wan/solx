# Stage 3 — Skill ↔ `solx` integration (deferred)

Sub-plan of [PLAN.md](PLAN.md). **Deferred until Stage 2 is mature
and the user has greenlit skill integration.** Until then, do not
touch any file under `skills/sol-skill/`.

## Why deferred

Stage 2 (`solx` CLI) is in active design and rewrite. Stitching the
agent skill to a CLI that's still in flux means doing the
integration work twice. Worse, premature integration risks teaching
the agent commands that get renamed or removed before Stage 2
stabilizes.

The skill currently teaches `sol_renew.py` for scratch renewal and
the manual SSH chain for sessions. That is **complete and works
end-to-end** on its own — there is no user-facing gap that demands
Stage 3 ship sooner.

## Greenlight criteria

Stage 3 work begins only after **all** of the following hold:

1. Stage 2 (`solx` CLI) has shipped and survived real Sol use — at
   least one full lifecycle smoke (`init` → `job start` → `jump` →
   `time` → `stop` → `keep`) verified live, and ideally a few days
   of dogfooding.
2. The CLI command surface is stable. No pending renames, no
   deferred verbs that need to slot in. If `solx skill *` is going
   to land, it lands here.
3. The user explicitly says "ok, do Stage 3."

## What Stage 3 will contain (when it returns)

This list is provisional. The exact contents get re-litigated when
the time comes, against the actual stable state of `solx`.

### Likely in scope

- **`skills/sol-skill/references/solx.md`** — new reference doc
  walking the agent through the `solx`-driven workflow: `solx init`,
  `solx job list/start/shell/time/stop`, `solx keep`. Includes a
  config example and the `--dry-run`-first ethos.
- **`skills/sol-skill/SKILL.md`** — add a `command -v solx`
  detection branch under "Sessions" / "Submitting Jobs" / "Scratch
  Renewal" sections so the agent prefers `solx` when present and
  falls back to manual / `sol_renew.py` when not. The manual branch
  stays — it never goes away.
- **`skills/sol-skill/scripts/sol_renew.py`** — likely **kept**, not
  removed. The skill's no-CLI flow stays viable as a fallback. May
  get a note pointing at `solx keep` for users who have the CLI.
- **`solx skill install/remove/list` subcommands** — the deferred
  CLI verbs that `solx config init` / `solx init` couldn't include
  in Stage 2. Likely a thin shell-out to `gh skill install` / Vercel
  `skills add`, supporting `claude` and `codex` agents. Implementation
  details (own path mapping vs delegate to existing CLI) decided at
  the time.
- **Root `README.md`** — possibly reframed to lead with both `solx`
  and the skill as paired tools. Or left skill-primary if `solx`
  hasn't earned top billing yet. Decided when Stage 3 starts.
- **`docs/coverage.md`** — new rows for `solx`-detection-branch
  behaviors, flipping from ⚪ roadmap to 🟡 documented or 🟢
  tested as evals catch up.
- **`CHANGELOG.md`** — a minor skill version bump describing the
  new branch.

### Likely out of scope (still)

- **Laptop-side `solx`** — `solx up/down/forward/info`,
  `~/.config/solx/laptop.toml`, ssh-chain construction. Still
  deferred for further design discussion separate from Stage 3.
- **Repo rename or PyPI publication** — release-engineering work is
  a different track.

## Success criteria (when Stage 3 ships)

Provisional, finalized at greenlight time:

1. With `solx` installed on Sol, the agent answers "start an
   interactive job for me" by running `solx job start <template>`
   (after `--dry-run` preview), not the manual `interactive` /
   `sbatch` chain.
2. With `solx` absent (uninstalled or fresh container), the agent
   re-detects on the next prompt and falls back to the manual flow
   from `sessions.md` and `slurm.md` cleanly.
3. With `solx` installed and a `[keep]` block configured, the agent
   answers "keep my scratch alive" by running `solx keep --dry-run`
   then `solx keep`. With `solx` absent, it falls back to
   `sol_renew.py` per the existing `references/scratch.md`.
4. No content from `solx/README.md` or `docs/solx.md` is duplicated
   into `references/solx.md`. The reference teaches the agent
   *workflow*; the docs teach the user *configuration*. Two sources
   of truth diverge.
5. `git diff` shows zero changes to the existing `sol_renew.py`
   behavior. Path remains supported.

## Until then

Treat Stage 3 as a closed door:

- Do not edit `skills/sol-skill/SKILL.md`.
- Do not add `references/solx.md`.
- Do not remove or modify `scripts/sol_renew.py`.
- Do not reframe root `README.md`.
- Do not bump skill version in `CHANGELOG.md`.

The skill ships v0.2.1 and that's the released line until Stage 3
work is greenlit and ships its own version bump.
