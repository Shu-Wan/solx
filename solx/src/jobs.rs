//! `solx job` subcommands: list, start, stop, jump, time.
//!
//! Output obeys [`crate::output::Out`]: JSON on a non-TTY stdout, plain
//! tables on a TTY, all diagnostics on stderr. Jobid resolution is
//! verb-aware (see [`crate::slurm::resolve_jobid`]): read/attach verbs
//! auto-pick the most recent job, the destructive `stop` never does, and
//! acting from inside an allocation carries a nesting / self-cancel guard.

use serde_json::{json, Value};

use crate::config::Config;
use crate::output::Out;
use crate::slurm::{self, Job, Runner, Verb};

// --- shared rendering -------------------------------------------------------

const JOB_COLUMNS: [&str; 7] = [
    "JOBID",
    "NAME",
    "STATE",
    "TIME",
    "LEFT",
    "PARTITION",
    "NODE / REASON",
];

/// Render jobs as plain aligned columns (kubectl-style) for a TTY.
fn jobs_table(jobs: &[Job]) -> String {
    let rows: Vec<[&str; 7]> = jobs
        .iter()
        .map(|j| {
            [
                j.job_id.as_str(),
                j.name.as_str(),
                j.state.as_str(),
                j.time_used.as_str(),
                j.time_left.as_str(),
                j.partition.as_str(),
                j.node_list.as_str(),
            ]
        })
        .collect();
    let mut widths: Vec<usize> = JOB_COLUMNS.iter().map(|c| c.len()).collect();
    for row in &rows {
        for (i, cell) in row.iter().enumerate() {
            widths[i] = widths[i].max(cell.chars().count());
        }
    }
    let render = |cells: &[&str; 7]| -> String {
        let mut line = String::new();
        for (i, cell) in cells.iter().enumerate() {
            if i > 0 {
                line.push_str("  ");
            }
            line.push_str(cell);
            if i + 1 < cells.len() {
                for _ in cell.chars().count()..widths[i] {
                    line.push(' ');
                }
            }
        }
        line.trim_end().to_string()
    };
    let mut out = vec![render(&JOB_COLUMNS)];
    out.extend(rows.iter().map(render));
    out.join("\n")
}

fn jobs_payload(jobs: &[Job]) -> Value {
    Value::Array(
        jobs.iter()
            .map(|j| {
                json!({
                    "job_id": j.job_id,
                    "name": j.name,
                    "state": j.state,
                    "time_used": j.time_used,
                    "time_left": j.time_left,
                    "partition": j.partition,
                    "node_list": j.node_list,
                })
            })
            .collect(),
    )
}

/// Surface a candidate set for a verb that won't auto-pick (stop).
fn print_candidates(out: &Out, jobs: &[Job], reason: &str) {
    if out.json_mode {
        out.json(&json!({"error": reason, "jobs": jobs_payload(jobs)}));
    } else {
        out.error(&format!("{reason} - specify a JOBID:"));
        out.error(&jobs_table(jobs));
    }
}

// --- list --------------------------------------------------------------------

pub fn cmd_list(runner: Runner, out: &Out) -> i32 {
    let jobs = match slurm::squeue_user_jobs(None, runner) {
        Ok(jobs) => jobs,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };
    out.emit(&jobs_payload(&jobs), || {
        Some(if jobs.is_empty() {
            "no jobs in queue".to_string()
        } else {
            jobs_table(&jobs)
        })
    });
    0
}

// --- start ---------------------------------------------------------------------

/// The `job start` tail, parsed Click-style (see [`parse_start_tail`]).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct StartTail {
    pub template: Option<String>,
    pub dry_run: bool,
    pub timeout: Option<String>,
    pub passthrough: Vec<String>,
    pub help: bool,
}

/// Parse everything after `job start`.
///
/// The grammar matches an ignore-unknown-options + allow-extra-args command:
///
/// * `-n` / `--dry-run`, `-h` / `--help`, and `--timeout VALUE` (or
///   `--timeout=VALUE`) are consumed wherever they appear before the first
///   `--`. An explicit value on the flag form (`--dry-run=...`) is a usage
///   error.
/// * The first `--` is dropped; everything after it is treated as bare
///   tokens (no option parsing).
/// * The first unconsumed bare token - even one after `--` that looks like
///   a flag - becomes the TEMPLATE; every other leftover token is salloc
///   passthrough, in original order.
pub fn parse_start_tail(args: &[String]) -> Result<StartTail, String> {
    let mut tail = StartTail::default();
    let mut leftovers: Vec<String> = Vec::new();
    let mut after_dashdash = false;
    let mut i = 0;
    while i < args.len() {
        let tok = &args[i];
        if after_dashdash {
            leftovers.push(tok.clone());
            i += 1;
            continue;
        }
        if tok == "--" {
            after_dashdash = true;
            i += 1;
            continue;
        }
        if tok == "-n" || tok == "--dry-run" {
            tail.dry_run = true;
            i += 1;
            continue;
        }
        if tok.starts_with("--dry-run=") {
            return Err("Option '--dry-run' does not take a value.".to_string());
        }
        if tok == "-h" || tok == "--help" {
            tail.help = true;
            i += 1;
            continue;
        }
        if tok == "--timeout" {
            let value = args
                .get(i + 1)
                .ok_or("Option '--timeout' requires an argument.")?;
            tail.timeout = Some(value.clone());
            i += 2;
            continue;
        }
        if let Some(v) = tok.strip_prefix("--timeout=") {
            tail.timeout = Some(v.to_string());
            i += 1;
            continue;
        }
        if tok.starts_with('-') && tok.len() > 1 && !tok.starts_with("--") {
            // A short-option cluster: peel known shorts, keep the rest.
            let mut unknown = String::new();
            for c in tok.chars().skip(1) {
                if c == 'n' {
                    tail.dry_run = true;
                } else {
                    unknown.push(c);
                }
            }
            if !unknown.is_empty() {
                leftovers.push(format!("-{unknown}"));
            }
            i += 1;
            continue;
        }
        // Unknown long option or bare token: leave it for template/passthrough.
        leftovers.push(tok.clone());
        i += 1;
    }
    let mut it = leftovers.into_iter();
    tail.template = it.next();
    tail.passthrough = it.collect();
    Ok(tail)
}

pub fn cmd_start(
    config: &Config,
    template_name: Option<&str>,
    dry_run: bool,
    timeout_override: Option<i64>,
    passthrough: &[String],
    salloc_runner: Option<Runner>,
    out: &Out,
) -> i32 {
    let name = template_name
        .unwrap_or(&config.default_template)
        .to_string();
    let template = match config.template(&name) {
        Ok(t) => t,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };

    let argv = slurm::salloc_argv(template, passthrough);

    if dry_run {
        out.status("dry-run - would run:");
        out.emit(
            &json!({"dry_run": true, "template": name, "argv": argv}),
            || Some(format!("  {}", slurm::shell_join(&argv))),
        );
        return 0;
    }

    let timeout = timeout_override.unwrap_or(config.start_timeout_seconds);
    out.status(&format!("submitting: {}", slurm::shell_join(&argv)));
    out.status(&format!(
        "waiting up to {timeout}s for the queue to grant the allocation..."
    ));
    let jobid = match slurm::run_salloc(&argv, timeout, salloc_runner) {
        Ok(j) => j,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };

    out.status(&format!("allocated job {jobid}"));
    let attach_cmd = slurm::shell_join(&slurm::srun_pty_argv(&jobid, &config.default_shell));
    out.status(&format!(
        "attach: solx job jump {jobid}  (or: {attach_cmd})"
    ));
    if out.json_mode {
        out.json(&json!({"jobid": jobid, "template": name}));
    }
    0
}

// --- stop ----------------------------------------------------------------------

pub fn cmd_stop(
    jobid_arg: Option<&str>,
    yes: bool,
    dry_run: bool,
    runner: Runner,
    out: &Out,
) -> i32 {
    if yes && dry_run {
        out.error("error: --yes and --dry-run are mutually exclusive");
        return 2;
    }

    let res = match slurm::resolve_jobid(jobid_arg, Verb::Stop, None, None, runner) {
        Ok(r) => r,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };
    if let Some(err) = &res.error {
        out.error(&format!("error: {err}"));
        return 1;
    }
    if res.ambiguous {
        print_candidates(out, &res.candidates, "multiple jobs running");
        return 2;
    }

    let jid = res.job_id.clone().expect("resolved job id");
    let argv = slurm::scancel_argv(&jid);

    // Acting on the job you're sitting inside ends this session - surface it
    // in every path, including a dry-run preview, so the resolver's decision
    // is never a surprise.
    let self_cancel = res.acting_on_current();
    if self_cancel {
        out.status(&format!(
            "warning: job {jid} is the allocation you're inside ($SLURM_JOB_ID); \
             cancelling it will end this session."
        ));
    }

    if dry_run {
        out.status("dry-run - would run:");
        out.emit(
            &json!({
                "dry_run": true,
                "jobid": jid,
                "argv": argv,
                "inside_allocation": self_cancel,
            }),
            || Some(format!("  {}", slurm::shell_join(&argv))),
        );
        return 0;
    }

    if !yes {
        if !out.interactive {
            out.error(&format!(
                "error: non-interactive session - pass -y to cancel job {jid}, \
                 or -n to preview."
            ));
            return 2;
        }
        let prompt = if self_cancel {
            format!("Cancel job {jid} (the one you're inside)?")
        } else {
            format!("Cancel job {jid}?")
        };
        if !crate::output::confirm(&prompt, false) {
            out.status("aborted");
            return 1;
        }
    }

    let (code, _, err) = runner(&argv);
    if code != 0 {
        out.error(&format!("scancel failed: {}", err.trim()));
        return 1;
    }
    out.status(&format!("cancelled job {jid}"));
    if out.json_mode {
        out.json(&json!({"cancelled": jid}));
    }
    0
}

// --- jump ----------------------------------------------------------------------

/// Drop the user into a shell on the job's compute node.
///
/// Exec-replaces the current process with `srun --pty` so the user's shell
/// history and signal handling are clean.
///
/// Nesting heads-up: attaching from *inside* an allocation ($SLURM_JOB_ID
/// set) spawns a nested step. Unlike `stop`, attach is non-destructive and
/// Ctrl-D-recoverable, so the command WARNS-AND-PROCEEDS (not refuses) -
/// `-q/--quiet` silences the heads-up.
pub fn cmd_jump(
    config: &Config,
    jobid_arg: Option<&str>,
    quiet: bool,
    runner: Runner,
    out: &Out,
) -> i32 {
    let res = match slurm::resolve_jobid(jobid_arg, Verb::Jump, None, None, runner) {
        Ok(r) => r,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };
    if let Some(err) = &res.error {
        out.error(&format!("error: {err}"));
        return 1;
    }

    if !quiet {
        if res.acting_on_current() {
            out.status(&format!(
                "already inside job {} - opening a nested srun step here burns \
                 extra resources. `exit` to leave, or pass another JOBID. \
                 Attaching anyway.",
                res.inside_job_id.as_deref().unwrap_or("")
            ));
        } else if res.inside {
            out.status(&format!(
                "nesting: you're inside job {}; attaching to job {} opens a \
                 step on another allocation. Proceeding.",
                res.inside_job_id.as_deref().unwrap_or(""),
                res.job_id.as_deref().unwrap_or("")
            ));
        }
        if res.source == "most-recent" {
            out.status(&format!(
                "multiple running jobs; attaching to most recent {} \
                 (pass JOBID to choose another)",
                res.job_id.as_deref().unwrap_or("")
            ));
        }
    }

    let jid = res.job_id.expect("resolved job id");
    let argv = slurm::srun_pty_argv(&jid, &config.default_shell);
    exec_replace(&argv, out)
}

/// Replace the current process with `argv` (returns only on failure).
fn exec_replace(argv: &[String], out: &Out) -> i32 {
    use std::os::unix::process::CommandExt;

    let err = std::process::Command::new(&argv[0]).args(&argv[1..]).exec();
    out.error(&format!("error: failed to exec {}: {err}", argv[0]));
    1
}

// --- time ----------------------------------------------------------------------

pub fn cmd_time(jobid_arg: Option<&str>, runner: Runner, out: &Out) -> i32 {
    let res = match slurm::resolve_jobid(jobid_arg, Verb::Time, None, None, runner) {
        Ok(r) => r,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };
    if let Some(err) = &res.error {
        out.error(&format!("error: {err}"));
        return 1;
    }
    if res.source == "most-recent" {
        out.status(&format!(
            "multiple jobs; showing most recent {} (pass JOBID to choose another)",
            res.job_id.as_deref().unwrap_or("")
        ));
    }

    let jid = res.job_id.expect("resolved job id");
    let argv = slurm::squeue_time_left_argv(&jid);
    let (code, out_text, err) = runner(&argv);
    if code != 0 || out_text.trim().is_empty() {
        let detail = if err.trim().is_empty() {
            "(empty output)".to_string()
        } else {
            err.trim().to_string()
        };
        out.error(&format!("squeue failed for jobid {jid}: {detail}"));
        return 1;
    }
    let time_left = out_text.trim().to_string();
    out.emit(&json!({"jobid": jid, "time_left": time_left}), || {
        Some(time_left.clone())
    });
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strs(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    // ---- start-tail parsing (the Click-faithful algorithm) ------------------

    #[test]
    fn start_tail_empty() {
        let t = parse_start_tail(&[]).unwrap();
        assert_eq!(t, StartTail::default());
    }

    #[test]
    fn start_tail_dry_run_only() {
        let t = parse_start_tail(&strs(&["-n"])).unwrap();
        assert!(t.dry_run);
        assert_eq!(t.template, None);
        assert!(t.passthrough.is_empty());
    }

    #[test]
    fn start_tail_template_and_flags() {
        let t = parse_start_tail(&strs(&["gpu", "-n"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert!(t.dry_run);
    }

    #[test]
    fn start_tail_dashdash_passthrough() {
        let t = parse_start_tail(&strs(&["gpu", "-n", "--", "--mem=128G"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert_eq!(t.passthrough, ["--mem=128G"]);
    }

    #[test]
    fn start_tail_unknown_options_interleaved() {
        let t = parse_start_tail(&strs(&["gpu", "-n", "--mem=128G", "-c", "8"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert!(t.dry_run);
        assert_eq!(t.passthrough, ["--mem=128G", "-c", "8"]);
    }

    #[test]
    fn start_tail_first_token_after_dashdash_is_template() {
        // Even an option-looking token becomes the template after `--`.
        let t = parse_start_tail(&strs(&["-n", "--", "--mem=128G"])).unwrap();
        assert!(t.dry_run);
        assert_eq!(t.template.as_deref(), Some("--mem=128G"));
        assert!(t.passthrough.is_empty());
    }

    #[test]
    fn start_tail_timeout_separate_and_equals() {
        let t = parse_start_tail(&strs(&["--timeout", "30s", "-n"])).unwrap();
        assert_eq!(t.timeout.as_deref(), Some("30s"));
        assert!(t.dry_run);
        let t = parse_start_tail(&strs(&["--timeout=1h", "gpu"])).unwrap();
        assert_eq!(t.timeout.as_deref(), Some("1h"));
        assert_eq!(t.template.as_deref(), Some("gpu"));
    }

    #[test]
    fn start_tail_timeout_missing_value() {
        let err = parse_start_tail(&strs(&["--timeout"])).unwrap_err();
        assert_eq!(err, "Option '--timeout' requires an argument.");
    }

    #[test]
    fn start_tail_second_dashdash_is_literal() {
        let t = parse_start_tail(&strs(&["--", "gpu", "--", "-x"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert_eq!(t.passthrough, ["--", "-x"]);
    }

    #[test]
    fn start_tail_options_after_dashdash_not_consumed() {
        let t = parse_start_tail(&strs(&["gpu", "--", "-n", "--timeout", "5m"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert!(!t.dry_run);
        assert_eq!(t.timeout, None);
        assert_eq!(t.passthrough, ["-n", "--timeout", "5m"]);
    }

    #[test]
    fn start_tail_short_cluster_peels_known() {
        let t = parse_start_tail(&strs(&["-nc"])).unwrap();
        assert!(t.dry_run);
        assert_eq!(t.template.as_deref(), Some("-c"));
    }

    #[test]
    fn start_tail_bundled_dry_run_shorts() {
        // `-nn` unbundles to two dry-run flags (golden js-bundled-shorts).
        let t = parse_start_tail(&strs(&["-nn"])).unwrap();
        assert!(t.dry_run);
        assert_eq!(t.template, None);
        assert!(t.passthrough.is_empty());
    }

    #[test]
    fn start_tail_dashdash_shields_dry_run_for_salloc() {
        // golden js-dd-shield-n / js-dd-shield-n4: with the template slot
        // filled, `--` forwards -n (and its value) to salloc.
        let t = parse_start_tail(&strs(&["gpu", "--", "-n"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert!(!t.dry_run);
        assert_eq!(t.passthrough, ["-n"]);

        let t = parse_start_tail(&strs(&["gpu", "--", "-n", "4"])).unwrap();
        assert_eq!(t.passthrough, ["-n", "4"]);
    }

    #[test]
    fn start_tail_dashdash_option_fills_template_slot() {
        // golden js-dd-shield-timeout: the first token after `--` becomes
        // the template even when it looks like a flag.
        let t = parse_start_tail(&strs(&["--", "--timeout", "30s"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("--timeout"));
        assert_eq!(t.timeout, None);
        assert_eq!(t.passthrough, ["30s"]);
    }

    #[test]
    fn start_tail_double_dashdash_forwards_literal() {
        // golden js-dd-dd: only the first `--` is consumed.
        let t = parse_start_tail(&strs(&["gpu", "-n", "--", "--mem=1G", "--", "-c", "2"])).unwrap();
        assert_eq!(t.template.as_deref(), Some("gpu"));
        assert!(t.dry_run);
        assert_eq!(t.passthrough, ["--mem=1G", "--", "-c", "2"]);
    }

    #[test]
    fn start_tail_dry_run_with_value_is_usage_error() {
        let err = parse_start_tail(&strs(&["--dry-run=true"])).unwrap_err();
        assert_eq!(err, "Option '--dry-run' does not take a value.");
        let err = parse_start_tail(&strs(&["gpu", "--dry-run="])).unwrap_err();
        assert_eq!(err, "Option '--dry-run' does not take a value.");
    }

    #[test]
    fn start_tail_short_help_token() {
        let t = parse_start_tail(&strs(&["-h"])).unwrap();
        assert!(t.help);
        // After `--`, -h is passthrough-bound, not help.
        let t = parse_start_tail(&strs(&["gpu", "--", "-h"])).unwrap();
        assert!(!t.help);
        assert_eq!(t.passthrough, ["-h"]);
    }

    // ---- table rendering ----------------------------------------------------

    #[test]
    fn jobs_table_aligns_columns() {
        let jobs = vec![Job {
            job_id: "54800001".to_string(),
            name: "solx-default".to_string(),
            state: "RUNNING".to_string(),
            time_used: "1:23".to_string(),
            time_left: "2-03:04:05".to_string(),
            partition: "general".to_string(),
            node_list: "sc042".to_string(),
        }];
        let table = jobs_table(&jobs);
        let lines: Vec<&str> = table.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].starts_with("JOBID"));
        assert!(lines[1].starts_with("54800001  solx-default"));
    }
}
