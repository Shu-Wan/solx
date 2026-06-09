//! `solx init` — write a starter `config.toml` — and the `~/.solkeep`
//! migration behind `solx config import-solkeep`.

use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use serde_json::json;

use crate::config as cfg;
use crate::output::{confirm, Out};

pub const SHELLS: [&str; 3] = ["bash", "zsh", "fish"];

/// Interactive first-run walkthrough. Returns
/// `(default_shell, imported_keep)`, or `None` if declined.
///
/// Steps: pick the login shell `solx job jump` opens, then optionally import
/// an existing `~/.solkeep` into `[keep]`. TTY-only; plain-text prompts on
/// stderr.
#[allow(clippy::type_complexity)]
fn walkthrough(out: &Out, solkeep: &Path) -> Option<(String, Option<(Vec<String>, Vec<String>)>)> {
    if !confirm("Walk through a quick setup?", false) {
        return None;
    }

    // Step 1 — shell (a real choice, so the walkthrough doesn't open with
    // two yes/no questions in a row).
    out.status("\nStep 1 — shell");
    let shell = loop {
        eprint!(
            "Which shell should `solx job jump` open on the compute node? \
             ({}) [bash] ",
            SHELLS.join("/")
        );
        let _ = std::io::stderr().flush();
        let mut line = String::new();
        if std::io::stdin().read_line(&mut line).is_err() {
            break "bash".to_string();
        }
        let answer = line.trim().to_string();
        if answer.is_empty() {
            break "bash".to_string();
        }
        if SHELLS.contains(&answer.as_str()) {
            break answer;
        }
        out.status(&format!("please pick one of: {}", SHELLS.join(", ")));
    };

    // Step 2 — scratch keep-list (only when there's a ~/.solkeep to offer).
    let mut keep = None;
    if let Some((inc, exc)) = cfg::import_solkeep(solkeep) {
        out.status(&format!(
            "\nStep 2 — scratch keep-list  ({}: {} include / {} exclude)",
            solkeep.display(),
            inc.len(),
            exc.len()
        ));
        if confirm("Import it into [keep]?", true) {
            keep = Some((inc, exc));
        }
    }

    Some((shell, keep))
}

pub fn cmd_init(force: bool, solkeep: &Path, out: &Out) -> i32 {
    let p = cfg::config_path();

    if p.exists() && !force {
        // Never block on the overwrite prompt in a non-interactive session.
        if !out.interactive {
            out.error(&format!(
                "error: {} already exists. pass -f to overwrite.",
                p.display()
            ));
            return 2;
        }
        if !confirm(
            &format!("{} already exists. Overwrite?", p.display()),
            false,
        ) {
            out.status("aborted");
            return 1;
        }
    }

    // Optional interactive walkthrough — skipped entirely in a
    // non-interactive session (an agent/cron just gets the defaults, never a
    // hung prompt). The `~/.solkeep` import is one of its prompted steps.
    let mut imported = None;
    let mut default_shell = "bash".to_string();
    if out.interactive {
        if let Some((shell, keep)) = walkthrough(out, solkeep) {
            default_shell = shell;
            imported = keep;
        }
    }

    if let Some(parent) = p.parent() {
        if let Err(e) = std::fs::create_dir_all(parent) {
            out.error(&format!(
                "error: unable to create {}: {e}",
                parent.display()
            ));
            return 1;
        }
    }
    let text = cfg::starter_config_text(imported.as_ref(), &default_shell);
    if let Err(e) = std::fs::write(&p, text) {
        out.error(&format!("error: unable to write {}: {e}", p.display()));
        return 1;
    }
    // Mode 0600 — config may eventually contain user-specific paths or
    // mail-user etc.; keep it readable only by the owner.
    let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600));

    if let Some((inc, exc)) = &imported {
        out.status(&format!(
            "imported {} include / {} exclude pattern(s) into [keep]",
            inc.len(),
            exc.len()
        ));
    }
    out.status("edit it with `solx config edit`, then `solx job start`.");
    out.emit(&json!({"wrote": p.display().to_string()}), || {
        Some(format!("wrote {}", p.display()))
    });
    0
}

/// Migrate a legacy `~/.solkeep` keep-list into the config's `[keep]` block.
///
/// The implicit `~/.solkeep` fallback (and the `.solkeep` format) is
/// deprecated and loses support in a future release (see
/// `keep::SOLKEEP_REMOVED_IN`); this is the one-shot migration. Reads
/// `solkeep` (default `~/.solkeep`), splits it into include/exclude via
/// [`cfg::import_solkeep`], and appends a rendered `[keep]` block to an
/// existing `config.toml`. The merged document is validated before anything
/// is written, so a pattern that can't round-trip through TOML never leaves
/// a corrupt config on disk. Refuses if the config already has an active
/// `[keep]` table — a second one is invalid TOML, so the user must merge by
/// hand there.
///
/// `.solkeep` is gitignore last-match-wins while `[keep]` is
/// include-minus-exclude, so an order-dependent re-include (a positive rule
/// under an earlier `!` carve-out) can't be preserved — the split would
/// renew *fewer* directories, and since `[keep]` then takes precedence over
/// `~/.solkeep`, keeping the old file does not preserve the prior behavior.
/// Such a **lossy** import is **refused** unless `force` is set, so the
/// semantic change is never silent.
pub fn cmd_import_solkeep(solkeep: Option<&Path>, force: bool, out: &Out) -> i32 {
    let p = cfg::config_path();
    let default_src = cfg::home_dir().join(".solkeep");
    let src = solkeep.unwrap_or(&default_src);

    if !p.exists() {
        out.error(&format!(
            "error: no config at {}. run `solx init` first, then re-run this.",
            p.display()
        ));
        return 2;
    }

    let (include, exclude) = match cfg::import_solkeep(src) {
        Some(pair) => pair,
        None => {
            out.error(&format!(
                "error: nothing to import from {} (missing or no patterns).",
                src.display()
            ));
            return 2;
        }
    };

    let existing = match cfg::load(&p) {
        Ok(c) => c,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 2;
        }
    };
    if existing.keep.is_some() {
        out.error(
            "error: config already has a [keep] block. merge the patterns by \
             hand with `solx config edit` (a second [keep] table would be \
             invalid TOML).",
        );
        return 2;
    }

    // A lossy migration (order-dependent re-include) changes which
    // directories get renewed and can't be undone by keeping ~/.solkeep,
    // since [keep] wins. Refuse it unless the user explicitly accepts with
    // -f, so nothing is silently written.
    let lossy = cfg::solkeep_is_order_sensitive(src);
    if lossy && !force {
        out.error(&format!(
            "error: {} re-includes a path under an earlier `!` carve-out. A \
             [keep] block (include minus exclude) can't preserve that \
             ordering, so the migration would renew FEWER directories — and \
             [keep] then takes precedence over ~/.solkeep, so keeping the old \
             file won't preserve current behavior. Compare `solx keep \
             --dry-run` before and after, then re-run with -f to accept the \
             change (or edit the config by hand).",
            src.display()
        ));
        return 2;
    }

    let block = cfg::render_keep_block(&include, &exclude, &src.display().to_string());
    // Validate the merged document before touching the file: a pattern that
    // can't round-trip through TOML must never leave a corrupt config on disk.
    let current = match std::fs::read_to_string(&p) {
        Ok(t) => t,
        Err(e) => {
            out.error(&format!(
                "error: unable to read config at {}: {e}",
                p.display()
            ));
            return 2;
        }
    };
    let new_text = format!("{}\n\n{block}", current.trim_end_matches('\n'));
    if let Err(e) = new_text.parse::<toml::Table>() {
        out.error(&format!(
            "error: importing these patterns would produce invalid TOML \
             ({e}); config left unchanged. Fix {} or run `solx config edit`.",
            src.display()
        ));
        return 1;
    }
    if let Err(e) = std::fs::write(&p, &new_text) {
        out.error(&format!("error: unable to write {}: {e}", p.display()));
        return 1;
    }

    out.status(&format!(
        "imported {} include / {} exclude pattern(s) into [keep]",
        include.len(),
        exclude.len()
    ));
    if lossy {
        // Only reachable with -f.
        out.status(&format!(
            "warning: ordering not preserved (re-include under a `!` \
             carve-out) — verify with `solx keep --dry-run` against the old \
             {} and adjust the [keep] block if it renews too little.",
            src.display()
        ));
    } else {
        out.status(
            "review with `solx config show`, then verify with `solx keep \
             --dry-run` before removing the old keep-list.",
        );
    }
    out.emit(
        &json!({
            "config": p.display().to_string(),
            "include": include,
            "exclude": exclude,
        }),
        || Some(format!("wrote [keep] → {}", p.display())),
    );
    0
}
