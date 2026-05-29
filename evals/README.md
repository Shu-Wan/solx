# evals/

Eval harness for `sol-skill`. **Not** part of the shipped skill —
nothing in this directory is bundled into the `.skill` artifact.

For the conceptual layout (L0–L3 layers, release process, what's in
git vs. not), see [`../DEVELOPMENT.md`](../DEVELOPMENT.md).

For the public-facing coverage matrix, see
[`../docs/coverage.md`](../docs/coverage.md).

## Layout

```
evals/
├── README.md                       # this file
├── evals.example.json              # sanitized template; copy to evals.json
├── evals.json                      # gitignored — your real eval set
├── mocks/                          # userland Sol mock environment
│   ├── activate.sh                 # source to put mocks on PATH
│   ├── bin/                        # PATH shims
│   ├── home/                       # fake $HOME (CSVs + .solkeep)
│   └── scratch/                    # fake /scratch tree
├── runner/
│   ├── build_sandbox_home.sh       # hides the skill for fair baselines
│   └── run_l2_renew.py             # runnable L2 for the renewal feature
└── results/                        # gitignored — per-iteration benchmarks
```

## Quick start

```shell
# 1. Copy the template to start your private eval set
cp evals/evals.example.json evals/evals.json
# (edit evals.json with your real prompts; it's gitignored)

# 2. Verify the mock environment activates cleanly
source evals/mocks/activate.sh
hostname -a                          # → sc001.sol.rc.asu.edu (mocked)
echo "$MOCK_LOG"                     # path to per-session invocation log

# 3. Run the L2 renewal eval end-to-end. It builds its own sandbox
#    (real files + stale mtimes) and asserts the touch pass refreshes
#    kept files, honors .solkeep carve-outs, and leaves the rest alone.
#    Exits non-zero if any assertion fails.
evals/runner/run_l2_renew.py            # add -v to echo the script's output
```

> The static `mocks/` CSVs list absolute `/scratch/sparky/...` paths
> for L1 (parsing/plan) checks, so they can't prove real touching on a
> test box. `run_l2_renew.py` builds a self-contained tree under `$TMPDIR`
> and points the script at it, so it can assert filesystem mutations.

## Eval entry schema

`evals.json` (and `evals.example.json`) follows the skill-creator
schema with one extension — each assertion carries a `layer` tag and a
machine-checkable `check`.

```json
{
  "skill_name": "sol-skill",
  "evals": [
    {
      "id": 1,
      "name": "scratch-renewal-default-flow",
      "prompt": "User-style task prompt",
      "expected_output": "What success looks like, in one sentence",
      "setup": {
        "mock_hostname": "sc001.sol.rc.asu.edu",
        "include_solx": false
      },
      "assertions": [
        {
          "text": "Agent proposes running `sol_renew.py --dry-run` first",
          "layer": "L1",
          "check": {"transcript_contains": "sol_renew.py --dry-run"}
        },
        {
          "text": "Agent does not suggest `find /scratch -exec touch`",
          "layer": "L1",
          "check": {"transcript_lacks": "find /scratch"}
        },
        {
          "text": "Renewal refreshes kept files, honors .solkeep carve-outs, and skips the rest",
          "layer": "L2",
          "check": {"l2_script": "evals/runner/run_l2_renew.py", "exit_code": 0}
        }
      ]
    }
  ]
}
```

Layer tags drive how the runner executes each eval and how
`docs/coverage.md` is regenerated.

## Privacy

`evals.json` and `evals/results/` are gitignored because they may
include real ASURITEs, project names, partition names, and ports
specific to your Sol environment. The sanitized example uses
`sparky` (ASU's mascot) as a stand-in username and only references
public Sol concepts.

If you contribute new evals back upstream, please launder identifiers
out before opening a PR — `sed -i "s/$(whoami)/sparky/g"` over the
prompts is usually enough.
