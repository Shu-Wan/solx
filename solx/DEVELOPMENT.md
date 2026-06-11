# Developing solx

solx is a single native binary (Rust). It reproduces the behavior the
Python implementation shipped through v0.5.0, captured as the
behavioral-parity golden matrix; the Python tree itself was retired in
v1.0.

## Parity is the spec

The v0.5.0 behavioral-parity golden matrix (`../evals/parity/`) is the
spec for the command surface. The goldens were captured from the v0.5.0
Python build and the binary must still reproduce them; when in doubt, the
goldens win:

* The matrix runs ~67 cases (every command, every error path) in isolated
  fake HOMEs with deterministic SLURM mocks and captures stdout/stderr/exit
  code per case.
* Strict cases must match byte-for-byte: JSON documents render like
  `json.dumps(obj, indent=2)` (two-space indent, `\uXXXX` escapes for
  non-ASCII, insertion-ordered keys), and stderr diagnostics reproduce the
  v0.5.0 strings with markup stripped.
* Help/usage text and completion scripts are relaxed (exit code + content
  smoke), so clap renders its own help.

When changing any user-visible string, check it against the golden
`.err`/`.out` files before changing the goldens themselves.

## Module map

| Module            | Contents                                              |
| ----------------- | ----------------------------------------------------- |
| `src/main.rs`     | clap command tree, dispatch, `config show`/`edit`     |
| `src/side.rs`     | Sol host detection (`hostname -a` + kernel hostname)  |
| `src/slurm.rs`    | squeue/scancel/salloc/srun wrappers, jobid resolution |
| `src/config.rs`   | TOML config, `[keep]` rules, `.solkeep` parsing       |
| `src/jobs.rs`     | job list/start/stop/jump/time bodies                  |
| `src/keep.rs`     | CSV plan, enumeration, touch pipeline                 |
| `src/init.rs`     | starter config, `.solkeep` migration                  |
| `src/output.rs`   | TTY detection, JSON writer matching the v0.5.0 format |
| `src/completions.rs` | embedded static completion scripts                 |

Notable design decisions:

* **CLI parsing.** clap handles the tree; two paths are parsed by hand
  because their semantics predate clap conventions: the leading global
  flags (`--json`, eager `--version`) and the whole `job start` tail, where
  the first unconsumed bare token — even after `--` — is the template and
  every other leftover token passes through to salloc in order.
* **`[keep]` matching.** `ignore::gitignore::Gitignore` rooted at `/` with
  `matched_path_or_any_parents`, so a bare path pattern matches the
  directory and everything under it and `!` negations win last-match style.
  The keep-matching vectors carried over from the v0.5.0 suite live as unit
  tests in `config.rs` and `keep.rs`; run them before touching matcher code.
* **Enumeration.** `ignore::WalkBuilder` with every ignore facility off
  (`hidden(false)`, `ignore(false)`, `git_*(false)`, `parents(false)`,
  `follow_links(false)`), files only — semantics equal `find DIR -type f`,
  hidden files included.
* **Touch.** `filetime::set_file_times` to now; a missing path is a silent
  skip and nothing is ever created (`touch -c` semantics).
* **Completion scripts.** `assets/` holds the static bash/zsh/fish scripts,
  embedded via `include_str!`. They match the v0.5.0 completion output and
  are checked by the parity matrix; edit them as a set so the three shells
  stay in sync.

## Tests

Toolchain setup on Sol (rustup user-install, `CARGO_TARGET_DIR`, crates.io
access, glibc vs musl) is covered in
[`README.md` → Toolchain on Sol](README.md#toolchain-on-sol).

```console
$ export CARGO_TARGET_DIR=/tmp/solx-target
$ cargo fmt --all --check
$ cargo clippy --all-targets -- -D warnings
$ cargo test
```

* Unit tests live next to each module and carry the v0.5.0 suite's vectors
  (slurm parsing, config/`.solkeep`, the keep matching/planning vectors,
  JSON formatting).
* `tests/cli.rs` drives the compiled binary end-to-end with the SLURM mocks
  in `tests/mocks/bin` and a tempdir HOME/XDG, asserting stdout, stderr,
  and exit codes for the core flows.

CI (`.github/workflows/ci.yml`) runs the same three commands (`check`
job) on every push to main and every PR, plus a `build` job that
compiles the portable binary and uploads it (see below).

## Building and installing

A native development build, for running on the same machine:

```console
$ export CARGO_TARGET_DIR=/tmp/solx-target   # keep artifacts off the NFS home
$ cargo build --release                          # -> $CARGO_TARGET_DIR/release/solx
$ cp "$CARGO_TARGET_DIR/release/solx" ~/.local/bin-test/solx
```

This links the host's glibc, so it runs on the box it was built on (Sol
included). For a binary that runs anywhere — the form CI uploads and a
release ships — build the statically linked musl target:

```console
$ rustup target add x86_64-unknown-linux-musl    # one-time; no musl-gcc needed
$ cargo build --release --target x86_64-unknown-linux-musl
```

The result is a self-contained executable (`ldd` reports "statically
linked") with no libc-version dependency.

**From a PR, without a toolchain.** The `build` job attaches the musl
binary as the `solx-x86_64-linux-musl` artifact on every push/PR.
Download it from the PR's *Checks → Artifacts*, `chmod +x`, and run it
on Sol as-is — no install step, no toolchain:

```console
$ chmod +x solx && ./solx --version
```
