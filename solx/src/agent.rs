//! `solx agent` - install the bundled sol-agent scripts and drive them.
//!
//! sol-agent keeps an always-on Claude session alive on Sol across
//! preemption, job time limits and maintenance (see docs/sol-agent.md). It
//! is a set of Bash scripts, not Rust: this module only
//!
//! - **installs / updates** them into `~/.solx/agent` (`solx agent install`).
//!   The scripts are embedded at build time from the repo's `sol-agent/`
//!   tree (like the cheat sheet), so one downloaded binary is the whole
//!   install and the scripts always match the binary's version.
//! - **forwards** every other `solx agent <args>` to `~/.solx/agent/bin/sol-agent`.
//!
//! Install rules (what makes updating safe):
//!
//! - *Code* (`bin/`, `lib/`, docs) is replaced on every install. Each file is
//!   written to a temp name and renamed over the old one, so a Bash process
//!   that is running the old script keeps reading the old inode instead of
//!   a half-rewritten file.
//! - *Seeds* (`config.env`, `workspace/CLAUDE.md`) are user-edited. They are
//!   written only when absent; `--force` replaces them and keeps the old
//!   copy as `<name>.bak`. A seed that differs from the bundled one is left
//!   alone. If the bundled text is new to the user (it differs from the copy
//!   the last install recorded in `.shipped/`), it is written beside the
//!   seed as `<name>.new` so a new default is never lost; the `.new` file is
//!   removed again once the seed matches the bundled text.
//! - Runtime data (`state/`, `logs/`, `results/`) is never touched.
//! - `<root>/.version` records which solx wrote the code.

use std::fs;
use std::io::{self, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::config::home_dir;
use crate::output::Out;

const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Environment variable the scripts use to find their own root.
pub const ROOT_ENV: &str = "SOL_AGENT_ROOT";
/// Set to `json` when the user passed solx's global `--json`; the scripts otherwise detect a terminal themselves.
pub const FORMAT_ENV: &str = "SOL_AGENT_FORMAT";

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    /// Replaced on every install.
    Code,
    /// Written only when absent (user edits it).
    Seed,
}

struct Asset {
    path: &'static str,
    body: &'static str,
    exec: bool,
    kind: Kind,
}

macro_rules! asset {
    ($path:literal, $kind:expr, $exec:expr) => {
        Asset {
            path: $path,
            body: include_str!(concat!("../../sol-agent/", $path)),
            exec: $exec,
            kind: $kind,
        }
    };
}

/// Everything `install` writes. A test checks that every file under
/// `sol-agent/bin` and `sol-agent/lib` is listed here, so a new script
/// cannot be forgotten.
const ASSETS: &[Asset] = &[
    asset!("bin/sol-agent", Kind::Code, true),
    asset!("bin/sol-agent-msg", Kind::Code, true),
    asset!("bin/sol-agent-tunnel.sh", Kind::Code, true),
    asset!("bin/sol-agent-wd.sh", Kind::Code, true),
    asset!("bin/sol-agent.sbatch", Kind::Code, true),
    asset!("bin/sol-agent-collect.sbatch", Kind::Code, true),
    asset!("lib/common.sh", Kind::Code, false),
    asset!("lib/maint.sh", Kind::Code, false),
    asset!("lib/out.sh", Kind::Code, false),
    asset!("lib/pool.sh", Kind::Code, false),
    asset!("README.md", Kind::Code, false),
    asset!("DESIGN.md", Kind::Code, false),
    asset!("config.env", Kind::Seed, false),
    asset!("workspace/CLAUDE.md", Kind::Seed, false),
];

const VERSION_FILE: &str = ".version";
/// Copies of the seeds as the last install shipped them (`.shipped/config.env`, ...). Comparing with it tells
/// "the shipped text changed since you last installed" from "your file differs from the shipped one", so a
/// `.new` copy is written once per change and deleting it is final until the shipped text changes again.
const SHIPPED_DIR: &str = ".shipped";

fn shipped_copy(root: &Path, a: &Asset) -> PathBuf {
    root.join(SHIPPED_DIR).join(a.path)
}

/// The last install already shipped exactly this text.
fn shipped_is_current(root: &Path, a: &Asset) -> bool {
    fs::read(shipped_copy(root, a))
        .map(|b| b == a.body.as_bytes())
        .unwrap_or(false)
}

/// Where sol-agent lives: `$SOL_AGENT_ROOT`, else `~/.solx/agent`.
pub fn default_root() -> PathBuf {
    match std::env::var_os(ROOT_ENV) {
        Some(v) if !v.is_empty() => absolute(PathBuf::from(v)),
        _ => home_dir().join(".solx").join("agent"),
    }
}

/// A relative root would resolve differently once the scripts `cd` elsewhere (tunnel jobs start in $HOME).
fn absolute(p: PathBuf) -> PathBuf {
    if p.is_absolute() {
        p
    } else {
        std::env::current_dir().map(|c| c.join(&p)).unwrap_or(p)
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Action {
    New,
    Updated,
    Unchanged,
    /// A seed file the user already has and that differs from the bundled one; left alone. Nothing new to
    /// offer: the bundled text is what the last install shipped (a `<name>.new` copy may still be waiting).
    Kept,
    /// Like `Kept`, but the bundled text is new to the user, so it was written (or refreshed) as `<name>.new`.
    Staged,
    /// A seed file replaced because of `--force` (old copy saved as `.bak`).
    Reset,
}

impl Action {
    fn label(self) -> &'static str {
        match self {
            Action::New => "new",
            Action::Updated => "updated",
            Action::Unchanged => "unchanged",
            Action::Kept => "kept",
            Action::Staged => "staged",
            Action::Reset => "reset",
        }
    }
}

/// `<name>.new`: the bundled copy of a seed the user has changed, written beside it so a new default is never lost.
fn new_copy(dest: &Path) -> PathBuf {
    dest.with_file_name(format!(
        "{}.new",
        dest.file_name().unwrap().to_string_lossy()
    ))
}

/// The `.new` copy is a regular file holding exactly the bundled body.
fn new_copy_is_current(dest: &Path, a: &Asset) -> bool {
    let p = new_copy(dest);
    fs::symlink_metadata(&p)
        .map(|m| m.file_type().is_file())
        .unwrap_or(false)
        && fs::read(&p)
            .map(|b| b == a.body.as_bytes())
            .unwrap_or(false)
}

fn want_mode(a: &Asset) -> u32 {
    if a.exec {
        0o755
    } else {
        0o644
    }
}

/// Decide what to do with one file. "Absent" means the path does not exist (a dangling symlink or an
/// unreadable file still exists, so a seed is never mistaken for a missing one and overwritten). Contents
/// are compared as bytes, so a non-UTF-8 `config.env` is kept like any other.
fn plan_one(root: &Path, a: &Asset, force: bool) -> Action {
    let dest = root.join(a.path);
    if fs::symlink_metadata(&dest).is_err() {
        return Action::New;
    }
    let same = fs::read(&dest)
        .map(|b| b == a.body.as_bytes())
        .unwrap_or(false);
    match a.kind {
        Kind::Seed => {
            if same {
                Action::Unchanged
            } else if force {
                Action::Reset
            } else if new_copy_is_current(&dest, a) || shipped_is_current(root, a) {
                Action::Kept
            } else {
                Action::Staged
            }
        }
        Kind::Code => {
            let mode_ok = fs::metadata(&dest)
                .map(|m| m.permissions().mode() & 0o777 == want_mode(a))
                .unwrap_or(false);
            if same && mode_ok {
                Action::Unchanged
            } else {
                Action::Updated
            }
        }
    }
}

/// Write `body` to `dest` through an exclusively created temp file in the same directory, then rename it
/// into place. The temp name is random and created with O_EXCL (two installs on different nodes sharing
/// NFS cannot collide, a planted symlink is never followed) and is removed again if anything fails.
fn write_atomic(dest: &Path, body: &[u8], mode: u32) -> io::Result<()> {
    let dir = dest.parent().expect("asset path has a parent");
    fs::create_dir_all(dir)?;
    let mut tmp = tempfile::Builder::new()
        .prefix(".solx-tmp-")
        .tempfile_in(dir)?;
    tmp.write_all(body)?;
    tmp.as_file()
        .set_permissions(fs::Permissions::from_mode(mode))?;
    tmp.persist(dest).map_err(|e| e.error)?;
    Ok(())
}

fn installed_version(root: &Path) -> Option<String> {
    fs::read_to_string(root.join(VERSION_FILE))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

pub fn cmd_install(dir: Option<PathBuf>, dry_run: bool, force: bool, out: &Out) -> i32 {
    let root = dir.map(absolute).unwrap_or_else(default_root);
    if root.to_string_lossy().chars().any(char::is_whitespace) {
        out.error(&format!(
            "error: {} contains whitespace; the sol-agent scripts do not support that. pick another --dir.",
            root.display()
        ));
        return 2;
    }
    let previous = installed_version(&root);

    let plan: Vec<(&Asset, Action)> = ASSETS
        .iter()
        .map(|a| (a, plan_one(&root, a, force)))
        .collect();

    // A seed that now matches the bundled one no longer needs its `.new` copy. Only a regular file holding exactly the
    // bundled body is removed, so anything else the user keeps under that name stays.
    let obsolete: Vec<&Asset> = plan
        .iter()
        .filter(|(a, action)| {
            matches!(action, Action::Unchanged | Action::Reset)
                && a.kind == Kind::Seed
                && new_copy_is_current(&root.join(a.path), a)
        })
        .map(|(a, _)| *a)
        .collect();

    if !dry_run {
        for (a, action) in &plan {
            if matches!(action, Action::Unchanged | Action::Kept) {
                continue;
            }
            let dest = root.join(a.path);
            if *action == Action::Staged {
                // atomic, so a symlink planted under the `.new` name is replaced, never written through
                if let Err(e) = write_atomic(&new_copy(&dest), a.body.as_bytes(), want_mode(a)) {
                    out.error(&format!(
                        "error: unable to write {}: {e}",
                        new_copy(&dest).display()
                    ));
                    return 1;
                }
                continue;
            }
            if *action == Action::Reset {
                let bak = dest.with_file_name(format!(
                    "{}.bak",
                    dest.file_name().unwrap().to_string_lossy()
                ));
                // through a temp file + rename, so an existing `.bak` (or a symlink named like it) is replaced, never written through
                let saved = fs::read(&dest).and_then(|b| write_atomic(&bak, &b, 0o600));
                if let Err(e) = saved {
                    out.error(&format!("error: unable to back up {}: {e}", dest.display()));
                    return 1;
                }
            }
            if let Err(e) = write_atomic(&dest, a.body.as_bytes(), want_mode(a)) {
                out.error(&format!("error: unable to write {}: {e}", dest.display()));
                return 1;
            }
        }
        if let Err(e) = write_atomic(
            &root.join(VERSION_FILE),
            format!("{VERSION}\n").as_bytes(),
            0o644,
        ) {
            out.error(&format!("error: unable to write {VERSION_FILE}: {e}"));
            return 1;
        }
        for a in ASSETS.iter().filter(|a| a.kind == Kind::Seed) {
            if shipped_is_current(&root, a) {
                continue;
            }
            if let Err(e) = write_atomic(&shipped_copy(&root, a), a.body.as_bytes(), want_mode(a)) {
                out.error(&format!(
                    "error: unable to record {SHIPPED_DIR}/{}: {e}",
                    a.path
                ));
                return 1;
            }
        }
        for a in &obsolete {
            let stale = new_copy(&root.join(a.path));
            if let Err(e) = fs::remove_file(&stale) {
                out.error(&format!("error: unable to remove {}: {e}", stale.display()));
                return 1;
            }
        }
    }

    let count = |want: Action| plan.iter().filter(|(_, a)| *a == want).count();
    let changed = count(Action::New) + count(Action::Updated) + count(Action::Reset);

    // `new_default` names the bundled copy of a changed seed, relative to the root, as `kept`/`staged` entries point at it
    let files: Vec<Value> = plan
        .iter()
        .map(|(a, action)| {
            let mut f = json!({"path": a.path, "action": action.label()});
            let waiting = *action == Action::Staged
                || (*action == Action::Kept && new_copy_is_current(&root.join(a.path), a));
            if waiting {
                f["new_default"] = json!(format!("{}.new", a.path));
            }
            f
        })
        .collect();
    let removed: Vec<String> = obsolete.iter().map(|a| format!("{}.new", a.path)).collect();
    let data = json!({
        "root": root.display().to_string(),
        "version": VERSION,
        "previous": previous,
        "dry_run": dry_run,
        "files": files,
        "removed": removed,
    });
    out.emit(&data, || {
        let head = match (&previous, dry_run) {
            (_, true) => format!("would install sol-agent {VERSION} into {}", root.display()),
            (None, false) => format!("installed sol-agent {VERSION} into {}", root.display()),
            (Some(p), false) if p == VERSION => {
                format!("sol-agent {VERSION} in {} (reinstalled)", root.display())
            }
            (Some(p), false) => format!("updated sol-agent {p} -> {VERSION} in {}", root.display()),
        };
        let mut lines = vec![head];
        for (a, action) in &plan {
            match action {
                Action::Unchanged => {}
                Action::Staged => lines.push(format!(
                    "  {:<9} {}  (yours differs; the new bundled copy is {}.new)",
                    action.label(),
                    a.path,
                    a.path
                )),
                Action::Kept if new_copy_is_current(&root.join(a.path), a) => lines.push(format!(
                    "  {:<9} {}  (yours differs; the bundled copy is waiting as {}.new)",
                    action.label(),
                    a.path,
                    a.path
                )),
                Action::Kept => lines.push(format!(
                    "  {:<9} {}  (yours differs; nothing new in the bundled copy)",
                    action.label(),
                    a.path
                )),
                _ => lines.push(format!("  {:<9} {}", action.label(), a.path)),
            }
        }
        for stale in &removed {
            lines.push(format!(
                "  removed   {stale}  (the file now matches the bundled copy)"
            ));
        }
        lines.push(format!("  {} unchanged", count(Action::Unchanged)));
        Some(lines.join("\n"))
    });

    let staged: Vec<&str> = plan
        .iter()
        .filter(|(_, action)| *action == Action::Staged)
        .map(|(a, _)| a.path)
        .collect();
    if let (false, Some(first)) = (dry_run, staged.first()) {
        let (what, copies) = if staged.len() == 1 {
            (
                format!("your {first} as it is"),
                "the copy this solx ships is beside it",
            )
        } else {
            (
                format!("your {} as they are", staged.join(" and ")),
                "the copies this solx ships are beside them",
            )
        };
        out.status(&format!(
            "kept {what}; {copies} as <name>.new, because the shipped text changed since your last install. \
             Compare with `diff {dir}/{first} {dir}/{first}.new`, copy over what you want, then delete the .new file.",
            dir = root.display(),
        ));
    }
    if dry_run {
        return 0;
    }
    if root != default_root() {
        out.status(&format!(
            "note: not the default location - export {ROOT_ENV}={} when you run it. \
             workspace/CLAUDE.md names ~/.solx/agent paths; edit them for this location.",
            root.display()
        ));
    }
    if previous.is_none() {
        out.status(&format!(
            "next: cd {} && claude   (accept the trust prompt, then /exit)",
            root.join("workspace").display()
        ));
        out.status("      then: solx agent up      (docs/sol-agent.md has the full walk-through)");
    } else if changed > 0 {
        out.status(
            "a running agent keeps the old code until it restarts: \
             `solx agent down`, then `solx agent up` to switch now.",
        );
    }
    0
}

/// Forward `solx agent <args>` to `<root>/bin/sol-agent`.
///
/// Output format follows the same rule as the rest of solx: the scripts print JSON when their stdout is not a terminal,
/// which they detect themselves, so the only thing to pass down is an explicit global `--json` (`SOL_AGENT_FORMAT`).
pub fn cmd_run(args: &[String], force_json: bool) -> i32 {
    let root = default_root();
    let bin = root.join("bin").join("sol-agent");
    if !bin.is_file() {
        eprintln!(
            "error: sol-agent is not installed at {} - run `solx agent install` first.",
            root.display()
        );
        return 1;
    }
    match installed_version(&root) {
        Some(v) if v == VERSION => {}
        Some(v) => eprintln!(
            "note: installed sol-agent is {v} but this solx bundles {VERSION} - \
             run `solx agent install` to update."
        ),
        None => eprintln!(
            "note: no {VERSION_FILE} in {} - run `solx agent install` to refresh it.",
            root.display()
        ),
    }
    let mut cmd = std::process::Command::new(&bin);
    cmd.args(args).env(ROOT_ENV, &root);
    if force_json {
        cmd.env(FORMAT_ENV, "json");
    }
    let err = cmd.exec();
    eprintln!("error: unable to run {}: {err}", bin.display());
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    fn out() -> Out {
        Out {
            json_mode: true,
            interactive: false,
        }
    }

    #[test]
    fn every_script_in_the_tree_is_embedded() {
        let base = Path::new(env!("CARGO_MANIFEST_DIR")).join("../sol-agent");
        for sub in ["bin", "lib"] {
            for entry in fs::read_dir(base.join(sub)).unwrap() {
                let name = entry.unwrap().file_name().to_string_lossy().into_owned();
                let rel = format!("{sub}/{name}");
                assert!(
                    ASSETS.iter().any(|a| a.path == rel),
                    "{rel} exists in sol-agent/ but is not in ASSETS"
                );
            }
        }
    }

    #[test]
    fn assets_are_sane() {
        for a in ASSETS {
            assert!(!a.body.is_empty(), "{} is empty", a.path);
            if a.path.starts_with("bin/") {
                assert!(a.exec, "{} must be executable", a.path);
                assert!(a.body.starts_with("#!"), "{} needs a shebang", a.path);
            }
        }
        let seeds: Vec<_> = ASSETS
            .iter()
            .filter(|a| a.kind == Kind::Seed)
            .map(|a| a.path)
            .collect();
        assert_eq!(seeds, ["config.env", "workspace/CLAUDE.md"]);
    }

    #[test]
    fn shipped_text_has_no_author_specifics() {
        for a in ASSETS {
            for bad in ["/home/swan16", "huanliu", "swan16"] {
                assert!(!a.body.contains(bad), "{} mentions {bad}", a.path);
            }
        }
    }

    #[test]
    fn install_update_and_force() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");

        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(installed_version(&root).as_deref(), Some(VERSION));
        let exec = fs::metadata(root.join("bin/sol-agent")).unwrap();
        assert_eq!(exec.permissions().mode() & 0o777, 0o755);
        assert_eq!(
            fs::metadata(root.join("lib/common.sh"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o644
        );

        // User edits a seed; local damage to code is repaired.
        fs::write(root.join("config.env"), "PERMISSION_MODE=acceptEdits\n").unwrap();
        fs::write(root.join("lib/common.sh"), "# broken\n").unwrap();
        fs::create_dir_all(root.join("state")).unwrap();
        fs::write(root.join("state/lease"), "keep me").unwrap();

        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env")).unwrap(),
            "PERMISSION_MODE=acceptEdits\n"
        );
        assert!(fs::read_to_string(root.join("lib/common.sh"))
            .unwrap()
            .contains("sol-agent common library"));
        assert_eq!(
            fs::read_to_string(root.join("state/lease")).unwrap(),
            "keep me"
        );
        let leftovers: Vec<_> = fs::read_dir(root.join("lib"))
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().contains("solx-tmp"))
            .collect();
        assert!(leftovers.is_empty(), "temp files left behind");

        // --force resets the seed and keeps the old copy.
        assert_eq!(cmd_install(Some(root.clone()), false, true, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env.bak")).unwrap(),
            "PERMISSION_MODE=acceptEdits\n"
        );
        assert_ne!(
            fs::read_to_string(root.join("config.env")).unwrap(),
            "PERMISSION_MODE=acceptEdits\n"
        );
    }

    fn asset(path: &str) -> &'static Asset {
        ASSETS.iter().find(|a| a.path == path).unwrap()
    }

    /// Pretend the last install came from a solx that shipped different text for `rel` (a release changed it).
    fn older_release(root: &Path, rel: &str) {
        let p = root.join(SHIPPED_DIR).join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, "# shipped by an older solx\n").unwrap();
    }

    #[test]
    fn the_last_shipped_text_is_recorded_on_install_only() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), true, false, &out()), 0);
        assert!(!root.exists());
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        for a in ASSETS.iter().filter(|a| a.kind == Kind::Seed) {
            assert_eq!(
                fs::read_to_string(root.join(SHIPPED_DIR).join(a.path)).unwrap(),
                a.body
            );
        }
        assert!(!root.join(SHIPPED_DIR).join("bin/sol-agent").exists());
        // a dry run leaves an old record alone; a real install brings it up to date
        older_release(&root, "config.env");
        assert_eq!(cmd_install(Some(root.clone()), true, false, &out()), 0);
        assert!(!shipped_is_current(&root, asset("config.env")));
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(shipped_is_current(&root, asset("config.env")));
    }

    #[test]
    fn changed_seed_gets_the_bundled_copy_beside_it() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        let cfg = asset("config.env");
        // untouched seeds stay matching the bundled copy and get no `.new`
        assert_eq!(plan_one(&root, cfg, false), Action::Unchanged);
        assert!(!root.join("config.env.new").exists());

        fs::write(root.join("config.env"), "PERMISSION_MODE=acceptEdits\n").unwrap();
        // your edits alone are not news: the bundled text is what the last install shipped
        assert_eq!(plan_one(&root, cfg, false), Action::Kept);
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(!root.join("config.env.new").exists());

        // a newer solx ships different text: now it is offered
        older_release(&root, "config.env");
        assert_eq!(plan_one(&root, cfg, false), Action::Staged);
        // a dry run reports it and writes nothing
        assert_eq!(cmd_install(Some(root.clone()), true, false, &out()), 0);
        assert!(!root.join("config.env.new").exists());

        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env")).unwrap(),
            "PERMISSION_MODE=acceptEdits\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("config.env.new")).unwrap(),
            cfg.body
        );
        // the second install finds the copy current and leaves it alone
        assert_eq!(plan_one(&root, cfg, false), Action::Kept);
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(plan_one(&root, cfg, false), Action::Kept);

        // deleting the copy is final until the shipped text changes again
        fs::remove_file(root.join("config.env.new")).unwrap();
        assert_eq!(plan_one(&root, cfg, false), Action::Kept);
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(!root.join("config.env.new").exists());

        // an out-of-date copy is refreshed when the shipped text changes again
        older_release(&root, "config.env");
        fs::write(root.join("config.env.new"), "# older default\n").unwrap();
        assert_eq!(plan_one(&root, cfg, false), Action::Staged);
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env.new")).unwrap(),
            cfg.body
        );

        // no record at all (installed by a solx that did not keep one): offer it once
        fs::remove_file(root.join("config.env.new")).unwrap();
        fs::remove_dir_all(root.join(SHIPPED_DIR)).unwrap();
        assert_eq!(plan_one(&root, cfg, false), Action::Staged);
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(root.join("config.env.new").is_file());
        assert!(shipped_is_current(&root, cfg));

        // the other seed is independent
        assert_eq!(
            plan_one(&root, asset("workspace/CLAUDE.md"), false),
            Action::Unchanged
        );
        assert!(!root.join("workspace/CLAUDE.md.new").exists());
    }

    #[test]
    fn the_new_copy_goes_away_once_the_seed_matches() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        let cfg = asset("config.env");
        fs::write(root.join("config.env"), "MINE=1\n").unwrap();
        older_release(&root, "config.env");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(root.join("config.env.new").is_file());

        // the user adopts the shipped copy by hand: the leftover `.new` is removed, a dry run only says so
        fs::copy(root.join("config.env.new"), root.join("config.env")).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), true, false, &out()), 0);
        assert!(root.join("config.env.new").exists());
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(!root.join("config.env.new").exists());
        assert_eq!(
            fs::read_to_string(root.join("config.env")).unwrap(),
            cfg.body
        );

        // --force: the old file is saved as `.bak`, the seed matches, so the `.new` copy goes too
        fs::write(root.join("config.env"), "MINE=2\n").unwrap();
        older_release(&root, "config.env");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(root.join("config.env.new").is_file());
        assert_eq!(cmd_install(Some(root.clone()), false, true, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env.bak")).unwrap(),
            "MINE=2\n"
        );
        assert!(!root.join("config.env.new").exists());

        // a file under that name that is not the shipped copy is never deleted
        fs::write(root.join("config.env.new"), "something of the user's\n").unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(
            fs::read_to_string(root.join("config.env.new")).unwrap(),
            "something of the user's\n"
        );
    }

    #[test]
    fn the_new_copy_never_writes_through_a_symlink() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        fs::write(root.join("config.env"), "MINE=1\n").unwrap();
        older_release(&root, "config.env");
        let victim = tmp.path().join("victim");
        fs::write(&victim, "do not touch").unwrap();
        std::os::unix::fs::symlink(&victim, root.join("config.env.new")).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(fs::read_to_string(&victim).unwrap(), "do not touch");
        let new = root.join("config.env.new");
        assert!(!fs::symlink_metadata(&new).unwrap().file_type().is_symlink());
        assert_eq!(fs::read_to_string(&new).unwrap(), asset("config.env").body);
    }

    #[test]
    fn dry_run_writes_nothing() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), true, false, &out()), 0);
        assert!(!root.exists());
    }

    #[test]
    fn replacing_a_script_leaves_a_running_reader_intact() {
        // The rename-over guarantee the module docs rely on: an open file
        // handle keeps seeing the old content after the path is replaced.
        use std::io::Read;
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("script.sh");
        write_atomic(&p, b"old body\n", 0o755).unwrap();
        let mut held = fs::File::open(&p).unwrap();
        write_atomic(&p, b"new body, different length\n", 0o755).unwrap();
        let mut seen = String::new();
        held.read_to_string(&mut seen).unwrap();
        assert_eq!(seen, "old body\n");
        assert_eq!(
            fs::read_to_string(&p).unwrap(),
            "new body, different length\n"
        );
    }

    #[test]
    fn non_utf8_or_unreadable_seed_is_kept_not_overwritten() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        let seed = root.join("config.env");
        fs::write(&seed, [0xff, 0xfe, b'x', b'\n']).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(fs::read(&seed).unwrap(), vec![0xff, 0xfe, b'x', b'\n']);
        // a dangling symlink still counts as "the user has a file here"
        let wf = root.join("workspace/CLAUDE.md");
        fs::remove_file(&wf).unwrap();
        std::os::unix::fs::symlink(tmp.path().join("nowhere"), &wf).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert!(fs::symlink_metadata(&wf).unwrap().file_type().is_symlink());
    }

    #[test]
    fn script_with_wrong_exec_bits_is_repaired() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        let bin = root.join("bin/sol-agent");
        // identical bytes, but the owner cannot execute it
        fs::set_permissions(&bin, fs::Permissions::from_mode(0o641)).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        assert_eq!(
            fs::metadata(&bin).unwrap().permissions().mode() & 0o777,
            0o755
        );
    }

    #[test]
    fn force_backup_never_writes_through_a_bak_symlink() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().join("agent");
        assert_eq!(cmd_install(Some(root.clone()), false, false, &out()), 0);
        fs::write(root.join("config.env"), "MINE=1\n").unwrap();
        let victim = tmp.path().join("victim");
        fs::write(&victim, "do not touch").unwrap();
        std::os::unix::fs::symlink(&victim, root.join("config.env.bak")).unwrap();
        assert_eq!(cmd_install(Some(root.clone()), false, true, &out()), 0);
        assert_eq!(fs::read_to_string(&victim).unwrap(), "do not touch");
        assert_eq!(
            fs::read_to_string(root.join("config.env.bak")).unwrap(),
            "MINE=1\n"
        );
    }

    #[test]
    fn temp_files_are_random_and_cleaned_up() {
        let tmp = tempfile::tempdir().unwrap();
        let dest = tmp.path().join("x.sh");
        // a planted file with the old predictable name is not used or followed
        let planted = tmp
            .path()
            .join(format!(".x.sh.solx-tmp-{}", std::process::id()));
        let target = tmp.path().join("target");
        fs::write(&target, "keep").unwrap();
        std::os::unix::fs::symlink(&target, &planted).unwrap();
        write_atomic(&dest, b"body", 0o755).unwrap();
        assert_eq!(fs::read_to_string(&target).unwrap(), "keep");
        let strays: Vec<_> = fs::read_dir(tmp.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().starts_with(".solx-tmp-"))
            .collect();
        assert!(strays.is_empty(), "temp file left behind");
    }

    #[test]
    fn relative_and_whitespace_roots() {
        assert!(absolute(PathBuf::from("rel/agent")).is_absolute());
        let tmp = tempfile::tempdir().unwrap();
        let spaced = tmp.path().join("my agent");
        assert_eq!(cmd_install(Some(spaced.clone()), false, false, &out()), 2);
        assert!(!spaced.exists());
    }
}
