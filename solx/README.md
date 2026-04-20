# solx

Sol-side session and config CLI for ASU's Sol supercomputer.

This release ships **Sol-side commands only** — `solx` is meant to be
run after you've SSHed to Sol manually. Laptop-side composite commands
(`solx up`, `solx down`, etc.) ship as stubs that exit 2 with a
deferral message; they land in a follow-up release.

Full docs (install, profile config, examples, smoke checklist) live
under [`../docs/solx.md`](../docs/solx.md) and
[`../docs/solx-smoke.md`](../docs/solx-smoke.md).

## Quick install (on Sol)

```shell
uv tool install git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx
solx --version
solx config init
solx config show
```

## License

MIT.
