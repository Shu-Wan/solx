# solx (Rust)

Native `solx` binary for ASU's Sol supercomputer: interactive Slurm job
management (`solx job start/stop/jump/time/list`), scratch renewal
(`solx keep`), and a single TOML config (`solx config`, `solx init`).

The command surface, JSON output, diagnostics, and exit codes match the
Python `solx` package in this repository (see `solx/docs/solx.md` for the
full command reference). One binary, no interpreter or virtualenv on the
critical path — a cold start from NFS home is a single exec.

## Build

Requires stable Rust (pinned via `rust-toolchain.toml`; rustup installs it
on first build).

```console
$ cd solx-rs
$ cargo build --release
$ ./target/release/solx --version
```

On Sol, keep build artifacts off NFS:

```console
$ export CARGO_TARGET_DIR=/tmp/solx-rs-target
$ cargo build --release
```

## Install

Copy the release binary anywhere on `PATH`:

```console
$ install -m 755 target/release/solx ~/.local/bin/solx
```

Then set up as usual:

```console
$ solx init                       # write the starter config
$ solx completions zsh > ~/.zfunc/_solx   # optional tab completion
```

## Output contract

* stdout is the data channel: JSON when piped or under `--json`, a plain
  table on a terminal.
* All diagnostics, progress, and prompts go to stderr.
* Exit codes: 0 success, 1 runtime failure, 2 usage error / missing config /
  refused action.

## UX notes

* Human tables are plain aligned columns; there is no color output yet.
* Confirmation prompts are plain `[y/N]` lines on stderr (TTY only;
  non-interactive sessions require `-y` or `-n`).
* `solx completions` emits static scripts (no runtime completion callback
  into the binary).

## Development

```console
$ cargo fmt --all
$ cargo clippy --all-targets -- -D warnings
$ cargo test
```

`cargo test` runs the unit suites plus end-to-end tests that drive the real
binary against deterministic SLURM mocks in `tests/mocks/bin`. See
`DEVELOPMENT.md` for the module map and the parity workflow against the
Python implementation.
