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
├── runner/                         # thin wrapper over skill-creator
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

# 3. Run an L2 sanity check end-to-end
skills/sol-skill/scripts/sol_renew.py --dry-run -v
cat "$MOCK_LOG"                      # see what got invoked
```

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
          "text": "sol_renew.py exits 0 against the mocked CSV+.solkeep state",
          "layer": "L2",
          "check": {"exit_code": 0}
        },
        {
          "text": "Touch ran exclusively on directories matched by .solkeep",
          "layer": "L2",
          "check": {"mock_log_contains": "touch -a -m -c"}
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
