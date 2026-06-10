//! End-to-end tests over the real binary.
//!
//! Each test runs `solx` in an isolated fake HOME with deterministic SLURM
//! mocks (`tests/mocks/bin`) on PATH, mirroring the behavioral parity
//! matrix: stdout is the data channel (JSON when piped), diagnostics land on
//! stderr, and exit codes follow the documented contract.

use std::fs;
use std::path::{Path, PathBuf};

use assert_cmd::Command;
use predicates::prelude::*;

const SAMPLE_CONFIG: &str = r#"default_shell = "zsh"
default_template = "default"
start_timeout = "5m"

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"

[jobs.gpu]
partition = "public"
gres = "gpu:a100:1"
time = "0-4"
extra_args = ["--mem=64G", "--cpus-per-task=8"]

[keep]
include = ["/scratch/sparky/proj-a", "/scratch/sparky/proj-b/**"]
exclude = ["**/__pycache__", "**/.venv"]
"#;

fn mocks_bin() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("mocks")
        .join("bin")
}

struct Sandbox {
    home: tempfile::TempDir,
}

impl Sandbox {
    fn new() -> Self {
        let home = tempfile::tempdir().expect("tempdir");
        fs::create_dir_all(home.path().join(".config/solx")).expect("config dir");
        Sandbox { home }
    }

    fn with_config(self) -> Self {
        fs::write(
            self.home.path().join(".config/solx/config.toml"),
            SAMPLE_CONFIG,
        )
        .expect("write config");
        self
    }

    fn write_home(&self, name: &str, content: &str) {
        fs::write(self.home.path().join(name), content).expect("write fixture");
    }

    fn cmd(&self) -> Command {
        let mut cmd = Command::cargo_bin("solx").expect("solx binary");
        cmd.env_clear()
            .env("PATH", format!("{}:/usr/bin:/bin", mocks_bin().display()))
            .env("HOME", self.home.path())
            .env("XDG_CONFIG_HOME", self.home.path().join(".config"))
            .env("USER", "sparky")
            .env("LOGNAME", "sparky")
            .env("TERM", "dumb")
            .env("LC_ALL", "C");
        cmd
    }
}

#[test]
fn version_flag_prints_bare_semver() {
    let sb = Sandbox::new();
    sb.cmd()
        .arg("--version")
        .assert()
        .success()
        .stdout(format!("{}\n", env!("CARGO_PKG_VERSION")))
        .stderr("");
}

#[test]
fn version_command_matches_flag() {
    let sb = Sandbox::new();
    sb.cmd()
        .arg("version")
        .assert()
        .success()
        .stdout(format!("{}\n", env!("CARGO_PKG_VERSION")));
}

#[test]
fn no_args_prints_help_and_exits_2() {
    let sb = Sandbox::new();
    sb.cmd()
        .assert()
        .code(2)
        .stdout(predicate::str::contains("keep").and(predicate::str::contains("job")));
}

#[test]
fn job_list_emits_json_when_piped() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "list"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"job_id\": \"54800001\""))
        .stdout(predicate::str::starts_with("[\n"))
        .stderr("");
}

#[test]
fn job_list_squeue_failure_is_exit_1_on_stderr() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["job", "list"])
        .env("MOCK_SQUEUE_FAIL", "1")
        .assert()
        .code(1)
        .stdout("")
        .stderr("error: squeue failed: boom\n");
}

#[test]
fn job_time_inside_allocation_uses_env_jobid() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "time"])
        .env("SLURM_JOB_ID", "54800001")
        .assert()
        .success()
        .stdout("{\n  \"jobid\": \"54800001\",\n  \"time_left\": \"2-03:04:05\"\n}\n");
}

#[test]
fn job_stop_dry_run_previews_scancel() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "stop", "12345", "-n"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"dry_run\": true"))
        .stdout(predicate::str::contains("\"inside_allocation\": false"))
        .stderr("dry-run — would run:\n");
}

#[test]
fn job_stop_non_interactive_refuses_without_yes() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["job", "stop", "12345"])
        .assert()
        .code(2)
        .stderr(
            "error: non-interactive session — pass -y to cancel job 12345, or -n to preview.\n",
        );
}

#[test]
fn job_start_dry_run_uses_default_template() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "start", "-n"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"template\": \"default\""))
        .stdout(predicate::str::contains("\"-q\",\n    \"public\""))
        .stderr("dry-run — would run:\n");
}

#[test]
fn job_start_passthrough_after_dashdash() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "start", "gpu", "-n", "--", "--mem=128G"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"--mem=128G\""))
        .stdout(predicate::str::contains("\"template\": \"gpu\""));
}

#[test]
fn job_start_first_token_after_dashdash_is_template() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "start", "-n", "--", "--mem=128G"])
        .assert()
        .code(1)
        .stderr(predicate::str::contains(
            "unknown job template '--mem=128G'. defined: debug, default, gpu",
        ));
}

#[test]
fn job_start_real_parses_granted_allocation() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "start"])
        .assert()
        .success()
        .stdout("{\n  \"jobid\": \"54809999\",\n  \"template\": \"default\"\n}\n")
        .stderr(predicate::str::contains("allocated job 54809999"));
}

#[test]
fn jump_exec_replaces_with_srun() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["jump", "12345", "-q"])
        .assert()
        .success()
        .stdout("MOCK_SRUN --jobid=12345 --overlap --pty zsh\n");
}

#[test]
fn keep_dry_run_plan_filters_by_keep_block() {
    let sb = Sandbox::new().with_config();
    sb.write_home(
        "scratch-dirs-pending-removal.csv",
        "User,Directory,Size\nsparky,/scratch/sparky/proj-a,12G\nsparky,/scratch/sparky/other,3G\n",
    );
    sb.write_home(
        "scratch-dirs-over-90days.csv",
        "User,Directory,Size\nsparky,/scratch/sparky/proj-b/data,40G\n",
    );
    sb.cmd()
        .args(["--json", "keep", "-n"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"kept_count\": 2"))
        .stdout(predicate::str::contains("\"skipped_count\": 1"))
        .stdout(predicate::str::contains("/scratch/sparky/proj-b/data"));
}

#[test]
fn keep_renews_real_files() {
    let sb = Sandbox::new();
    // A [keep] block pointing inside the sandbox, plus a flagged dir with a
    // stale file.
    let scratch = sb.home.path().join("scratch");
    fs::create_dir_all(scratch.join("proj/sub")).unwrap();
    let stale = scratch.join("proj/sub/stale.bin");
    fs::write(&stale, "x").unwrap();
    let old = filetime::FileTime::from_unix_time(1_000_000, 0);
    filetime::set_file_times(&stale, old, old).unwrap();

    fs::write(
        sb.home.path().join(".config/solx/config.toml"),
        format!(
            "default_shell = \"bash\"\ndefault_template = \"default\"\n\n\
             [jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n\n\
             [keep]\ninclude = [\"{}/**\"]\n",
            scratch.display()
        ),
    )
    .unwrap();
    sb.write_home(
        "scratch-dirs-pending-removal.csv",
        &format!(
            "User,Directory,Size\nsparky,{},1G\n",
            scratch.join("proj").display()
        ),
    );

    sb.cmd()
        .args(["--json", "keep", "-y", "-j", "1"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"files_touched\": 1"))
        .stdout(predicate::str::contains("\"failures\": 0"));

    let mtime = filetime::FileTime::from_last_modification_time(&stale.metadata().unwrap());
    assert!(mtime.unix_seconds() > 1_000_000, "stale file renewed");
}

#[test]
fn keep_invalid_stage_exits_2() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["keep", "--stage", "bogus"])
        .assert()
        .code(2)
        .stderr("invalid --stage 'bogus'. choose from: all, inactive, over90, pending\n");
}

#[test]
fn keep_without_rules_exits_2() {
    let sb = Sandbox::new(); // no config, no ~/.solkeep
    sb.cmd().args(["keep", "-n"]).assert().code(2).stderr(
        "error: no [keep] block in config and no ~/.solkeep. \
         run `solx config edit` to add a [keep] block.\n",
    );
}

#[test]
fn keep_solkeep_fallback_names_removal_version() {
    let sb = Sandbox::new(); // no config.toml
    sb.write_home(".solkeep", "/scratch/sparky/proj-a\n");
    sb.write_home(
        "scratch-dirs-pending-removal.csv",
        "User,Directory,Size\nsparky,/scratch/sparky/proj-a,12G\n",
    );
    sb.cmd()
        .args(["--json", "keep", "-n"])
        .assert()
        .success()
        .stderr(predicate::str::contains("loses support in solx 1.0.0"));
}

#[test]
fn config_show_json_preserves_file_order() {
    let sb = Sandbox::new().with_config();
    let assert = sb
        .cmd()
        .args(["config", "show", "--json"])
        .assert()
        .success();
    let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
    let d = stdout.find("\"default\"").unwrap();
    let g = stdout.find("\"gpu\"").unwrap();
    let b = stdout.find("\"debug\"").unwrap();
    assert!(d < b && b < g, "templates serialize in file order");
    assert!(stdout.contains("\"start_timeout_seconds\": 300"));
}

#[test]
fn config_edit_propagates_editor_argv_and_exit() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["config", "edit"])
        .env("EDITOR", "/bin/echo -n")
        .assert()
        .success()
        .stdout(predicate::str::ends_with("config.toml"))
        .stdout(predicate::str::ends_with("\n").not());
}

#[test]
fn init_fresh_writes_starter_config() {
    let sb = Sandbox::new(); // empty XDG
    sb.cmd()
        .args(["--json", "init"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"wrote\""))
        .stderr("edit it with `solx config edit`, then `solx job start`.\n");
    let written = sb.home.path().join(".config/solx/config.toml");
    let text = fs::read_to_string(&written).unwrap();
    assert!(text.contains("sparky"));
    use std::os::unix::fs::PermissionsExt;
    let mode = written.metadata().unwrap().permissions().mode() & 0o777;
    assert_eq!(mode, 0o600);
}

#[test]
fn init_existing_without_force_exits_2() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["init"])
        .assert()
        .code(2)
        .stderr(predicate::str::contains(
            "already exists. pass -f to overwrite.",
        ));
}

#[test]
fn import_solkeep_appends_keep_block() {
    let sb = Sandbox::new();
    fs::write(
        sb.home.path().join(".config/solx/config.toml"),
        "default_shell = \"zsh\"\ndefault_template = \"default\"\n\n\
         [jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n",
    )
    .unwrap();
    sb.write_home(".solkeep", "/scratch/sparky/proj\n!**/__pycache__\n");
    sb.cmd()
        .args(["--json", "config", "import-solkeep"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"include\""))
        .stderr(predicate::str::contains(
            "imported 1 include / 1 exclude pattern(s) into [keep]",
        ));
    let text = fs::read_to_string(sb.home.path().join(".config/solx/config.toml")).unwrap();
    assert!(text.contains("[keep]"));
    assert!(text.contains("\"/scratch/sparky/proj\","));
}

#[test]
fn import_solkeep_refuses_lossy_order() {
    let sb = Sandbox::new();
    fs::write(
        sb.home.path().join(".config/solx/config.toml"),
        "default_shell = \"zsh\"\ndefault_template = \"default\"\n\n\
         [jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n",
    )
    .unwrap();
    sb.write_home(
        ".solkeep",
        "/scratch/sparky/proj-a\n!/scratch/sparky/proj-a/tmp\n/scratch/sparky/proj-a/tmp/keepme\n",
    );
    sb.cmd()
        .args(["config", "import-solkeep"])
        .assert()
        .code(2)
        .stderr(predicate::str::contains(
            "re-includes a path under an earlier",
        ));
}

#[test]
fn completions_unknown_shell_exits_2() {
    let sb = Sandbox::new();
    sb.cmd()
        .args(["completions", "tcsh"])
        .assert()
        .code(2)
        .stdout("")
        .stderr("unknown shell 'tcsh'; choose bash, zsh, or fish.\n");
}

#[test]
fn completions_zsh_is_compdef_script() {
    let sb = Sandbox::new();
    sb.cmd()
        .args(["completions", "zsh"])
        .assert()
        .success()
        .stdout(predicate::str::starts_with("#compdef solx"));
}

#[test]
fn trailing_json_is_accepted_on_leaves() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["job", "list", "--json"])
        .assert()
        .success()
        .stdout(predicate::str::contains("\"job_id\""));
}

#[test]
fn version_with_junk_arguments_exits_2() {
    // Only the bare `--version` / `version` forms print the version.
    let sb = Sandbox::new();
    sb.cmd().args(["version", "bogus"]).assert().code(2);
    sb.cmd().args(["--bogus", "--version"]).assert().code(2);
    sb.cmd().args(["--version", "--bogus"]).assert().code(2);
}

#[test]
fn help_command_rejects_arguments() {
    let sb = Sandbox::new();
    sb.cmd()
        .arg("help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage: solx"));
    sb.cmd().args(["help", "job"]).assert().code(2).stdout("");
}

#[test]
fn dash_h_prints_help_and_exits_0() {
    let sb = Sandbox::new();
    sb.cmd()
        .arg("-h")
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage: solx"));
}

#[test]
fn group_help_usage_carries_binary_name() {
    let sb = Sandbox::new();
    sb.cmd()
        .arg("job")
        .assert()
        .code(2)
        .stdout(predicate::str::contains("Usage: solx job"));
    sb.cmd()
        .arg("config")
        .assert()
        .code(2)
        .stdout(predicate::str::contains("Usage: solx config"));
}

#[test]
fn job_start_help_documents_contract_options() {
    let sb = Sandbox::new().with_config();
    for flags in [["job", "start", "--help"], ["job", "start", "-h"]] {
        let assert = sb.cmd().args(flags).assert().success();
        let stdout = String::from_utf8(assert.get_output().stdout.clone()).unwrap();
        assert!(stdout.contains("Usage: solx job start"));
        assert!(stdout.contains("-n, --dry-run"));
        assert!(stdout.contains("--timeout"));
        assert!(stdout.contains("[TEMPLATE]"));
        assert!(stdout.contains("salloc"));
    }
}

#[test]
fn job_start_dry_run_with_value_exits_2() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["job", "start", "--dry-run=true"])
        .assert()
        .code(2)
        .stderr("error: Option '--dry-run' does not take a value.\n");
}

#[test]
fn job_start_submit_line_keeps_equals_tokens_bare() {
    // The `submitting:` argv render quotes nothing in the gpu template.
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["--json", "job", "start", "gpu"])
        .assert()
        .success()
        .stderr(predicate::str::contains(
            "submitting: salloc --no-shell -J solx-gpu -p public -t 0-4 \
             --gres=gpu:a100:1 --mem=64G --cpus-per-task=8",
        ));
}

#[test]
fn keep_jobs_zero_or_negative_exits_2() {
    let sb = Sandbox::new().with_config();
    sb.cmd().args(["keep", "-n", "-j", "0"]).assert().code(2);
    sb.cmd().args(["keep", "-n", "-j", "-2"]).assert().code(2);
}

#[test]
fn keep_unreadable_csv_exits_1_naming_the_file() {
    use std::os::unix::fs::PermissionsExt;
    let sb = Sandbox::new().with_config();
    sb.write_home(
        "scratch-dirs-pending-removal.csv",
        "Directory,Size\n/scratch/sparky/proj-a,1G\n",
    );
    let csv = sb.home.path().join("scratch-dirs-pending-removal.csv");
    fs::set_permissions(&csv, fs::Permissions::from_mode(0o000)).unwrap();
    sb.cmd()
        .args(["--json", "keep", "-n"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(
            predicate::str::contains("error: unable to read")
                .and(predicate::str::contains("scratch-dirs-pending-removal.csv")),
        );
    fs::set_permissions(&csv, fs::Permissions::from_mode(0o644)).unwrap();
}

#[test]
fn keep_trailing_slash_flagged_dir_is_kept() {
    let sb = Sandbox::new().with_config();
    sb.write_home(
        "scratch-dirs-pending-removal.csv",
        "Directory,Size\n/scratch/sparky/proj-a/,1G\n",
    );
    sb.cmd()
        .args(["--json", "keep", "-n"])
        .assert()
        .success()
        .stdout(
            predicate::str::contains("\"kept_count\": 1")
                .and(predicate::str::contains("/scratch/sparky/proj-a/")),
        );
}

#[test]
fn config_validation_error_strips_table_context() {
    let sb = Sandbox::new();
    fs::write(
        sb.home.path().join(".config/solx/config.toml"),
        "default_shell = \"bash\"\ndefault_template = \"default\"\n\
         [jobs.default]\npartition = \"x\"\n",
    )
    .unwrap();
    sb.cmd()
        .args(["config", "show"])
        .assert()
        .code(2)
        .stderr(predicate::str::contains(
            "config.toml:: required key `time` is missing",
        ));
}

#[test]
fn invalid_toml_error_is_one_line() {
    let sb = Sandbox::new();
    fs::write(
        sb.home.path().join(".config/solx/config.toml"),
        "default_shell = [unclosed\n",
    )
    .unwrap();
    let assert = sb.cmd().args(["config", "show"]).assert().code(2);
    let stderr = String::from_utf8(assert.get_output().stderr.clone()).unwrap();
    assert_eq!(
        stderr.lines().count(),
        1,
        "single-line TOML error: {stderr}"
    );
    assert!(stderr.contains("error: invalid TOML in"));
    assert!(stderr.contains("(at line 1, column"));
}

#[test]
fn config_edit_unparseable_editor_exits_1() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["config", "edit"])
        .env("EDITOR", "vim '")
        .assert()
        .code(1)
        .stderr("error: unparseable $EDITOR value \"vim '\"\n");
}

#[test]
fn solx_complete_env_exits_0_silently() {
    let sb = Sandbox::new().with_config();
    sb.cmd()
        .args(["job", "list"])
        .env("_SOLX_COMPLETE", "complete_zsh")
        .assert()
        .success()
        .stdout("")
        .stderr("");
}
