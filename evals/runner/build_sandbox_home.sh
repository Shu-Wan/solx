#!/usr/bin/env bash
# build_sandbox_home.sh — construct a CLAUDE_CONFIG_DIR sandbox that
# mirrors the user's real ~/.claude with controlled perturbations:
# hide a named skill, drop CLAUDE.md, inject an alternate skill tree.
#
# Why: the eval harness compares "with sol-skill" vs "baseline (no
# sol-skill)" subagents. If sol-skill is installed at user scope,
# baseline subagents see it too, making the comparison meaningless.
# CLAUDE.md can also prime the baseline with Sol-related directives
# ("You are on ASU Sol Supercomputer..."), contaminating the delta.
# This script produces a mirror config dir with those perturbations
# applied so the only intentional difference between runs is the
# skill being tested.
#
# Usage:
#   # Baseline sandbox (no sol-skill, no CLAUDE.md)
#   BASELINE=$(./evals/runner/build_sandbox_home.sh \
#       --no-claude-md /tmp/sandbox-baseline)
#
#   # With-skill sandbox (dev-tree sol-skill re-injected, no CLAUDE.md)
#   WITH_SKILL=$(./evals/runner/build_sandbox_home.sh \
#       --no-claude-md \
#       --add-skill skills/sol-skill \
#       /tmp/sandbox-with-skill)
#
#   CLAUDE_CONFIG_DIR=$BASELINE claude -p "..."
#
# Options:
#   --hide-skill NAME     Skill to exclude (default: sol-skill)
#   --add-skill PATH      Symlink this skill dir into sandbox/skills/
#                         (takes precedence over --hide-skill for the
#                         same name; use this to inject a dev tree)
#   --no-claude-md        Skip the CLAUDE.md symlink
#   TARGET_DIR            Sandbox path (default: /tmp/sol-skill-eval-claude)

set -euo pipefail

HIDE_SKILL="sol-skill"
ADD_SKILL=""
NO_CLAUDE_MD=0
TARGET=""
SOURCE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

while [ $# -gt 0 ]; do
    case "$1" in
        --hide-skill)    HIDE_SKILL="$2"; shift 2 ;;
        --add-skill)     ADD_SKILL="$(cd "$2" && pwd)"; shift 2 ;;
        --no-claude-md)  NO_CLAUDE_MD=1; shift ;;
        -h|--help)
            grep -E '^# ' "$0" | sed 's/^# \?//' ; exit 0 ;;
        --*)
            echo "build_sandbox_home: unknown flag $1" >&2; exit 2 ;;
        *)
            if [ -n "$TARGET" ]; then
                echo "build_sandbox_home: extra positional arg $1" >&2; exit 2
            fi
            TARGET="$1"; shift ;;
    esac
done

SANDBOX="${TARGET:-/tmp/sol-skill-eval-claude}"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "build_sandbox_home: source dir $SOURCE_DIR does not exist" >&2
    exit 1
fi

# Refuse to wipe anything that isn't an obviously throwaway sandbox path.
# We're about to `rm -rf "$SANDBOX"`, so we want strong guarantees that
# the target is (a) absolute, (b) under /tmp, (c) not the user's real
# config dir or anything else load-bearing. A typo like an empty TARGET
# or `/` would otherwise be catastrophic.
case "$SANDBOX" in
    "" | "/" | "/tmp" | "/tmp/")
        echo "build_sandbox_home: refusing to operate on '$SANDBOX'" >&2
        exit 1 ;;
    /tmp/*) ;;
    *)
        echo "build_sandbox_home: target must be an absolute path under /tmp/" >&2
        echo "                    got: '$SANDBOX'" >&2
        exit 1 ;;
esac
if [ "$SANDBOX" = "$SOURCE_DIR" ] || [ "$SANDBOX" = "$HOME" ] || [ "$SANDBOX" = "$HOME/" ]; then
    echo "build_sandbox_home: refusing to wipe real config dir '$SANDBOX'" >&2
    exit 1
fi

# Wipe any prior sandbox so symlinks reflect the current real state.
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/skills"

# Symlink every top-level entry from the real config except `skills/`
# (handled below) and, if --no-claude-md, CLAUDE.md.
shopt -s dotglob nullglob
for entry in "$SOURCE_DIR"/*; do
    name="$(basename "$entry")"
    case "$name" in
        skills) continue ;;
        CLAUDE.md)
            [ "$NO_CLAUDE_MD" -eq 1 ] && continue
            ln -sfn "$entry" "$SANDBOX/$name" ;;
        .|..) continue ;;
        *) ln -sfn "$entry" "$SANDBOX/$name" ;;
    esac
done

# Symlink each user-scope skill except the one being hidden.
hidden_present=0
if [ -d "$SOURCE_DIR/skills" ]; then
    for skill in "$SOURCE_DIR/skills"/*; do
        [ -e "$skill" ] || continue
        name="$(basename "$skill")"
        if [ "$name" = "$HIDE_SKILL" ]; then
            hidden_present=1
            continue
        fi
        ln -sfn "$skill" "$SANDBOX/skills/$name"
    done
fi
shopt -u dotglob nullglob

# Inject the alternate skill tree if requested. This replaces whatever
# name was hidden when ADD_SKILL's basename matches HIDE_SKILL.
if [ -n "$ADD_SKILL" ]; then
    if [ ! -d "$ADD_SKILL" ]; then
        echo "build_sandbox_home: --add-skill path $ADD_SKILL is not a directory" >&2
        exit 1
    fi
    add_name="$(basename "$ADD_SKILL")"
    ln -sfn "$ADD_SKILL" "$SANDBOX/skills/$add_name"
fi

if [ "$hidden_present" -eq 0 ] && [ -z "$ADD_SKILL" ]; then
    echo "build_sandbox_home: note — '$HIDE_SKILL' is not installed at" \
         "user scope ($SOURCE_DIR/skills/), so the sandbox is" \
         "equivalent to your real config." >&2
fi

echo "$SANDBOX"
