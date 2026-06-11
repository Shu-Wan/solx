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
├── parity/                         # solx CLI behavioral parity matrix
│   └── README.md                   # how to capture goldens + compare runs
├── runner/
│   ├── bench_solx_latency.sh       # L3: solx vs raw SLURM latency, on real Sol
│   └── build_sandbox_home.sh       # hides the skill for fair baselines
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

# 3. The renewal mechanism is tested in the solx crate — run that suite
#    for the L2 filesystem-mutation coverage (real files + stale mtimes;
#    refreshes kept files, honors carve-outs, skips the rest):
( cd solx && cargo test --test cli )
```

> The static `mocks/` CSVs list absolute `/scratch/sparky/...` paths
> for L1 (parsing/plan) checks, so they can't prove real touching on a
> test box. The end-to-end real-touch test in `solx/tests/cli.rs`
> builds a self-contained tree under `$TMPDIR` with stale mtimes and
> asserts the filesystem mutations.

## CLI parity matrix

[`parity/`](parity/README.md) regression-tests the **`solx` CLI itself**
(rather than the skill): 67 cases over the full command surface, each in
an isolated fake `$HOME` with deterministic SLURM mocks, captured as
stdout/stderr/exit-code and compared byte-for-byte between two `solx`
builds. Use it whenever the dispatch layer or runtime changes and the
command surface must provably not. Goldens are environment-captured and
not committed — see its README for the capture/compare workflow.

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
        "include_solx": true
      },
      "assertions": [
        {
          "text": "Agent proposes running `solx keep --dry-run` first",
          "layer": "L1",
          "check": {"transcript_contains": "solx keep --dry-run"}
        },
        {
          "text": "Agent does not suggest `find /scratch -exec touch`",
          "layer": "L1",
          "check": {"transcript_lacks": "find /scratch"}
        },
        {
          "text": "Renewal refreshes kept files, honors carve-outs, and skips the rest",
          "layer": "L2",
          "check": {"l2_script": "solx/tests/test_keep.py", "exit_code": 0}
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
