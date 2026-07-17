# solx

The `solx` CLI for ASU's Sol supercomputer: interactive Slurm job
management (`solx job start/stop/jump/time/list`), scratch renewal
(`solx keep`), and a single TOML config (`solx config`, `solx init`).

A single native binary (Rust). The command surface, JSON output,
diagnostics, and exit codes are locked by the crate's end-to-end and unit
tests; see [`../docs/solx.md`](../docs/solx.md) for the full command
reference. One binary, no interpreter or virtualenv on the critical path -
a cold start from NFS home is a single exec.

## Install

The supported install is a prebuilt single binary from a CI release: no
Rust toolchain, no Python, no `uv` on the box. Download it, make it
executable, and put it anywhere on `PATH`:

```console
$ mkdir -p ~/.local/bin
$ curl -fLo solx https://github.com/Shu-Wan/solx/releases/latest/download/solx-x86_64-unknown-linux-musl
$ chmod +x solx
$ mv solx ~/.local/bin/
```

Then set up as usual:

```console
$ solx init                       # write the starter config
$ solx completions zsh > ~/.zfunc/_solx   # optional tab completion
```

## Toolchain on Sol

Contributor setup for building from source - users installing a release
binary never need any of this. None of it requires sudo.

* **Rust via rustup, user-install.**

  ```console
  $ curl https://sh.rustup.rs | sh -s -- -y --profile minimal
  ```

  Installs to `~/.cargo` and works on both login and compute nodes.
  `rust-toolchain.toml` pins the channel; rustup fetches it on first build.

* **Build artifacts on node-local storage.** Build artifacts on the NFS
  home are painfully slow; point `CARGO_TARGET_DIR` at node-local storage.
  The `~/.cargo` registry cache staying on NFS is a one-time acceptable
  cost.

  ```console
  $ export CARGO_TARGET_DIR=/tmp/solx-target
  ```

* **crates.io connectivity.** crates.io is reachable from compute nodes
  but rejects UA-less HEAD probes with 403, so `curl -I` reports failure
  on a working connection. Verify with a real GET:

  ```console
  $ curl -fsS https://index.crates.io/config.json
  ```

* **glibc.** A binary built on Sol links against RHEL 8's glibc 2.28 and
  runs on Sol. CI releases target `x86_64-unknown-linux-musl` (fully
  static) for portability.

With the toolchain in place:

```console
$ cd solx
$ cargo build --release
$ "${CARGO_TARGET_DIR:-target}/release/solx" --version
```

To run a local build, copy it onto `PATH`:

```console
$ install -m 755 "${CARGO_TARGET_DIR:-target}/release/solx" ~/.local/bin/solx
```

## Output contract

* stdout is the data channel: JSON when piped or under `--json`, a plain
  table on a terminal.
* All diagnostics, progress, and prompts go to stderr.
* Exit codes: 0 success, 1 runtime failure, 2 usage error / missing config /
  refused action.

## UX notes

* Human tables are plain aligned columns; nothing emits color.
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
`DEVELOPMENT.md` for the module map and the behavior contract.
