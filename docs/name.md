# CLI Name Candidates

Working name: **`solx`** — tentative, may change before public release.

The CLI described in [PLAN.md](PLAN.md) needs a name that is short, easy to
type, unambiguous at the shell prompt, and not already taken on PyPI / GitHub /
Homebrew. Candidates are listed here so the decision is explicit and
revisitable; nothing below is final.

## Naming principles

### Must-haves

1. **Short.** The name *is* the command — typed many times a day. Target ≤ 6
   letters; 4 is the sweet spot.
2. **Unique.** No collision with popular CLIs, PyPI packages, Homebrew
   formulae, or widely-known GitHub tools. Check before committing — a rename
   late in Phase 2 is painful.

### Plus factors

- **ASU-tied.** Mascot (Sparky), logo (pitchfork), slogan ("Fork 'em up"),
  colors (maroon/gold).
- **Arizona-tied.** Geographic or cultural references (Sonoran, Tempe,
  saguaro, etc.).
- **Sol etymology.** Latin/Greek roots tied to the sun — the CLI is a
  *companion* to Sol, so companion-of-sun words fit well (e.g., *aura*,
  *lumen*, *solis*, *vesper*).
- **Subcommand-friendly.** Reads naturally with the subcommands PLAN.md
  introduces: `<name> up`, `<name> down`, `<name> session start`,
  `<name> doctor`, `<name> init`.
- **Pronounceable.** Says cleanly out loud when explaining the tool in a
  meeting or over chat.
- **Tab-completion friendly.** Short unique prefix relative to the user's
  other installed CLIs (so `<prefix><tab>` resolves in one hop).
- **No clash with existing Sol-cluster tooling.** `sol_renew`, `interactive`,
  `vscode` already live on Sol; don't pick a name that visually collides with
  what users already type at the cluster prompt.

## Candidates

### `solx` (current working name)

- **Read as**: "Sol extended / experimental."
- **For**: short, neutral, signals "not stable yet" while Phase 1 is ongoing.
- **Against**: no meaning outside this project; doesn't evoke ASU or Sol
  specifically.
- **Collisions**: none known in the HPC / developer-tooling space.

### Mogollon / `mog`

- **Read as**: "Mogollon" for the project name; `mog` for the CLI binary.
- **For**: Mogollon grounds the tool in Arizona's geography (the Mogollon
  Rim), giving it a distinctive and locally meaningful identity. This
  separation balances professionalism and practicality: "Mogollon" works well
  for documentation, repositories, and presentation, whereas `mog` is concise,
  memorable, and efficient for frequent command-line use.
- **Against**: `mog` has minor naming overlap in unrelated domains, though the
  risk is negligible in the intended HPC context, making the pairing expressive
  and pragmatic for a school project.
- **Collisions**: minor overlap outside HPC; no blocking collision known.

### `pitchfork` / `pfork`

- **Read as**: ASU logo (the pitchfork).
- **For**: strong ASU branding, memorable.
- **Against**: `pitchfork` is long to type; `pfork` reads like a process-fork
  utility and could confuse. `pitchfork` also collides with the Python
  `pitchfork` config library.
- **Collisions**: `pitchfork` exists on PyPI.

### `sparky`

- **Read as**: ASU mascot.
- **For**: instantly recognizable to ASU users, friendly.
- **Against**: collides with existing tools (`sparkyfish`, Apache Spark
  tooling, etc.); ambiguous at the prompt.
- **Collisions**: multiple on PyPI / GitHub.

### `forkup`

- **Read as**: "Fork 'em up" chant.
- **For**: fun, ASU-insider.
- **Against**: reads as git-fork tooling to outsiders; niche appeal.
- **Collisions**: none known.

## Current stance

Stay on `solx` through Phases 0–1. Revisit before Phase 2 (friendly beta),
once failure modes are understood and the tool's shape is stable enough that a
rename wouldn't thrash users.
