//! `solx` entry point and command dispatch.
//!
//! Surface (see docs/solx.md):
//!
//! ```text
//! solx init
//! solx job list  (alias `ls`; group also reachable as `jobs`)
//! solx job start [TEMPLATE]
//! solx job stop  [JOBID]
//! solx job jump  [JOBID]   (also `solx jump`)
//! solx job time  [JOBID]
//! solx keep      [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]
//! solx config show [--json]
//! solx config edit
//! solx config import-solkeep   (migrate ~/.solkeep into [keep])
//! solx completions <bash|zsh|fish>
//! solx version   (alias of --version)
//! solx help      (alias of --help)
//! ```
//!
//! Global output flag: `--json` forces JSON; by default output auto-detects
//! (tables on a terminal, JSON when stdout is not a TTY). `--json` is
//! accepted both before the subcommand and trailing on every leaf except
//! `job start`, where a non-leading `--json` is salloc passthrough.

mod completions;
mod config;
mod gitwild;
mod init;
mod jobs;
mod keep;
mod output;
mod side;
mod slurm;

use std::path::PathBuf;

use clap::{CommandFactory, Parser, Subcommand};

use crate::output::{py_repr, Out};
use crate::side::require_sol;

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Parser)]
#[command(
    name = "solx",
    about = "CLI for ASU's Sol supercomputer.",
    disable_version_flag = true,
    disable_help_subcommand = true
)]
struct Cli {
    /// Show version and exit.
    #[arg(long)]
    version: bool,

    /// Force JSON output (machine-readable).
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Option<Cmd>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Write a starter config.toml.
    Init {
        /// Overwrite without prompting (-y/--yes accepted too).
        #[arg(short = 'f', long = "force", alias = "yes", short_alias = 'y')]
        force: bool,
    },

    /// Renew CSV-flagged scratch files filtered by the keep block in config.
    Keep(KeepArgs),

    /// Drop into a shell on the job's compute node (= solx job jump).
    Jump {
        /// Job ID. Defaults to current job (compute) or sole/most-recent
        /// running job (login).
        jobid: Option<String>,
        /// Suppress the nesting / most-recent heads-up.
        #[arg(short = 'q', long)]
        quiet: bool,
    },

    /// Emit a shell completion script (bash, zsh, or fish).
    Completions {
        /// Target shell: bash, zsh, or fish.
        shell: String,
    },

    /// Show version and exit (alias of --version).
    Version,

    /// Manage interactive Slurm jobs on Sol (alias: jobs).
    #[command(alias = "jobs")]
    Job {
        #[command(subcommand)]
        command: Option<JobCmd>,
    },

    /// Inspect and edit the solx config.
    Config {
        #[command(subcommand)]
        command: Option<ConfigCmd>,
    },

    /// Show help and exit (alias of --help).
    Help,
}

#[derive(clap::Args)]
struct KeepArgs {
    /// Which warning CSVs to read.
    #[arg(long, default_value = "all")]
    stage: String,
    /// Directory holding Sol's warning CSVs.
    #[arg(long = "csv-dir")]
    csv_dir: Option<PathBuf>,
    /// Path to a gitignore-style keep-list (overrides the [keep] config block).
    #[arg(long)]
    solkeep: Option<PathBuf>,
    /// Parallel touch workers.
    #[arg(
        short = 'j',
        long = "jobs",
        default_value_t = keep::default_jobs(),
        value_parser = clap::value_parser!(u64).range(1..)
    )]
    jobs: u64,
    /// Skip confirmation prompt (also -f/--force).
    #[arg(short = 'y', long = "yes", alias = "force", short_alias = 'f')]
    yes: bool,
    /// Print plan without executing.
    #[arg(short = 'n', long = "dry-run")]
    dry_run: bool,
    /// Verbose plan + progress.
    #[arg(short = 'v', long)]
    verbose: bool,
}

#[derive(Subcommand)]
enum JobCmd {
    /// Print my Sol jobs.
    #[command(alias = "ls")]
    List,

    /// Start an interactive allocation from a config template.
    ///
    /// Unrecognized options and everything after `--` pass through to
    /// salloc.
    #[command(disable_help_flag = true)]
    Start {
        /// Template name (defaults to default_template) plus salloc
        /// passthrough.
        #[arg(num_args = 0.., allow_hyphen_values = true, trailing_var_arg = true)]
        rest: Vec<String>,
    },

    /// Cancel a job (prompts unless -y).
    Stop {
        /// Job ID. Defaults per resolution rules.
        jobid: Option<String>,
        /// Skip confirmation prompt (also -f/--force).
        #[arg(short = 'y', long = "yes", alias = "force", short_alias = 'f')]
        yes: bool,
        /// Print scancel argv without executing.
        #[arg(short = 'n', long = "dry-run")]
        dry_run: bool,
    },

    /// Drop into a shell on the job's compute node.
    Jump {
        /// Job ID. Defaults per resolution rules.
        jobid: Option<String>,
        /// Suppress the nesting / most-recent heads-up.
        #[arg(short = 'q', long)]
        quiet: bool,
    },

    /// Print remaining time (D-HH:MM:SS).
    Time {
        /// Job ID. Defaults per resolution rules.
        jobid: Option<String>,
    },
}

#[derive(Subcommand)]
enum ConfigCmd {
    /// Print the resolved config.
    Show,
    /// Open the config in $EDITOR.
    Edit,
    /// Migrate a legacy ~/.solkeep keep-list into the config's [keep] block.
    ImportSolkeep {
        /// Keep-list to import (default: ~/.solkeep).
        #[arg(long)]
        solkeep: Option<PathBuf>,
        /// Accept a lossy import (an order-dependent re-include that the
        /// [keep] block can't preserve).
        #[arg(short = 'f', long)]
        force: bool,
    },
}

fn main() {
    std::process::exit(run());
}

fn run() -> i32 {
    // Runtime-completion invocations (the `_SOLX_COMPLETE` env protocol
    // that installed completion scripts use to call back into solx) never
    // execute a command: exit 0 silently.
    if std::env::var_os("_SOLX_COMPLETE").is_some() {
        return 0;
    }

    let argv: Vec<String> = std::env::args().skip(1).collect();

    // A leading `--json` resolves before anything else so a `job start`
    // invocation can hand its raw tail to the Click-style parser (clap
    // would otherwise eat the `--` separator and the passthrough options).
    // `--version` is left to clap: only an invocation it fully validates
    // prints the version (junk alongside the flag is a usage error).
    let mut i = 0;
    let mut json = false;
    while i < argv.len() && argv[i] == "--json" {
        json = true;
        i += 1;
    }
    let rest = &argv[i..];

    // No-args invocations print the group help on stdout and exit 2.
    if rest.is_empty() {
        return print_group_help(&[]);
    }
    if rest.len() == 1 && matches!(rest[0].as_str(), "job" | "jobs" | "config") {
        let group = if rest[0] == "jobs" { "job" } else { &rest[0] };
        return print_group_help(&[group]);
    }
    // `job start` parses its own tail (template / passthrough split).
    if matches!(rest[0].as_str(), "job" | "jobs")
        && rest.get(1).map(String::as_str) == Some("start")
    {
        return run_job_start(json, &rest[2..]);
    }

    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(err) => {
            // clap renders help to stdout (exit 0) and usage errors to
            // stderr (exit 2).
            err.exit();
        }
    };
    if cli.version {
        println!("{VERSION}");
        return 0;
    }
    let json = cli.json || json;

    match cli.command {
        None => {
            eprintln!("error: missing subcommand. Try 'solx --help'.");
            2
        }
        Some(Cmd::Version) => {
            println!("{VERSION}");
            0
        }
        Some(Cmd::Help) => {
            print!("{}", root_help());
            0
        }
        Some(Cmd::Completions { shell }) => completions::cmd_completions(&shell),
        Some(Cmd::Init { force }) => {
            require_sol();
            let out = Out::auto(json);
            // Auto-import an existing ~/.solkeep into the new config's
            // [keep] block (interactive walkthrough only).
            init::cmd_init(force, &config::home_dir().join(".solkeep"), &out)
        }
        Some(Cmd::Keep(args)) => {
            require_sol();
            run_keep(&args, json)
        }
        Some(Cmd::Jump { jobid, quiet }) => {
            require_sol();
            run_jump(jobid.as_deref(), quiet, json)
        }
        Some(Cmd::Job { command }) => match command {
            None => print_group_help(&["job"]),
            Some(JobCmd::List) => {
                require_sol();
                let out = Out::auto(json);
                jobs::cmd_list(&slurm::real_runner, &out)
            }
            // Unreachable in practice: `job start` is intercepted on the raw
            // argv above. Kept for completeness.
            Some(JobCmd::Start { rest }) => run_job_start(json, &rest),
            Some(JobCmd::Stop {
                jobid,
                yes,
                dry_run,
            }) => {
                require_sol();
                let out = Out::auto(json);
                jobs::cmd_stop(jobid.as_deref(), yes, dry_run, &slurm::real_runner, &out)
            }
            Some(JobCmd::Jump { jobid, quiet }) => {
                require_sol();
                run_jump(jobid.as_deref(), quiet, json)
            }
            Some(JobCmd::Time { jobid }) => {
                require_sol();
                let out = Out::auto(json);
                jobs::cmd_time(jobid.as_deref(), &slurm::real_runner, &out)
            }
        },
        Some(Cmd::Config { command }) => match command {
            None => print_group_help(&["config"]),
            Some(ConfigCmd::Show) => {
                require_sol();
                run_config_show(json)
            }
            Some(ConfigCmd::Edit) => {
                require_sol();
                run_config_edit()
            }
            Some(ConfigCmd::ImportSolkeep { solkeep, force }) => {
                require_sol();
                let out = Out::auto(json);
                init::cmd_import_solkeep(solkeep.as_deref(), force, &out)
            }
        },
    }
}

/// The root help text, with the binary name in the usage line.
fn root_help() -> String {
    Cli::command().bin_name("solx").render_help().to_string()
}

/// Print the help for a (sub)command path on stdout; exit code 2
/// (a no-args invocation is a usage error that still shows the way out).
fn print_group_help(path: &[&str]) -> i32 {
    match path {
        [] => print!("{}", root_help()),
        [group] => {
            // Render with the full `solx <group>` usage prefix.
            let mut cmd = Cli::command();
            let mut sub = cmd
                .find_subcommand_mut(group)
                .expect("known subcommand group")
                .clone()
                .bin_name(format!("solx {group}"));
            print!("{}", sub.render_help());
        }
        _ => unreachable!("only root and one-level groups print help here"),
    }
    2
}

fn load_or_exit(out: &Out) -> Result<config::Config, i32> {
    match config::load(&config::config_path()) {
        Ok(c) => Ok(c),
        Err(e) => {
            out.error(&format!("error: {e}"));
            Err(2)
        }
    }
}

fn run_jump(jobid: Option<&str>, quiet: bool, json: bool) -> i32 {
    let out = Out::auto(json);
    let config = match load_or_exit(&out) {
        Ok(c) => c,
        Err(code) => return code,
    };
    jobs::cmd_jump(&config, jobid, quiet, &slurm::real_runner, &out)
}

/// `job start` help. The command's tail is parsed by
/// [`jobs::parse_start_tail`], not clap, so its help is rendered here: the
/// full `solx job start` usage plus the contract options (`-n/--dry-run`,
/// `--timeout`), the TEMPLATE argument, and the salloc passthrough.
const JOB_START_HELP: &str = "\
Start an interactive allocation from a config template.

Unrecognized options and everything after `--` pass through to salloc.

Usage: solx job start [OPTIONS] [TEMPLATE] [SALLOC_ARGS]...

Arguments:
  [TEMPLATE]        Template name; defaults to default_template
  [SALLOC_ARGS]...  Extra arguments forwarded to salloc

Options:
  -n, --dry-run             Print salloc argv without submitting
      --timeout <DURATION>  Override start_timeout (e.g. \"5m\", \"1h\")
  -h, --help                Print help
";

fn run_job_start(json: bool, tail: &[String]) -> i32 {
    require_sol();
    let parsed = match jobs::parse_start_tail(tail) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("error: {e}");
            return 2;
        }
    };
    if parsed.help {
        print!("{JOB_START_HELP}");
        return 0;
    }
    let out = Out::auto(json);
    let config = match load_or_exit(&out) {
        Ok(c) => c,
        Err(code) => return code,
    };
    let mut timeout_seconds: Option<i64> = None;
    if let Some(t) = parsed.timeout.as_deref().filter(|t| !t.is_empty()) {
        match config::parse_duration(t) {
            Ok(secs) => timeout_seconds = Some(secs),
            Err(e) => {
                out.error(&format!("error: {e}"));
                return 2;
            }
        }
    }
    jobs::cmd_start(
        &config,
        parsed.template.as_deref(),
        parsed.dry_run,
        timeout_seconds,
        &parsed.passthrough,
        None,
        &out,
    )
}

fn run_keep(args: &KeepArgs, json: bool) -> i32 {
    let out = Out::auto(json);
    let valid = ["all", "inactive", "over90", "pending"]; // sorted
    if !valid.contains(&args.stage.as_str()) {
        out.error(&format!(
            "invalid --stage {}. choose from: {}",
            py_repr(&args.stage),
            valid.join(", ")
        ));
        return 2;
    }
    // `keep` can run off a `~/.solkeep` alone, so a missing config.toml is
    // fine (config stays None). A config that exists but is malformed still
    // errors.
    let config = if config::config_path().exists() {
        match load_or_exit(&out) {
            Ok(c) => Some(c),
            Err(code) => return code,
        }
    } else {
        None
    };
    let opts = keep::KeepOptions {
        csv_dir: args.csv_dir.clone(),
        stage: args.stage.clone(),
        jobs_n: args.jobs,
        yes: args.yes,
        dry_run: args.dry_run,
        verbose: args.verbose,
        solkeep: args.solkeep.clone(),
        config_keep: config.as_ref().and_then(|c| c.keep.as_ref()),
    };
    keep::cmd_keep(&opts, &out)
}

fn run_config_show(json: bool) -> i32 {
    use serde_json::{json, Map, Value};

    let out = Out::auto(json);
    let config = match load_or_exit(&out) {
        Ok(c) => c,
        Err(code) => return code,
    };

    if out.json_mode {
        let mut templates = Map::new();
        for (name, t) in &config.templates {
            let mut body = Map::new();
            body.insert("name".to_string(), json!(t.name));
            body.insert("partition".to_string(), json!(t.partition));
            body.insert("time".to_string(), json!(t.time));
            if let Some(qos) = &t.qos {
                body.insert("qos".to_string(), json!(qos));
            }
            if let Some(gres) = &t.gres {
                body.insert("gres".to_string(), json!(gres));
            }
            if !t.extra_args.is_empty() {
                body.insert("extra_args".to_string(), json!(t.extra_args));
            }
            templates.insert(name.clone(), Value::Object(body));
        }
        let keep_value = match &config.keep {
            Some(k) => json!({"include": k.raw_include, "exclude": k.raw_exclude}),
            None => Value::Null,
        };
        out.json(&json!({
            "default_shell": config.default_shell,
            "default_template": config.default_template,
            "start_timeout_seconds": config.start_timeout_seconds,
            "templates": templates,
            "keep": keep_value,
        }));
        return 0;
    }

    out.human(&format!("default_shell    {}", config.default_shell));
    out.human(&format!("default_template {}", config.default_template));
    out.human(&format!(
        "start_timeout    {}s",
        config.start_timeout_seconds
    ));
    for (name, t) in &config.templates {
        out.human(&format!("\n[jobs.{name}]"));
        out.human(&format!("  partition   {}", t.partition));
        out.human(&format!("  time        {}", t.time));
        if let Some(qos) = &t.qos {
            out.human(&format!("  qos         {qos}"));
        }
        if let Some(gres) = &t.gres {
            out.human(&format!("  gres        {gres}"));
        }
        if !t.extra_args.is_empty() {
            out.human(&format!("  extra_args  {}", t.extra_args.join(" ")));
        }
    }
    match &config.keep {
        Some(k) => {
            out.human("\n[keep]");
            for (i, pat) in k.raw_include.iter().enumerate() {
                let label = if i == 0 { "include    " } else { "           " };
                out.human(&format!("  {label} {pat}"));
            }
            for (i, pat) in k.raw_exclude.iter().enumerate() {
                let label = if i == 0 { "exclude    " } else { "           " };
                out.human(&format!("  {label} {pat}"));
            }
        }
        None => out.human("\n[keep] not configured (solx keep will exit 2)"),
    }
    0
}

fn run_config_edit() -> i32 {
    let p = config::config_path();
    if !p.exists() {
        eprintln!("no config at {}. run `solx init` first.", p.display());
        return 2;
    }
    // $EDITOR is often a command with flags (e.g. "code --wait",
    // "vim -u NORC"), so split it into argv rather than treating the whole
    // string as one binary.
    let editor = std::env::var("EDITOR")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| which("vi"))
        .unwrap_or_else(|| "nano".to_string());
    // An unparseable $EDITOR (e.g. an unbalanced quote) is a hard runtime
    // failure, not a usage error: one clean line, exit 1.
    let argv = match shlex::split(&editor) {
        Some(argv) if !argv.is_empty() => argv,
        _ => {
            eprintln!("error: unparseable $EDITOR value {}", py_repr(&editor));
            return 1;
        }
    };
    match std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .arg(&p)
        .status()
    {
        Ok(status) => status.code().unwrap_or(1),
        Err(e) => {
            eprintln!("error: failed to run {}: {e}", argv[0]);
            1
        }
    }
}

/// Locate `name` on PATH (a plain executable-file check).
fn which(name: &str) -> Option<String> {
    use std::os::unix::fs::PermissionsExt;

    let path = std::env::var("PATH").ok()?;
    for dir in path.split(':').filter(|d| !d.is_empty()) {
        let candidate = std::path::Path::new(dir).join(name);
        if let Ok(meta) = candidate.metadata() {
            if meta.is_file() && meta.permissions().mode() & 0o111 != 0 {
                return Some(candidate.display().to_string());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_tree_is_consistent() {
        Cli::command().debug_assert();
    }
}
