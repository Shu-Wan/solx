//! Thin wrappers around `squeue`, `scancel`, `salloc`, and `srun`.
//!
//! Not a Slurm client library — every function shells out and parses the
//! result. Tests inject a [`Runner`] so they can mock subprocess output
//! without spawning anything.

use std::collections::HashMap;
use std::fmt;
use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use crate::config::JobTemplate;
use crate::output::py_repr;

/// A runner takes argv and returns (returncode, stdout, stderr).
pub type Runner<'a> = &'a dyn Fn(&[String]) -> (i32, String, String);

/// One row of `squeue -u $USER`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Job {
    pub job_id: String,
    pub name: String,
    pub state: String,
    pub time_used: String,
    pub time_left: String,
    pub partition: String,
    pub node_list: String,
}

impl Job {
    /// Parse one `squeue` pipe-delimited row (field order set by
    /// [`squeue_user_jobs`]'s format string).
    pub fn from_squeue_row(line: &str) -> Result<Job, SlurmError> {
        let parts: Vec<&str> = line.split('|').collect();
        if parts.len() < 7 {
            return Err(SlurmError(format!(
                "unexpected squeue row: {}",
                py_repr(line)
            )));
        }
        Ok(Job {
            job_id: parts[0].to_string(),
            name: parts[1].to_string(),
            state: parts[2].to_string(),
            time_used: parts[3].to_string(),
            time_left: parts[4].to_string(),
            partition: parts[5].to_string(),
            node_list: parts[6].to_string(),
        })
    }
}

/// Any Slurm-side failure surfaced to the user.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlurmError(pub String);

impl fmt::Display for SlurmError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SlurmError {}

/// Default runner: a real subprocess with captured output.
pub fn real_runner(argv: &[String]) -> (i32, String, String) {
    let result = Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .output();
    match result {
        Ok(out) => (
            out.status.code().unwrap_or(1),
            String::from_utf8_lossy(&out.stdout).into_owned(),
            String::from_utf8_lossy(&out.stderr).into_owned(),
        ),
        Err(e) => (1, String::new(), format!("{}: {e}", argv[0])),
    }
}

// --- squeue ---------------------------------------------------------------

const SQUEUE_FORMAT: &str = "%i|%j|%T|%M|%L|%P|%R";

/// Return the user's current jobs (running, pending, etc.).
pub fn squeue_user_jobs(user: Option<&str>, runner: Runner) -> Result<Vec<Job>, SlurmError> {
    let user = match user {
        Some(u) => u.to_string(),
        None => std::env::var("USER").unwrap_or_default(),
    };
    let argv: Vec<String> = ["squeue", "-u", &user, "-h", "-o", SQUEUE_FORMAT]
        .iter()
        .map(|s| s.to_string())
        .collect();
    let (code, out, err) = runner(&argv);
    if code != 0 {
        let detail = if err.trim().is_empty() {
            out.trim().to_string()
        } else {
            err.trim().to_string()
        };
        return Err(SlurmError(format!("squeue failed: {detail}")));
    }
    out.lines()
        .filter(|line| !line.trim().is_empty())
        .map(Job::from_squeue_row)
        .collect()
}

// --- jobid resolution -----------------------------------------------------
//
// Resolution is VERB-AWARE. The conventions are inspired by tmux (a no-arg
// command acts on the obvious target; "most recent" when several exist; warn
// when you act on the session you're sitting in) but adapted to Slurm, where
// a cancelled job is unrecoverable and attaching spends real allocation time:
//
//   * `time`/`jump` (read / attach): when several jobs match, auto-pick the
//     MOST RECENT one (like `tmux attach`). Deterministic, so it's agent-safe.
//   * `stop` (cancel): NEVER auto-picks among several — that's how you cancel
//     the wrong job. It returns the candidates so the caller can print them
//     and exit 2.
//   * `jump`'s auto-pick considers RUNNING jobs only (you can't attach to a
//     pending one). An EXPLICIT arg or $SLURM_JOB_ID is passed through as-is
//     (no state pre-check) — `srun` surfaces a wrong-state job far more
//     clearly than we could, and it saves a squeue round-trip.
//
// "Inside an allocation" ($SLURM_JOB_ID set) is treated as "the current
// session": it's the default target, and acting on it carries a nesting /
// self-cancel warning the caller surfaces.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verb {
    Jump,
    Stop,
    Time,
}

/// Outcome of resolving a jobid for one verb.
///
/// Exactly one of these holds:
/// * `job_id` is set     → resolved; act on it.
/// * `ambiguous` is true → several candidates, caller must disambiguate.
/// * `error` is set      → nothing to act on (no jobs / none running).
#[derive(Debug, Clone, Default)]
pub struct Resolution {
    pub job_id: Option<String>,
    /// "arg" | "inside" | "single" | "most-recent"
    pub source: &'static str,
    /// $SLURM_JOB_ID is set (acting from within an allocation).
    pub inside: bool,
    pub inside_job_id: Option<String>,
    /// Set considered (for ambiguity / context).
    pub candidates: Vec<Job>,
    pub ambiguous: bool,
    pub error: Option<String>,
}

impl Resolution {
    /// True when the resolved job is the one we're sitting inside.
    pub fn acting_on_current(&self) -> bool {
        self.inside && self.job_id.is_some() && self.job_id == self.inside_job_id
    }
}

/// Sort key making "most recent" == "highest job id".
///
/// Slurm assigns monotonically increasing ids, so the highest id is the
/// newest submission — which for `solx job start` is the one you just made.
/// Array ids like `123_4` sort by (base, index); a non-numeric id sorts
/// first so a real number always wins.
fn jobid_key(job_id: &str) -> (i64, i64) {
    let (base, idx) = match job_id.split_once('_') {
        Some((b, i)) => (b, i),
        None => (job_id, ""),
    };
    match base.parse::<i64>() {
        Ok(b) => {
            let i = if !idx.is_empty() && idx.bytes().all(|c| c.is_ascii_digit()) {
                idx.parse().unwrap_or(0)
            } else {
                0
            };
            (b, i)
        }
        Err(_) => (-1, 0),
    }
}

/// Return the most recently submitted job (highest job id).
/// Ties keep the first occurrence. Panics on an empty slice (callers
/// guarantee at least one candidate).
pub fn most_recent(jobs: &[Job]) -> &Job {
    let mut best = &jobs[0];
    let mut best_key = jobid_key(&best.job_id);
    for j in &jobs[1..] {
        let k = jobid_key(&j.job_id);
        if k > best_key {
            best = j;
            best_key = k;
        }
    }
    best
}

/// Resolve the jobid for `stop` / `jump` / `time`, verb-aware (see above).
///
/// Order: explicit arg > inside-allocation ($SLURM_JOB_ID) > squeue. From
/// squeue, a single candidate is used; several are auto-resolved to the most
/// recent for read/attach verbs, or returned as `ambiguous` for stop.
///
/// Errors if the squeue query fails (the explicit-arg and inside-allocation
/// paths short-circuit before any squeue call, so they never do). `env`
/// substitutes for the process environment in tests; `None` reads the real
/// one.
pub fn resolve_jobid(
    arg: Option<&str>,
    verb: Verb,
    user: Option<&str>,
    env: Option<&HashMap<String, String>>,
    runner: Runner,
) -> Result<Resolution, SlurmError> {
    let inside_id: Option<String> = match env {
        Some(map) => map.get("SLURM_JOB_ID").cloned(),
        None => std::env::var("SLURM_JOB_ID").ok(),
    }
    .filter(|v| !v.is_empty());
    let inside = inside_id.is_some();

    if let Some(a) = arg.filter(|a| !a.is_empty()) {
        return Ok(Resolution {
            job_id: Some(a.to_string()),
            source: "arg",
            inside,
            inside_job_id: inside_id,
            ..Default::default()
        });
    }
    if let Some(id) = inside_id.clone() {
        return Ok(Resolution {
            job_id: Some(id),
            source: "inside",
            inside: true,
            inside_job_id: inside_id,
            ..Default::default()
        });
    }

    let jobs = squeue_user_jobs(user, runner)?;
    let candidates: Vec<Job> = if verb == Verb::Jump {
        jobs.iter()
            .filter(|j| j.state == "RUNNING")
            .cloned()
            .collect()
    } else {
        jobs.clone()
    };

    if candidates.is_empty() {
        // For jump, distinguish "you have jobs but none running" from "no jobs".
        let err = if verb == Verb::Jump && !jobs.is_empty() {
            "no running job to attach to (jobs exist but none are RUNNING)"
        } else {
            "no jobs found for the current user"
        };
        return Ok(Resolution {
            error: Some(err.to_string()),
            candidates: jobs,
            inside,
            ..Default::default()
        });
    }

    if candidates.len() == 1 {
        return Ok(Resolution {
            job_id: Some(candidates[0].job_id.clone()),
            source: "single",
            candidates,
            inside,
            inside_job_id: inside_id,
            ..Default::default()
        });
    }

    if verb == Verb::Stop {
        // Never auto-pick which job to cancel.
        return Ok(Resolution {
            ambiguous: true,
            candidates,
            inside,
            inside_job_id: inside_id,
            ..Default::default()
        });
    }

    let chosen = most_recent(&candidates).job_id.clone();
    Ok(Resolution {
        job_id: Some(chosen),
        source: "most-recent",
        candidates,
        inside,
        inside_job_id: inside_id,
        ..Default::default()
    })
}

// --- salloc / scancel / srun argv builders ---------------------------------

/// Build the argv for `salloc --no-shell` from a template + CLI passthrough.
pub fn salloc_argv(template: &JobTemplate, passthrough: &[String]) -> Vec<String> {
    let mut argv = vec![
        "salloc".to_string(),
        "--no-shell".to_string(),
        "-J".to_string(),
        format!("solx-{}", template.name),
        "-p".to_string(),
        template.partition.clone(),
        "-t".to_string(),
        template.time.clone(),
    ];
    if let Some(qos) = &template.qos {
        argv.push("-q".to_string());
        argv.push(qos.clone());
    }
    if let Some(gres) = &template.gres {
        argv.push(format!("--gres={gres}"));
    }
    argv.extend(template.extra_args.iter().cloned());
    argv.extend(passthrough.iter().cloned());
    argv
}

pub fn scancel_argv(job_id: &str) -> Vec<String> {
    vec!["scancel".to_string(), job_id.to_string()]
}

/// Argv for attaching a pty shell to a running allocation.
///
/// `--overlap` lets the step share the allocation's resources with steps
/// already running in it. Without it, srun demands exclusive use of the node
/// and stalls with "step creation temporarily disabled (Requested nodes are
/// busy)" whenever the job already has a step occupying its resources.
pub fn srun_pty_argv(job_id: &str, shell: &str) -> Vec<String> {
    vec![
        "srun".to_string(),
        format!("--jobid={job_id}"),
        "--overlap".to_string(),
        "--pty".to_string(),
        shell.to_string(),
    ]
}

pub fn squeue_time_left_argv(job_id: &str) -> Vec<String> {
    ["squeue", "-h", "-j", job_id, "-O", "TimeLeft"]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

// --- salloc execution -------------------------------------------------------

/// Extract the jobid from `salloc`'s stderr `Granted job allocation N` line.
pub fn parse_granted_jobid(stderr_text: &str) -> Result<String, SlurmError> {
    const NEEDLE: &str = "Granted job allocation ";
    let mut search = stderr_text;
    while let Some(pos) = search.find(NEEDLE) {
        let after = &search[pos + NEEDLE.len()..];
        let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !digits.is_empty() {
            return Ok(digits);
        }
        search = after;
    }
    Err(SlurmError(format!(
        "could not parse jobid from salloc output:\n{stderr_text}"
    )))
}

/// Join argv for display, quoting like Python's `shlex.join`: a token is
/// quoted only when it contains a character outside `[A-Za-z0-9_@%+=:,./-]`
/// (so `=`-style flags like `--gres=gpu:a100:1` stay bare), using single
/// quotes with embedded `'` rendered as `'"'"'`.
pub fn shell_join(argv: &[String]) -> String {
    argv.iter()
        .map(|s| shlex_quote(s))
        .collect::<Vec<_>>()
        .join(" ")
}

fn shlex_quote(s: &str) -> String {
    let safe = |c: char| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c);
    if !s.is_empty() && s.chars().all(safe) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\"'\"'"))
    }
}

/// Invoke salloc and return the granted jobid.
///
/// `salloc --no-shell` blocks until the allocation lands, then exits. If the
/// queue stalls beyond `timeout_seconds`, the process is killed and a
/// [`SlurmError`] surfaces a clear timeout instead of a hang. A `runner`
/// (tests) bypasses the subprocess and timeout entirely.
pub fn run_salloc(
    argv: &[String],
    timeout_seconds: i64,
    runner: Option<Runner>,
) -> Result<String, SlurmError> {
    if let Some(run) = runner {
        let (code, _, err) = run(argv);
        if code != 0 {
            return Err(SlurmError(format!("salloc failed: {}", err.trim())));
        }
        return parse_granted_jobid(&err);
    }

    let timeout_err = || {
        SlurmError(format!(
            "salloc timed out after {timeout_seconds}s waiting for the queue. \
             Cancel the request manually if needed; the request may still be \
             queued. Argv: {}",
            shell_join(argv)
        ))
    };

    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| SlurmError(format!("salloc failed: {e}")))?;

    // Drain the pipes on threads so a chatty salloc can't dead-lock against
    // a full pipe buffer while we poll for exit.
    let mut stdout_pipe = child.stdout.take().expect("stdout piped");
    let mut stderr_pipe = child.stderr.take().expect("stderr piped");
    let out_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout_pipe.read_to_end(&mut buf);
        buf
    });
    let err_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr_pipe.read_to_end(&mut buf);
        buf
    });

    let deadline = Instant::now() + Duration::from_secs(timeout_seconds.max(0) as u64);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(timeout_err());
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(e) => return Err(SlurmError(format!("salloc failed: {e}"))),
        }
    };
    let _stdout = out_thread.join().unwrap_or_default();
    let stderr = String::from_utf8_lossy(&err_thread.join().unwrap_or_default()).into_owned();

    if !status.success() {
        return Err(SlurmError(format!(
            "salloc failed (exit {}):\n{}",
            status.code().unwrap_or(1),
            stderr.trim()
        )));
    }
    parse_granted_jobid(&stderr)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn runner_of(
        code: i32,
        stdout: &str,
        stderr: &str,
    ) -> impl Fn(&[String]) -> (i32, String, String) {
        let stdout = stdout.to_string();
        let stderr = stderr.to_string();
        move |_argv: &[String]| (code, stdout.clone(), stderr.clone())
    }

    fn env(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    fn job(id: &str) -> Job {
        Job {
            job_id: id.to_string(),
            name: "a".to_string(),
            state: "RUNNING".to_string(),
            time_used: String::new(),
            time_left: String::new(),
            partition: "p".to_string(),
            node_list: String::new(),
        }
    }

    // ---- squeue ------------------------------------------------------------

    #[test]
    fn squeue_user_jobs_parses_rows() {
        let out = "12345|solx-default|RUNNING|00:05:23|00:54:37|lightwork|sg045\n\
                   12346|notebook|PENDING|00:00:00|01:00:00|htc|(Resources)\n";
        let captured: RefCell<Vec<Vec<String>>> = RefCell::new(Vec::new());
        let runner = |argv: &[String]| {
            captured.borrow_mut().push(argv.to_vec());
            (0, out.to_string(), String::new())
        };
        let jobs = squeue_user_jobs(Some("sparky"), &runner).unwrap();
        assert_eq!(jobs.len(), 2);
        assert_eq!(
            jobs[0],
            Job {
                job_id: "12345".to_string(),
                name: "solx-default".to_string(),
                state: "RUNNING".to_string(),
                time_used: "00:05:23".to_string(),
                time_left: "00:54:37".to_string(),
                partition: "lightwork".to_string(),
                node_list: "sg045".to_string(),
            }
        );
        let argv = &captured.borrow()[0];
        assert!(argv.contains(&"-u".to_string()) && argv.contains(&"sparky".to_string()));
    }

    #[test]
    fn squeue_user_jobs_empty() {
        let runner = runner_of(0, "", "");
        assert!(squeue_user_jobs(Some("sparky"), &runner)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn squeue_user_jobs_failure() {
        let runner = runner_of(1, "", "slurmctld is down");
        let err = squeue_user_jobs(Some("sparky"), &runner).unwrap_err();
        assert_eq!(err.0, "squeue failed: slurmctld is down");
    }

    #[test]
    fn squeue_row_too_short_is_error() {
        let err = Job::from_squeue_row("only|three|fields").unwrap_err();
        assert!(err.0.starts_with("unexpected squeue row: "));
    }

    // ---- resolve_jobid -----------------------------------------------------

    const TWO_RUNNING: &str = "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n\
                               67890|notebook|RUNNING|00:01:00|00:59:00|htc|sg010\n";

    #[test]
    fn resolve_arg_wins() {
        let called = RefCell::new(false);
        let runner = |_argv: &[String]| {
            *called.borrow_mut() = true;
            (0, String::new(), String::new())
        };
        let res = resolve_jobid(
            Some("99999"),
            Verb::Stop,
            None,
            Some(&env(&[("SLURM_JOB_ID", "11111")])),
            &runner,
        )
        .unwrap();
        assert_eq!(res.job_id.as_deref(), Some("99999"));
        assert_eq!(res.source, "arg");
        assert!(res.inside);
        assert_eq!(res.inside_job_id.as_deref(), Some("11111"));
        assert!(!*called.borrow()); // never queried squeue
    }

    #[test]
    fn resolve_uses_env_on_compute_node() {
        let called = RefCell::new(false);
        let runner = |_argv: &[String]| {
            *called.borrow_mut() = true;
            (0, String::new(), String::new())
        };
        let res = resolve_jobid(
            None,
            Verb::Time,
            None,
            Some(&env(&[("SLURM_JOB_ID", "55555")])),
            &runner,
        )
        .unwrap();
        assert_eq!(res.job_id.as_deref(), Some("55555"));
        assert_eq!(res.source, "inside");
        assert!(res.acting_on_current());
        assert!(!*called.borrow());
    }

    #[test]
    fn resolve_single_running_job() {
        let runner = runner_of(
            0,
            "12345|solx-default|RUNNING|00:01:00|00:59:00|lightwork|sg045\n",
            "",
        );
        let res =
            resolve_jobid(None, Verb::Stop, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        assert_eq!(res.job_id.as_deref(), Some("12345"));
        assert_eq!(res.source, "single");
        assert!(!res.ambiguous);
    }

    #[test]
    fn resolve_zero_jobs() {
        let runner = runner_of(0, "", "");
        let res =
            resolve_jobid(None, Verb::Time, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        assert!(res.job_id.is_none());
        assert!(res.error.as_deref().unwrap().contains("no jobs found"));
    }

    #[test]
    fn resolve_stop_ambiguous_no_autopick() {
        let runner = runner_of(0, TWO_RUNNING, "");
        let res =
            resolve_jobid(None, Verb::Stop, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        assert!(res.job_id.is_none());
        assert!(res.ambiguous);
        let ids: Vec<&str> = res.candidates.iter().map(|j| j.job_id.as_str()).collect();
        assert_eq!(ids, ["12345", "67890"]);
    }

    #[test]
    fn resolve_time_picks_most_recent() {
        let runner = runner_of(0, TWO_RUNNING, "");
        let res =
            resolve_jobid(None, Verb::Time, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        assert_eq!(res.job_id.as_deref(), Some("67890")); // highest jobid == most recent
        assert_eq!(res.source, "most-recent");
        assert!(!res.ambiguous);
    }

    #[test]
    fn resolve_jump_filters_running_only() {
        let out = "12345|a|RUNNING|00:01|00:59|p|sg045\n\
                   67890|b|PENDING|00:00|01:00|p|(Resources)\n";
        let runner = runner_of(0, out, "");
        let res =
            resolve_jobid(None, Verb::Jump, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        // Only the RUNNING job is an attach candidate -> unambiguous.
        assert_eq!(res.job_id.as_deref(), Some("12345"));
        assert_eq!(res.source, "single");
    }

    #[test]
    fn resolve_jump_no_running() {
        let runner = runner_of(0, "67890|b|PENDING|00:00|01:00|p|(Resources)\n", "");
        let res =
            resolve_jobid(None, Verb::Jump, Some("sparky"), Some(&env(&[])), &runner).unwrap();
        assert!(res.job_id.is_none());
        assert!(res.error.as_deref().unwrap().contains("no running job"));
    }

    #[test]
    fn resolve_squeue_failure_propagates() {
        let runner = runner_of(1, "", "boom");
        let err =
            resolve_jobid(None, Verb::Time, Some("sparky"), Some(&env(&[])), &runner).unwrap_err();
        assert_eq!(err.0, "squeue failed: boom");
    }

    #[test]
    fn resolve_empty_slurm_job_id_is_not_inside() {
        let runner = runner_of(0, "12345|a|RUNNING|0:01|0:59|p|sg045\n", "");
        let res = resolve_jobid(
            None,
            Verb::Time,
            Some("sparky"),
            Some(&env(&[("SLURM_JOB_ID", "")])),
            &runner,
        )
        .unwrap();
        assert_eq!(res.source, "single");
        assert!(!res.inside);
    }

    #[test]
    fn most_recent_highest_jobid() {
        let jobs = vec![job("100"), job("9999"), job("250")];
        assert_eq!(most_recent(&jobs).job_id, "9999");
    }

    #[test]
    fn most_recent_array_ids() {
        let jobs = vec![job("100_1"), job("100_7")];
        assert_eq!(most_recent(&jobs).job_id, "100_7");
    }

    #[test]
    fn most_recent_non_numeric_sorts_first() {
        let jobs = vec![job("abc"), job("5")];
        assert_eq!(most_recent(&jobs).job_id, "5");
    }

    // ---- argv builders -----------------------------------------------------

    #[test]
    fn salloc_argv_minimal() {
        let t = JobTemplate {
            name: "default".to_string(),
            partition: "lightwork".to_string(),
            time: "1-0".to_string(),
            qos: None,
            gres: None,
            extra_args: vec![],
        };
        assert_eq!(
            salloc_argv(&t, &[]),
            [
                "salloc",
                "--no-shell",
                "-J",
                "solx-default",
                "-p",
                "lightwork",
                "-t",
                "1-0"
            ]
        );
    }

    #[test]
    fn salloc_argv_full() {
        let t = JobTemplate {
            name: "gpu".to_string(),
            partition: "public".to_string(),
            time: "0-4".to_string(),
            qos: Some("public".to_string()),
            gres: Some("gpu:a100:1".to_string()),
            extra_args: vec!["--mem=64G".to_string(), "--cpus-per-task=8".to_string()],
        };
        assert_eq!(
            salloc_argv(&t, &["--mail-type=END".to_string()]),
            [
                "salloc",
                "--no-shell",
                "-J",
                "solx-gpu",
                "-p",
                "public",
                "-t",
                "0-4",
                "-q",
                "public",
                "--gres=gpu:a100:1",
                "--mem=64G",
                "--cpus-per-task=8",
                "--mail-type=END",
            ]
        );
    }

    #[test]
    fn scancel_argv_shape() {
        assert_eq!(scancel_argv("12345"), ["scancel", "12345"]);
    }

    #[test]
    fn srun_pty_argv_shape() {
        // --overlap lets the step share the allocation's busy resources.
        assert_eq!(
            srun_pty_argv("12345", "zsh"),
            ["srun", "--jobid=12345", "--overlap", "--pty", "zsh"]
        );
    }

    #[test]
    fn squeue_time_left_argv_shape() {
        assert_eq!(
            squeue_time_left_argv("12345"),
            ["squeue", "-h", "-j", "12345", "-O", "TimeLeft"]
        );
    }

    // ---- salloc parse + run ------------------------------------------------

    #[test]
    fn parse_granted_jobid_ok() {
        let text = "salloc: Pending job allocation 51642835\n\
                    salloc: job 51642835 queued and waiting for resources\n\
                    salloc: job 51642835 has been allocated resources\n\
                    salloc: Granted job allocation 51642835\n";
        assert_eq!(parse_granted_jobid(text).unwrap(), "51642835");
    }

    #[test]
    fn parse_granted_jobid_missing() {
        let err = parse_granted_jobid("salloc: error: queue down\n").unwrap_err();
        assert!(err.0.starts_with("could not parse"));
    }

    #[test]
    fn run_salloc_success_via_runner() {
        let captured: RefCell<Vec<Vec<String>>> = RefCell::new(Vec::new());
        let runner = |argv: &[String]| {
            captured.borrow_mut().push(argv.to_vec());
            (
                0,
                String::new(),
                "salloc: Granted job allocation 99999\n".to_string(),
            )
        };
        let argv: Vec<String> = vec!["salloc".to_string(), "--no-shell".to_string()];
        let jid = run_salloc(&argv, 60, Some(&runner)).unwrap();
        assert_eq!(jid, "99999");
        assert_eq!(captured.borrow()[0], argv);
    }

    #[test]
    fn run_salloc_failure_via_runner() {
        let runner = runner_of(1, "", "salloc: error: invalid partition\n");
        let err = run_salloc(&["salloc".to_string()], 60, Some(&runner)).unwrap_err();
        assert!(err.0.contains("invalid partition"));
    }

    #[test]
    fn shell_join_plain_tokens() {
        let argv: Vec<String> = ["salloc", "--no-shell", "-J", "solx-default"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(shell_join(&argv), "salloc --no-shell -J solx-default");
    }

    #[test]
    fn shell_join_keeps_equals_tokens_bare() {
        // The gpu-template argv: every `=`/`:`-bearing token stays unquoted,
        // matching Python's shlex.join.
        let argv: Vec<String> = [
            "salloc",
            "--no-shell",
            "-J",
            "solx-gpu",
            "-p",
            "public",
            "-t",
            "0-4",
            "--gres=gpu:a100:1",
            "--mem=64G",
            "--cpus-per-task=8",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert_eq!(
            shell_join(&argv),
            "salloc --no-shell -J solx-gpu -p public -t 0-4 \
             --gres=gpu:a100:1 --mem=64G --cpus-per-task=8"
        );
    }

    #[test]
    fn shell_join_quotes_unsafe_tokens() {
        let argv: Vec<String> = ["echo", "a b", "", "it's", "a*b"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(shell_join(&argv), r#"echo 'a b' '' 'it'"'"'s' 'a*b'"#);
    }
}
