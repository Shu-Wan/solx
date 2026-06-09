//! Detect whether the current host is part of the Sol cluster.
//!
//! `solx` is Sol-only. Each subcommand asks [`require_sol`] to enforce the
//! guard — wrong-side invocations exit 2 with a clear redirect rather than
//! attempting to talk to a Slurm controller that isn't there.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const SOL_HOSTNAME_SUFFIX: &str = ".sol.rc.asu.edu";

const NOT_SOL_MESSAGE: &str = "solx is Sol-only — SSH to a Sol login node first, then re-run.\n\
                               See: https://docs.rc.asu.edu/";

/// Return `true` if the current host is on the Sol cluster.
///
/// Looks for any token ending in `.sol.rc.asu.edu` in `hostname -a` output
/// and the kernel hostname.
pub fn is_sol() -> bool {
    matches_sol(&hostname_a())
}

/// Exit 2 with a redirect message if not on Sol. Used by every subcommand.
pub fn require_sol() {
    if !is_sol() {
        eprintln!("{NOT_SOL_MESSAGE}");
        std::process::exit(2);
    }
}

pub fn matches_sol(text: &str) -> bool {
    text.split_whitespace()
        .any(|tok| tok.ends_with(SOL_HOSTNAME_SUFFIX))
}

/// The kernel hostname (FQDN when the node is configured with one).
fn kernel_hostname() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname")
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

/// Run `hostname -a` (2s timeout) and return its output combined with the
/// kernel hostname; fall back to the kernel hostname alone on failure.
fn hostname_a() -> String {
    let fqdn = kernel_hostname();
    let child = Command::new("hostname")
        .arg("-a")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(_) => return fqdn,
    };
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return fqdn;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(_) => return fqdn,
        }
    }
    let mut stdout = String::new();
    if let Some(mut pipe) = child.stdout.take() {
        let _ = pipe.read_to_string(&mut stdout);
    }
    format!("{stdout} {fqdn}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sol_token_anywhere_matches() {
        assert!(matches_sol("scc041.sol.rc.asu.edu"));
        assert!(matches_sol("alias1 login01.sol.rc.asu.edu alias2"));
        assert!(matches_sol(" sc042.sol.rc.asu.edu"));
    }

    #[test]
    fn non_sol_hosts_do_not_match() {
        assert!(!matches_sol("laptop.local"));
        assert!(!matches_sol("phx01.phx.rc.asu.edu"));
        assert!(!matches_sol(""));
        // Suffix must terminate the token.
        assert!(!matches_sol("x.sol.rc.asu.edu.evil.com"));
    }
}
