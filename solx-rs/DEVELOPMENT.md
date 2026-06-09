# Developing solx-rs

## Parity first

The Python package under `../solx/` is the reference implementation and the
behavioral-parity golden matrix is the spec. When the two disagree, the
goldens win:

* A behavioral matrix runs ~67 cases (every command, every error path) in
  isolated fake HOMEs with deterministic SLURM mocks and captures
  stdout/stderr/exit code per case.
* Strict cases must match byte-for-byte: JSON documents render exactly like
  Python's `json.dumps(obj, indent=2)` (two-space indent, `\uXXXX` escapes
  for non-ASCII, insertion-ordered keys), and stderr diagnostics reproduce
  the Python strings with markup stripped.
* Help/usage text and completion scripts are relaxed (exit code + content
  smoke), so clap renders its own help.

When changing any user-visible string, copy it from the Python source (drop
the `[red]`/`[bold]`/`[dim]` markup, unescape `\[` to `[`) and check it
against the golden `.err`/`.out` files.

## Module map

Modules mirror the Python package one-to-one:

| Rust              | Python       | Contents                                              |
| ----------------- | ------------ | ----------------------------------------------------- |
| `src/main.rs`     | `cli.py`     | clap command tree, dispatch, `config show`/`edit`     |
| `src/side.rs`     | `side.py`    | Sol host detection (`hostname -a` + kernel hostname)  |
| `src/slurm.rs`    | `slurm.py`   | squeue/scancel/salloc/srun wrappers, jobid resolution |
| `src/config.rs`   | `config.py`  | TOML config, `[keep]` rules, `.solkeep` parsing       |
| `src/jobs.rs`     | `jobs.py`    | job list/start/stop/jump/time bodies                  |
| `src/keep.rs`     | `keep.py`    | CSV plan, enumeration, touch pipeline                 |
| `src/init.rs`     | `init.py`    | starter config, `.solkeep` migration                  |
| `src/output.rs`   | `output.py`  | TTY detection, Python-equivalent JSON writer          |
| `src/completions.rs` | (cli.py) | embedded static completion scripts                    |

Notable porting decisions:

* **CLI parsing.** clap handles the tree; two paths are parsed by hand
  because their semantics predate clap conventions: the leading global
  flags (`--json`, eager `--version`) and the whole `job start` tail, where
  the first unconsumed bare token — even after `--` — is the template and
  every other leftover token passes through to salloc in order.
* **`[keep]` matching.** `ignore::gitignore::Gitignore` rooted at `/` with
  `matched_path_or_any_parents`, so a bare path pattern matches the
  directory and everything under it and `!` negations win last-match style.
  The matching vectors from the Python test suite are ported as unit tests
  in `config.rs` and `keep.rs`; run them before touching matcher code.
* **Enumeration.** `ignore::WalkBuilder` with every ignore facility off
  (`hidden(false)`, `ignore(false)`, `git_*(false)`, `parents(false)`,
  `follow_links(false)`), files only — semantics equal `find DIR -type f`,
  hidden files included.
* **Touch.** `filetime::set_file_times` to now; a missing path is a silent
  skip and nothing is ever created (`touch -c` semantics).
* **Completion scripts.** `assets/` holds the static bash/zsh/fish scripts,
  embedded via `include_str!`. They are synced from the Python package's
  completion generator; regenerate there and copy over rather than editing
  the trees apart.

## Tests

```console
$ export CARGO_TARGET_DIR=/tmp/solx-rs-target   # on Sol: keep off NFS
$ cargo fmt --all --check
$ cargo clippy --all-targets -- -D warnings
$ cargo test
```

* Unit tests live next to each module and port the Python suite's vectors
  (`test_slurm.py`, `test_config.py`, the keep matching/planning vectors,
  JSON formatting).
* `tests/cli.rs` drives the compiled binary end-to-end with the SLURM mocks
  in `tests/mocks/bin` and a tempdir HOME/XDG, asserting stdout, stderr,
  and exit codes for the core flows.

CI (`.github/workflows/rust-ci.yml`) runs the same three commands on every
push/PR touching `solx-rs/`.
