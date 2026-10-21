#!/usr/bin/env bash
# Evaluate skill/sol-agent/SKILL.md with headless `claude -p`. Dry-run by construction: permission mode dontAsk with a small
# read-only allow list, so every tool_use the model ATTEMPTS is recorded but only read-only commands really run.
# arm "with" = plugin containing the skill; arm "base" = same prompt, no skill.
# Runs use --setting-sources project, i.e. a BARE environment (no user CLAUDE.md, no user-scope skills such as sol-skill): the skill's
# description alone has to trigger it. Discovery of the installed user-scope skill with real user settings is a separate one-off check. Output: results/skill_eval/ (raw jsonl + summary).
set -u
R=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}; E=$R/tests/skill_eval; O=$R/results/skill_eval
# The work dir must NOT sit inside a repo or any directory Claude has memory for: the baseline arm would read it (this
# once leaked a project memory file into the "no skill" arm and inflated the baseline from 11/25 to 19/25).
W=$(mktemp -d "${TMPDIR:-/tmp}/sol-agent-skill-eval.XXXXXX")
rm -rf "$O"; mkdir -p "$O/raw" "$W/plugin/.claude-plugin" "$W/plugin/skills/sol-agent" "$W/cwd"
echo '{"name":"sol-agent-eval","version":"0.0.1","description":"eval copy of the sol-agent skill"}' > "$W/plugin/.claude-plugin/plugin.json"
SK=$R/skill/sol-agent/SKILL.md; [ -f "$SK" ] || SK=$R/../skills/sol-agent/SKILL.md   # dev copy beside the scripts, else the repo layout
cp "$SK" "$W/plugin/skills/sol-agent/SKILL.md"
ALLOW=( 'Skill' 'Read' 'Bash(*sol-agent status*)' 'Bash(*sol-agent logs*)' 'Bash(*sol-agent tunnel status*)' 'Bash(squeue *)' 'Bash(myfairshare*)' 'Bash(ls *)' )
DENY=( Write Edit NotebookEdit WebFetch WebSearch Agent )
one() {  # id arm prompt
  local id=$1 arm=$2 prompt=$3 pd=()
  [ "$arm" = with ] && pd=(--plugin-dir "$W/plugin")
  ( cd "$W/cwd" && timeout 600 claude -p "$prompt" --model sonnet --permission-mode dontAsk --setting-sources project \
      --no-session-persistence --max-budget-usd 1.5 --output-format stream-json --verbose \
      --allowedTools "${ALLOW[@]}" --disallowedTools "${DENY[@]}" "${pd[@]}" </dev/null > "$O/raw/$id.$arm.jsonl" 2> "$O/raw/$id.$arm.err" )
}
jobs=0
while IFS=$'\t' read -r id arm prompt; do
  one "$id" "$arm" "$prompt" & jobs=$((jobs+1))
  [ $((jobs % 4)) -eq 0 ] && wait
done < <(python3 -I -c '
import json,sys
for c in json.load(open(sys.argv[1])):
    for a in c["arms"]: print(c["id"],a,c["prompt"],sep="\t")' "$E/cases.json")
wait
python3 -I "$E/grade.py" "$O" "$E/cases.json" | tee "$O/summary.md"
echo done > "$O/DONE"
P="$HOME/.claude/projects/$(printf %s "$W/cwd" | sed 's|[/.]|-|g')"      # the throwaway project dir these runs created
case "$W" in "${TMPDIR:-/tmp}"/sol-agent-skill-eval.*) rm -rf "$W"; rm -rf "$P";; esac
