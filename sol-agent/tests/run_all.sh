#!/usr/bin/env bash
# Run the whole suite one after another (about 30-40 min, uses short Slurm jobs and a few tiny real Claude sessions).
# live_tunnel_401.sh is left out on purpose: it logs in to GitHub's tunnel service with the user's gh token. Run it by hand.
R=$(cd "$(dirname "$0")/.." && pwd); mkdir -p "$R/results"; O=$R/results/run_all.txt; : > "$O"
failed=0
for t in test_maint test_pool test_perm_msg test_safety test_cli_contract test_alert test_tunnel e2e live_tunnel; do
  echo "=== $t $(date +%T)" >> "$O"; "$R/tests/$t.sh" > "$R/results/$t.run" 2>&1; rc=$?
  echo "$t rc=$rc  $(tail -1 "$R/results/$t.run")" >> "$O"
  [ "$rc" -eq 0 ] || failed=$((failed+1))
done
"$R/bin/sol-agent" gc --tests -y --text >> "$O" 2>&1 || { echo "gc --tests failed" >> "$O"; failed=$((failed+1)); }   # catch any test job a teardown race left behind
echo "=== finished $(date +%T): $failed failed" >> "$O"; touch "$R/results/run_all.DONE"
[ "$failed" -eq 0 ]
