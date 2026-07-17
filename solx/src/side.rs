//! Detect whether the current host is part of the Sol cluster.
//!
//! `solx` is Sol-only. Each subcommand asks [`require_sol`] to enforce the
//! guard - wrong-side invocations exit 2 with a clear redirect rather than
//! attempting to talk to a Slurm controller that isn't there.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const SOL_HOSTNAME_SUFFIX: &str = ".sol.rc.asu.edu";

const NOT_SOL_MESSAGE: &str = "solx is Sol-only - SSH to a Sol login node first, then re-run.\n\
                               See: https://docs.rc.asu.edu/";

/// Return `true` if the current host is on the Sol cluster.
///
/// Looks for any token ending in `.sol.rc.asu.edu` in `hostname -a` output
/// and the DNS-resolved FQDN of the kernel hostname.
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

/// The DNS-resolved fully qualified name for this host (Python
/// `socket.getfqdn()` semantics): resolve the kernel hostname to an
/// address, reverse-resolve that address, and take the first of the
/// returned primary name + aliases that contains a dot (else the primary
/// name); the kernel hostname is returned unchanged when resolution fails.
/// On Sol compute nodes the kernel hostname is the short name (e.g.
/// `scc041`) and the resolver supplies the `.sol.rc.asu.edu` form.
fn fqdn() -> String {
    let name = kernel_hostname();
    if name.is_empty() {
        return name;
    }
    match reverse_names(&name) {
        Some((primary, aliases)) => std::iter::once(primary.clone())
            .chain(aliases)
            .find(|n| n.contains('.'))
            .unwrap_or(primary),
        None => name,
    }
}

extern "C" {
    // Not re-exported by the libc crate; the glibc prototype.
    fn gethostbyaddr(
        addr: *const libc::c_void,
        len: libc::socklen_t,
        family: libc::c_int,
    ) -> *mut libc::hostent;
}

/// Resolve `name` forward to its first address, then reverse-resolve the
/// address. Returns the primary host name and its aliases, or `None` when
/// either resolution step fails.
fn reverse_names(name: &str) -> Option<(String, Vec<String>)> {
    use std::ffi::{CStr, CString};

    let c_name = CString::new(name).ok()?;
    let mut hints: libc::addrinfo = unsafe { std::mem::zeroed() };
    hints.ai_family = libc::AF_UNSPEC;
    let mut res: *mut libc::addrinfo = std::ptr::null_mut();
    let rc = unsafe { libc::getaddrinfo(c_name.as_ptr(), std::ptr::null(), &hints, &mut res) };
    if rc != 0 || res.is_null() {
        return None;
    }

    // Extract (address bytes, family) from the first result.
    let addr: Option<(Vec<u8>, libc::c_int)> = unsafe {
        let family = (*res).ai_family;
        let sockaddr = (*res).ai_addr;
        match family {
            libc::AF_INET => {
                let sin = sockaddr as *const libc::sockaddr_in;
                let bytes = (*sin).sin_addr.s_addr.to_ne_bytes().to_vec();
                Some((bytes, family))
            }
            libc::AF_INET6 => {
                let sin6 = sockaddr as *const libc::sockaddr_in6;
                Some(((*sin6).sin6_addr.s6_addr.to_vec(), family))
            }
            _ => None,
        }
    };
    unsafe { libc::freeaddrinfo(res) };
    let (bytes, family) = addr?;

    // glibc gethostbyaddr returns the primary name plus aliases (a
    // getnameinfo lookup yields only one name, which on Sol is the short
    // one - the FQDN arrives as an alias).
    let hostent = unsafe {
        gethostbyaddr(
            bytes.as_ptr() as *const libc::c_void,
            bytes.len() as libc::socklen_t,
            family,
        )
    };
    if hostent.is_null() {
        return None;
    }
    unsafe {
        let h_name = (*hostent).h_name;
        if h_name.is_null() {
            return None;
        }
        let primary = CStr::from_ptr(h_name).to_string_lossy().into_owned();
        let mut aliases = Vec::new();
        let mut p = (*hostent).h_aliases;
        if !p.is_null() {
            while !(*p).is_null() {
                aliases.push(CStr::from_ptr(*p).to_string_lossy().into_owned());
                p = p.add(1);
            }
        }
        Some((primary, aliases))
    }
}

/// Run `hostname -a` (2s timeout) and return its output combined with the
/// resolved FQDN; fall back to the FQDN alone on failure.
fn hostname_a() -> String {
    let fqdn = fqdn();
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
