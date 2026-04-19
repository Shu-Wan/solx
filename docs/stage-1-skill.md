# Stage 1 — Skill manual-SSH path (situational-aware)

Sub-plan of [PLAN.md](PLAN.md). This stage runs **in parallel** with Stage 2; it has no dependency on `solx` existing.

## Scope

Update the agent skill so an LLM (or a human reading the docs) can drive the laptop ↔ Sol dev loop using only manual `ssh -L … -J …` chains — no `solx` required. After this stage, the workflow is documented and usable from day one; `solx` becomes purely additive in Stage 3.

Out of scope: any code in `solx/`, any change to `README.md` framing (that's Stage 3).

## Deliverables

| Path | Action | Notes |
| --- | --- | --- |
| `skills/sol-skill/references/sessions.md` | NEW | Manual SSH flow: chained `-L`/`-J`, ProxyJump, OAuth callback (`-R`), port-already-in-use diagnosis. All examples use `$(whoami)` substitution. |
| `skills/sol-skill/references/solx.md` | NEW (stub) | One paragraph pointing at Stage 3. Exists so links from `SKILL.md` don't 404 when `solx` is detected before Stage 3 ships. |
| `skills/sol-skill/SKILL.md` | MODIFY | Add "Sessions and Tunneling" section with the `command -v solx` detection branch. |

## Implementation notes

- **Detection check**: `command -v solx` (not `which`, not a feature-test). Fast, side-agnostic, exits non-zero when absent.
- **Username substitution**: instruct the agent to run `whoami` once at the start of the session and substitute throughout — not per-command. Avoids prompt fatigue and keeps copy-paste idiomatic.
- **`sessions.md` style**: copy-paste-ready commands, not prose. The agent shouldn't have to construct anything from English.
- **Working examples to consolidate** (pulled forward from past conversations):
  - Chained tunnel via login node: `ssh -L 8888:localhost:8888 -J swan16@sol.asu.edu swan16@<compute-node>`.
  - OAuth callback via reverse tunnel (`-R`) for laptop-side OAuth flows.
  - ControlMaster check/exit (`ssh -O check`, `ssh -O exit`).
  - "Port already in use" diagnosis (`lsof -i :8888`, `ss -tlnp`).
- **No reading of `~/.ssh/*`**: even the docs should not instruct the agent to peek at the user's ssh config. If the user has a custom `Host` alias, they can use it directly in the manual commands.

## Success criteria

1. With `solx` not on PATH, the agent answers "start a jupyter session on Sol from my laptop" by running `command -v solx`, finding nothing, and walking through `sessions.md`'s manual chain — without prompting the user for a username (substituted from `whoami`).
2. A human user can follow `sessions.md` from a clean shell and reach a Jupyter on a compute node without consulting other docs.
3. `references/solx.md` exists as a stub; clicking through from `SKILL.md` doesn't 404.
4. `grep -RE '<asurite>|<username>|<user>@' skills/sol-skill/references/` returns nothing — no placeholder leakage.
5. The `SKILL.md` "Sessions and Tunneling" section is < 30 lines and just routes to one of the two reference docs based on `command -v solx`.

## Testing checklist

1. **Detection branch with `solx` absent**: ensure `solx` is not on PATH; ask the agent "start a jupyter session on Sol from my laptop". Expect:
   - Agent runs `command -v solx`, sees nothing.
   - Agent walks through `references/sessions.md`'s manual flow.
   - All examples use `swan16` (substituted from `whoami`), no placeholders surface to the user.
2. **Manual flow works end-to-end by hand**: follow `references/sessions.md` from a clean shell. Confirm the chained `ssh -L … -J …` reaches a jupyter on a compute node.
3. **Stub exists**: `cat skills/sol-skill/references/solx.md` returns a non-empty placeholder pointing at Stage 3.
4. **No placeholder leakage**: `grep -RE '<asurite>|<username>|<user>@' skills/sol-skill/references/` is empty.
5. **Detection branch with `solx` present** (re-run after Stage 2 ships): install `solx`, rerun the same prompt. Agent should pivot to `references/solx.md`. Manual fallback remains documented and reachable from `SKILL.md`.
