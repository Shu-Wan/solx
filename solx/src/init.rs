//! `solx init` - write a starter `config.toml`.

use std::io::Write;
use std::os::unix::fs::PermissionsExt;

use serde_json::json;

use crate::config as cfg;
use crate::output::{confirm, Out};

pub const SHELLS: [&str; 3] = ["bash", "zsh", "fish"];

/// Interactive first-run walkthrough. Returns the chosen `default_shell`, or
/// `None` if declined. TTY-only; plain-text prompts on stderr.
fn walkthrough(out: &Out) -> Option<String> {
    if !confirm("Walk through a quick setup?", false) {
        return None;
    }

    out.status("\nShell");
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

    Some(shell)
}

pub fn cmd_init(force: bool, out: &Out) -> i32 {
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

    // Optional interactive walkthrough - skipped entirely in a
    // non-interactive session (an agent/cron just gets the defaults, never a
    // hung prompt).
    let mut default_shell = "bash".to_string();
    if out.interactive {
        if let Some(shell) = walkthrough(out) {
            default_shell = shell;
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
    let text = cfg::starter_config_text(&default_shell);
    if let Err(e) = std::fs::write(&p, text) {
        out.error(&format!("error: unable to write {}: {e}", p.display()));
        return 1;
    }
    // Mode 0600 - config may eventually contain user-specific paths or
    // mail-user etc.; keep it readable only by the owner.
    let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600));

    out.status("edit it with `solx config edit`, then `solx job start`.");
    out.emit(&json!({"wrote": p.display().to_string()}), || {
        Some(format!("wrote {}", p.display()))
    });
    0
}
