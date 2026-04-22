# Stage 3 — Skill ↔ `solx` integration

Sub-plan of [PLAN.md](PLAN.md). **Depends on Stage 2** (`solx` must be installable and stable). Stage 1 must also be in place (the manual fallback that this stage layers on top of).

## Scope

Now that `solx` is usable, fill in the skill's `solx`-aware path and reframe the root README around the CLI. The Stage 1 manual fallback stays as the second branch — it never goes away (it's the "no `solx`" arm of the detection check).

In scope: `skills/sol-skill/references/solx.md`, `skills/sol-skill/SKILL.md`, root `README.md`. Out of scope: any code change to `solx/`.

## Deliverables

| Path | Action | Notes |
| --- | --- | --- |
| `skills/sol-skill/references/solx.md` | REPLACE Stage 1 stub | Full `solx`-driven workflow doc: config profile examples (incl. `[shared]` for `--mail-type=TIME_LIMIT_90,END,FAIL`), common subcommand recipes (`up`, `forward`, `down`, `info`), troubleshooting (Duo prompts, ControlMaster, port collisions). |
| `skills/sol-skill/SKILL.md` | MODIFY | Tighten the detection branch wording to recommend `solx up` when present. Manual branch unchanged. |
| `README.md` (root) | MODIFY | Reframe to lead with `solx` as the primary product; demote agent skill to a "companion" section; updated layout tree showing `solx/`, `docs/`, `skills/`. |

## Implementation notes

- **Don't duplicate `solx` reference material in the skill.** Link to `solx/README.md` for installation and config schema; teach the agent the *workflow* in `references/solx.md`. Two sources of truth diverge.
- **Detection branch must stay.** Even with `solx` "available," the skill should still run `command -v solx` per session — the user could have uninstalled it, or be in a fresh container, or be on a machine where it never made it onto PATH.
- **Dry-run-first ethos.** `references/solx.md` should instruct the agent to `solx up <profile> --dry-run` first when the user hasn't explicitly approved the action, then run for real after the user confirms.
- **Username handling carries over.** Same `whoami` substitution rule as Stage 1; `solx` itself reads `$USER`/`whoami` so the doc rarely needs to substitute, but examples should still use `swan16` not `<asurite>`.

## Success criteria

1. With `solx` installed, the agent answers "start a jupyter session on Sol from my laptop" by running `solx up default` (after a `--dry-run` preview), not by walking the manual chain.
2. With `solx` removed mid-conversation (uninstalled), the agent re-detects on the next prompt and falls back to `sessions.md` cleanly — no stale assumption that `solx` is still around.
3. Root `README.md` opens with a `solx` value-prop and install command before mentioning the skill. Layout tree includes `docs/`, `solx/`, `skills/`.
4. `references/solx.md` shows at least one `[shared]`-using config example (e.g., common `--mail-type=TIME_LIMIT_90,END,FAIL` across `default`/`gpu`/`debug`) so the agent learns to recommend `[shared]` when the user repeats themselves.
5. No content from `solx/README.md` is duplicated in `references/solx.md`; both link to each other instead.

## Testing checklist

1. **`solx`-aware path**: install `solx`; ask the agent for a jupyter session. Expect `solx up` invocation, with a `--dry-run` shown first and the user prompted to confirm before the real run.
2. **Re-detection on absence**: uninstall `solx`; ask again in a fresh session. Agent should detect absence and use the manual flow from `sessions.md`. No leftover assumption from earlier sessions.
3. **README framing**: visual review — first heading after the title should reference `solx`, not the skill. Install instructions for `uv tool install` appear before the skill section.
4. **No duplication**: `diff <(grep -A2 '^##' solx/README.md) <(grep -A2 '^##' skills/sol-skill/references/solx.md)` should show non-overlapping section topics (install/config in one, workflow recipes in the other).
5. **`[shared]` example surfaces**: `grep -E 'mail-type=TIME_LIMIT_90|\[shared\]' skills/sol-skill/references/solx.md` returns at least one hit.
