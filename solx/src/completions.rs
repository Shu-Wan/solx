//! `solx completions <shell>` - emit a static shell completion script.
//!
//! The scripts live under `assets/` and are embedded at build time; they are
//! synced from the Python package's completion generator so both
//! implementations install the same scripts.

use crate::output::py_repr;

const BASH: &str = include_str!("../assets/solx.bash");
const ZSH: &str = include_str!("../assets/_solx.zsh");
const FISH: &str = include_str!("../assets/solx.fish");

/// Print the completion script for `shell`; unknown shells exit 2.
pub fn cmd_completions(shell: &str) -> i32 {
    let shell = shell.to_lowercase();
    let script = match shell.as_str() {
        "bash" => BASH,
        "zsh" => ZSH,
        "fish" => FISH,
        _ => {
            eprintln!(
                "unknown shell {}; choose bash, zsh, or fish.",
                py_repr(&shell)
            );
            return 2;
        }
    };
    print!("{script}");
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scripts_embed_the_command_tree() {
        for script in [BASH, ZSH, FISH] {
            for needle in [
                "solx",
                "init",
                "keep",
                "jump",
                "completions",
                "cheatsheet",
                "config",
            ] {
                assert!(script.contains(needle), "missing {needle}");
            }
        }
        assert!(ZSH.starts_with("#compdef"));
        // fpath/autoload installs need the dual-mode footer.
        assert!(ZSH.contains("loadautofunc"));
        assert!(ZSH.contains("compdef _solx solx"));
    }
}
