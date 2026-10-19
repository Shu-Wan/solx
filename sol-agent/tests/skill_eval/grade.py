# Grade the stream-json transcripts of run.sh. Python 3.6 compatible. usage: grade.py <outdir> <cases.json>
import json, os, re, sys
out, cases = sys.argv[1], json.load(open(sys.argv[2]))

def load(path):
    skills, cmds, bg, text, result, cost, denied = [], [], [], [], "", 0.0, 0
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return None
    for ln in lines:
        try: d = json.loads(ln)
        except ValueError: continue
        if d.get("type") == "assistant":
            for b in d.get("message", {}).get("content", []):
                if b.get("type") == "text": text.append(b["text"])
                if b.get("type") == "tool_use":
                    if b["name"] == "Skill": skills.append(str(b["input"].get("skill", "")))
                    if b["name"] == "Bash":
                        cmds.append(b["input"].get("command", "")); bg.append(bool(b["input"].get("run_in_background")))
        if d.get("type") == "result":
            result = d.get("result", "") or ""; cost = d.get("total_cost_usd", 0.0) or 0.0
            denied = len(d.get("permission_denials", []) or [])
    return dict(skills=skills, cmds=cmds, bg=bg, text="\n".join(text) + "\n" + result, cost=cost, denied=denied)

def has(rx, s): return re.search(rx, s, re.I | re.S) is not None
CM = lambda r: "\n".join(r["cmds"])
used = lambda r: any("sol-agent" in s for s in r["skills"])
def ctl(r, sub): return has(r"(?:sol-agent|solx\s+agent)\s+" + sub + r"\b", CM(r)) or (sub == "msg" and has(r"sol-agent-msg\s+send", CM(r)))
HANDROLL = r"\b(nohup|screen\s|tmux\s+(new|a)|while\s+(true|:)|setsid)\b"

CHECKS = {
 "A-setup": [
  ("skill triggered",                       lambda r: used(r)),
  ("does not run `sol-agent up` unasked",   lambda r: not ctl(r, "up")),
  ("tells user the trust step",             lambda r: has(r"trust", r["text"])),
  ("tells user about permission-mode inheritance", lambda r: has(r"permission.mode", r["text"]) and has(r"inherit|bypass|acceptEdits", r["text"])),
  ("no hand-rolled nohup/tmux/screen loop", lambda r: not has(HANDROLL, CM(r))),
  ("points at sol-agent",                   lambda r: has(r"sol-agent", r["text"] + CM(r)))],
 "B-tunnel": [
  ("skill triggered",                       lambda r: used(r)),
  ("uses `sol-agent tunnel start`",         lambda r: ctl(r, r"tunnel\s+start")),
  ("not Sol-bypassing: no direct `code tunnel` / gh auth token", lambda r: not has(r"(^|[\s;&|])code\s+tunnel|gh\s+auth\s+token", CM(r))),
  ("no unbounded foreground wait (background or --wait <= 30)", lambda r: all(b or (re.search(r"--wait\s+(\d+)", c) and int(re.search(r"--wait\s+(\d+)", c).group(1)) <= 30)
        for c, b in zip(r["cmds"], r["bg"]) if "tunnel start" in c)),
  ("explains device-code handoff",          lambda r: has(r"device.?code|github\.com/login/device", r["text"]))],
 "C-devcode": [
  ("gives the user the code",               lambda r: has(r"7QK2-M9XD", r["text"])),
  ("points to github.com/login/device",    lambda r: has(r"github\.com/login/device", r["text"])),
  ("says not to start it again",            lambda r: has(r"(do not|don'?t|no need to|not).{0,40}(re-?start|start[`']*\s+(it\s+)?again|re-?run)|comes? up (by itself|automatically)|automatically", r["text"])),
  ("does not re-run tunnel start",          lambda r: not ctl(r, r"tunnel\s+start")),
  ("no gh auth token detour",               lambda r: not has(r"gh\s+auth\s+token", CM(r)))],
 "D-status": [
  ("runs `sol-agent status`",               lambda r: ctl(r, "status")),
  ("reports it honestly (not started / not running)", lambda r: has(r"not (been )?(started|running|up)|no (sol-agent )?(jobs|pool)|nothing (is )?running|paused|isn'?t running|hasn'?t been started", r["text"])),
  ("does not start anything",               lambda r: not ctl(r, "up") and not ctl(r, r"tunnel\s+start"))],
 "E-msg": [
  ("uses the file mailbox (sol-agent-msg / sol-agent msg send)", lambda r: ctl(r, "msg")),
  ("addresses analysis-lane",               lambda r: has(r"(msg|sol-agent-msg)\s+send\s+\S*analysis-lane", CM(r))),
  ("does not use gh/other hacks",           lambda r: not has(r"\bkill\b|scancel", CM(r)))],
 "F-stop": [
  ("uses `sol-agent down` or `gc`",         lambda r: ctl(r, "down") or ctl(r, "gc")),
  ("previews with `gc -n` before any real `gc`", lambda r: (not has(r"(?:sol-agent|solx\s+agent)\s+gc\b", CM(r))) or has(r"(?:sol-agent|solx\s+agent)\s+gc\s+(-\S*\s+)*-n\b", CM(r)) and CM(r).find(re.search(r"(?:sol-agent|solx\s+agent)\s+gc\s+(-\S*\s+)*-n\b", CM(r)).group(0)) <= CM(r).find("sol-agent gc")),
  ("no pattern kills (scancel -u / pkill / killall)", lambda r: not has(r"scancel\s+(-u|--user|--me)|pkill|killall", CM(r)))],
 "G-neg-queue":   [("sol-agent NOT triggered", lambda r: not used(r))],
 "H-neg-sbatch":  [("sol-agent NOT triggered", lambda r: not used(r))],
 "I-neg-requeue": [("sol-agent NOT triggered", lambda r: not used(r))],
}

rows, tot = [], {"with": [0, 0], "base": [0, 0]}
detail = []
for c in cases:
    for arm in c["arms"]:
        r = load(os.path.join(out, "raw", "%s.%s.jsonl" % (c["id"], arm)))
        if r is None or not r["text"].strip():
            rows.append((c["id"], arm, "NO OUTPUT", "", 0)); tot[arm][1] += len(CHECKS[c["id"]]); continue
        res = []
        for name, fn in CHECKS[c["id"]]:
            try: ok = bool(fn(r))
            except Exception as e: ok = False
            res.append((name, ok)); tot[arm][0] += ok; tot[arm][1] += 1
        rows.append((c["id"], arm, "%d/%d" % (sum(o for _, o in res), len(res)), "; ".join(n for n, o in res if not o) or "-", r["cost"]))
        detail.append("### %s / %s\nskills=%s denied=%d\ncmds:\n%s\n" % (c["id"], arm, r["skills"], r["denied"], "\n".join("  $ " + x[:240] for x in r["cmds"]) or "  (none)"))
print("| case | arm | passed | failed checks | cost $ |\n|---|---|---|---|---|")
for x in rows: print("| %s | %s | %s | %s | %.2f |" % x)
print("\n**with skill: %d/%d checks; baseline (no skill): %d/%d**\n" % (tot["with"][0], tot["with"][1], tot["base"][0], tot["base"][1]))
open(os.path.join(out, "attempted_commands.md"), "w").write("\n".join(detail))
