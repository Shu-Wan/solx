//! `solx keep` - renew scratch files Sol has flagged, filtered by `[keep]`.
//!
//! Read Sol's warning CSVs from `--csv-dir`, intersect the flagged
//! directories with the `[keep]` include/exclude globs from config, and
//! refresh timestamps (`touch -a -m -c` semantics) on only the intersection.
//! Only what Sol has explicitly flagged is renewed - never a wholesale
//! `/scratch` walk.
//!
//! Execution is file-level-sharded: a streaming pipeline over one worker
//! pool - enumerate a kept directory, split its files into evenly-sized
//! batches, and touch the batches across the pool. A single huge directory
//! fans out into many batches, so `-j` scales the parallelism of the whole
//! run including its largest directory, not just the count of directories.
//!
//! This is metadata-heavy NFS I/O. On Sol run it on a compute node or the
//! DTN (`ssh soldtn`), not a throttled login node.

use std::collections::HashSet;
use std::collections::VecDeque;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};

use filetime::FileTime;
use serde_json::{json, Value};

use crate::config::KeepRules;
use crate::output::{confirm, to_python_json, Out};

pub const STAGE_ORDER: [&str; 3] = ["pending", "over90", "inactive"];
pub const STAGES_ALL: &str = "all";

pub fn stage_file(stage: &str) -> &'static str {
    match stage {
        "pending" => "scratch-dirs-pending-removal.csv",
        "over90" => "scratch-dirs-over-90days.csv",
        "inactive" => "scratch-dirs-inactive.csv",
        _ => unreachable!("stage validated by the caller"),
    }
}

/// Files per touch shard. Big enough that per-batch overhead is negligible,
/// small enough that one huge directory fans out into many batches and
/// keeps every worker busy.
pub const BATCH: usize = 2000;

/// Cap on how many dirs are inlined into a JSON payload. Sol's warning CSVs
/// can list thousands of flagged dirs; emitting them all makes a
/// multi-megabyte document that blows an agent's context. The inlined
/// sample is capped and the true totals + a `*_truncated` flag are always
/// reported. Counts are always exact; the lists are a sample.
pub const JSON_LIST_CAP: usize = 100;

/// The default `-j` worker count: `max(1, min(8, ncpus / 4))`.
///
/// `ncpus` is the count of ONLINE system CPUs (`sysconf(_SC_NPROCESSORS_ONLN)`,
/// i.e. Python `os.cpu_count()` semantics), NOT the cgroup/affinity-limited
/// parallelism of the current process - inside a 4-core Slurm allocation on a
/// 128-CPU node the default is still 8.
pub fn default_jobs() -> u64 {
    let n = unsafe { libc::sysconf(libc::_SC_NPROCESSORS_ONLN) };
    let cpus = if n > 0 { n as u64 } else { 2 };
    (cpus / 4).clamp(1, 8)
}

/// The directories `solx keep` would touch (`kept`) vs filter out (`skipped`),
/// each tagged with the warning stage that flagged it.
#[derive(Debug, Default, Clone)]
pub struct Plan {
    pub kept: Vec<(String, String)>,
    pub skipped: Vec<(String, String)>,
}

// --- planning ----------------------------------------------------------------

/// Return the `Directory` column from one of Sol's warning CSVs.
///
/// A missing file is fine - Sol only drops the CSV when there's something
/// to flag. An empty result means nothing to do for that stage. An existing
/// file that can't be read or decoded is a hard error (the command must
/// fail loudly rather than treat the stage as "nothing flagged").
///
/// A UTF-8 BOM is treated as part of the first header cell's name (so a
/// BOM'd `Directory` header is not the `Directory` column and the file
/// yields no directories).
pub fn load_csv_dirs(csv_path: &Path) -> Result<Vec<String>, String> {
    if !csv_path.exists() {
        return Ok(Vec::new());
    }
    let read_err =
        |e: &dyn std::fmt::Display| format!("unable to read {}: {e}", csv_path.display());
    let has_bom = std::fs::File::open(csv_path)
        .and_then(|mut f| {
            use std::io::Read;
            let mut head = [0u8; 3];
            let n = f.read(&mut head)?;
            Ok(n == 3 && head == [0xEF, 0xBB, 0xBF])
        })
        .map_err(|e| read_err(&e))?;
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_path(csv_path)
        .map_err(|e| read_err(&e))?;
    let headers = reader.headers().map_err(|e| read_err(&e))?;
    let dir_idx = match headers
        .iter()
        .enumerate()
        .position(|(i, name)| name == "Directory" && !(i == 0 && has_bom))
    {
        Some(i) => i,
        None => return Ok(Vec::new()),
    };
    let mut dirs = Vec::new();
    for record in reader.records() {
        let record = record.map_err(|e| read_err(&e))?;
        if let Some(d) = record.get(dir_idx) {
            let d = d.trim();
            if !d.is_empty() {
                dirs.push(d.to_string());
            }
        }
    }
    Ok(dirs)
}

/// Walk the chosen stages' CSVs and split flagged dirs into kept/skipped.
pub fn build_plan(csv_dir: &Path, stages: &[String], keep: &KeepRules) -> Result<Plan, String> {
    let mut plan = Plan::default();
    let mut seen: HashSet<String> = HashSet::new();
    for stage in stages {
        for d in load_csv_dirs(&csv_dir.join(stage_file(stage)))? {
            if !seen.insert(d.clone()) {
                continue;
            }
            let entry = (stage.clone(), d.clone());
            if keep.matches(&d) {
                plan.kept.push(entry);
            } else {
                plan.skipped.push(entry);
            }
        }
    }
    Ok(plan)
}

// --- enumeration + touching ---------------------------------------------------
//
// Two task kinds run on one worker pool:
//   enumerate_dir -- walk a kept directory, return its files
//   touch_files   -- refresh timestamps on a batch of those files
// touch is the expensive half (one metadata write per file), so it is
// sharded into file batches and spread across the pool.

/// List every regular file under `directory` in one walk.
///
/// Matches `find DIR -type f`: hidden files included, no ignore files
/// honored, symlinks not followed. Returns `(directory, files, message)`.
/// A path that isn't a directory (e.g. flagged then removed) is reported as
/// a benign skip, not an error.
pub fn enumerate_dir(directory: &str) -> (String, Vec<PathBuf>, String) {
    if !Path::new(directory).is_dir() {
        return (
            directory.to_string(),
            Vec::new(),
            "skipped: not a directory".to_string(),
        );
    }
    let walker = ignore::WalkBuilder::new(directory)
        .hidden(false)
        .ignore(false)
        .git_ignore(false)
        .git_global(false)
        .git_exclude(false)
        .parents(false)
        .follow_links(false)
        .build();
    let mut files = Vec::new();
    let mut walk_error: Option<String> = None;
    for entry in walker {
        match entry {
            Ok(e) => {
                if e.file_type().is_some_and(|t| t.is_file()) {
                    files.push(e.into_path());
                }
            }
            Err(e) => walk_error = Some(e.to_string()),
        }
    }
    if let Some(msg) = walk_error {
        return (directory.to_string(), Vec::new(), msg);
    }
    (directory.to_string(), files, "ok".to_string())
}

/// Refresh atime+mtime on a batch of files (`touch -a -m -c` semantics).
///
/// Returns `(files_attempted, errors, message)`. A file deleted between
/// enumeration and touch is silently skipped, not an error, and nothing is
/// ever created. A real failure (permission, I/O) is counted and surfaced.
pub fn touch_files(paths: &[PathBuf]) -> (usize, usize, String) {
    if paths.is_empty() {
        return (0, 0, "ok".to_string());
    }
    let now = FileTime::now();
    let mut errors = 0;
    let mut msg = "ok".to_string();
    for p in paths {
        match filetime::set_file_times(p, now, now) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => {
                errors = 1;
                msg = format!("touch {}: {e}", p.display());
            }
        }
    }
    (paths.len(), errors, msg)
}

/// Split a flat file list into evenly-sized batches for the touch pool.
pub fn shard(files: Vec<PathBuf>, batch_size: usize) -> Vec<Vec<PathBuf>> {
    if files.is_empty() {
        return Vec::new();
    }
    let mut batches = Vec::with_capacity(files.len().div_ceil(batch_size));
    let mut current = Vec::with_capacity(batch_size.min(files.len()));
    for f in files {
        current.push(f);
        if current.len() == batch_size {
            batches.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        batches.push(current);
    }
    batches
}

// --- command -------------------------------------------------------------------

pub struct KeepOptions<'a> {
    pub csv_dir: Option<PathBuf>,
    pub stage: String,
    pub jobs_n: u64,
    pub yes: bool,
    pub dry_run: bool,
    pub verbose: bool,
    pub config_keep: Option<&'a KeepRules>,
}

pub fn cmd_keep(opts: &KeepOptions, out: &Out) -> i32 {
    if opts.yes && opts.dry_run {
        out.error("error: --yes and --dry-run are mutually exclusive");
        return 2;
    }

    // The keep-list comes from the config `[keep]` block - the single source
    // of truth.
    let keep_rules: &KeepRules = match opts.config_keep {
        Some(rules) => rules,
        None => {
            out.error("error: no [keep] block in config. add one with `solx config edit`.");
            return 2;
        }
    };

    let csv_dir = opts.csv_dir.clone().unwrap_or_else(crate::config::home_dir);
    if !csv_dir.is_dir() {
        out.error(&format!(
            "error: --csv-dir {} is not a directory \
             (Sol drops the warning CSVs in $HOME).",
            csv_dir.display()
        ));
        return 2;
    }
    let stages: Vec<String> = if opts.stage == STAGES_ALL {
        STAGE_ORDER.iter().map(|s| s.to_string()).collect()
    } else {
        vec![opts.stage.clone()]
    };

    let plan = match build_plan(&csv_dir, &stages, keep_rules) {
        Ok(p) => p,
        Err(e) => {
            out.error(&format!("error: {e}"));
            return 1;
        }
    };
    if let Err(e) = report_plan(out, &plan, &csv_dir, &stages, opts.verbose) {
        out.error(&format!("error: {e}"));
        return 1;
    }

    if plan.kept.is_empty() {
        if out.json_mode {
            // Still emit a document so an agent gets structured output, not
            // empty stdout, when nothing is flagged.
            match plan_json(&plan, &csv_dir, &stages, opts.dry_run) {
                Ok(doc) => out.json(&doc),
                Err(e) => {
                    out.error(&format!("error: {e}"));
                    return 1;
                }
            }
        } else {
            out.status("no flagged directories matched - nothing to do.");
        }
        return 0;
    }

    if opts.dry_run {
        if out.json_mode {
            match plan_json(&plan, &csv_dir, &stages, true) {
                Ok(doc) => out.json(&doc),
                Err(e) => {
                    out.error(&format!("error: {e}"));
                    return 1;
                }
            }
        }
        return 0;
    }

    if !opts.yes {
        // Destructive: never block on a prompt in a non-interactive session.
        if !out.interactive {
            out.error(&format!(
                "error: non-interactive session - pass -y to renew {} \
                 directories, or -n to preview.",
                plan.kept.len()
            ));
            return 2;
        }
        if !confirm(
            &format!("Touch mtimes on {} directories?", plan.kept.len()),
            false,
        ) {
            out.status("aborted");
            return 1;
        }
    }

    let (total_files, failures) = execute(&plan, opts.jobs_n, out);

    if out.json_mode {
        let kept_truncated = plan.kept.len() > JSON_LIST_CAP;
        let mut summary = json!({
            "renewed": true,
            "dirs": plan.kept.len(),
            "files_touched": total_files,
            "failures": failures,
            "kept_truncated": kept_truncated,
            "kept": plan.kept.iter().take(JSON_LIST_CAP).map(|(_, d)| d.clone()).collect::<Vec<_>>(),
        });
        if kept_truncated {
            match dump_full_plan(&plan, &csv_dir, &stages) {
                Ok(path) => summary["full_plan_path"] = json!(path),
                Err(e) => {
                    out.error(&format!("error: {e}"));
                    return 1;
                }
            }
        }
        out.json(&summary);
    } else {
        let failed = if failures > 0 {
            format!(" · {failures} failed")
        } else {
            String::new()
        };
        out.status(&format!(
            "done {} dirs · {total_files} files touched{failed}",
            plan.kept.len()
        ));
    }
    if failures > 0 {
        1
    } else {
        0
    }
}

/// Print the plan summary to stderr (human) - stdout stays the data channel.
fn report_plan(
    out: &Out,
    plan: &Plan,
    csv_dir: &Path,
    stages: &[String],
    verbose: bool,
) -> Result<(), String> {
    if out.json_mode {
        return Ok(());
    }
    out.status(&format!(
        "csv-dir: {}  stages: {}",
        csv_dir.display(),
        stages.join(", ")
    ));
    out.status(&format!(
        "plan: {} kept, {} skipped",
        plan.kept.len(),
        plan.skipped.len()
    ));
    if plan.kept.len() > JSON_LIST_CAP || plan.skipped.len() > JSON_LIST_CAP {
        let path = dump_full_plan(plan, csv_dir, stages)?;
        out.status(&format!(
            "full plan ({} dirs): {path}",
            plan.kept.len() + plan.skipped.len()
        ));
    }
    if verbose {
        if !plan.kept.is_empty() {
            out.status("kept:");
            for (stage, d) in plan.kept.iter().take(20) {
                out.status(&format!("  {stage:>9} {d}"));
            }
            if plan.kept.len() > 20 {
                out.status(&format!("  ... and {} more", plan.kept.len() - 20));
            }
        }
        if !plan.skipped.is_empty() {
            out.status("skipped (flagged by Sol but not in [keep]):");
            for (stage, d) in plan.skipped.iter().take(20) {
                out.status(&format!("  {stage:>9} {d}"));
            }
        }
    }
    Ok(())
}

/// Bounded plan document: exact counts, a capped sample of each list.
///
/// When either list is truncated, the COMPLETE plan is spilled to a temp
/// file and its path returned under `full_plan_path` - so the response
/// stays small enough for an agent's context while the full detail is one
/// `cat` away.
fn plan_json(
    plan: &Plan,
    csv_dir: &Path,
    stages: &[String],
    dry_run: bool,
) -> Result<Value, String> {
    let entry = |(stage, dir): &(String, String)| json!({"stage": stage, "dir": dir});
    let kept_truncated = plan.kept.len() > JSON_LIST_CAP;
    let skipped_truncated = plan.skipped.len() > JSON_LIST_CAP;
    let mut doc = json!({
        "dry_run": dry_run,
        "csv_dir": csv_dir.display().to_string(),
        "stages": stages,
        "kept_count": plan.kept.len(),
        "skipped_count": plan.skipped.len(),
        "kept_truncated": kept_truncated,
        "skipped_truncated": skipped_truncated,
        "kept": plan.kept.iter().take(JSON_LIST_CAP).map(entry).collect::<Vec<_>>(),
        "skipped": plan.skipped.iter().take(JSON_LIST_CAP).map(entry).collect::<Vec<_>>(),
    });
    if kept_truncated || skipped_truncated {
        doc["full_plan_path"] = json!(dump_full_plan(plan, csv_dir, stages)?);
    }
    Ok(doc)
}

/// Write the complete (untruncated) plan to `solx-keep-plan-*.json` in the
/// system temp dir; return its path.
///
/// The file is created owner-only (0600) with bounded name-collision
/// retries, and stays on disk after the run. A creation or write failure is
/// an error (the document enumerates the user's scratch layout, so a
/// truncated or missing spill must never be advertised as complete).
fn dump_full_plan(plan: &Plan, csv_dir: &Path, stages: &[String]) -> Result<String, String> {
    let entry = |(stage, dir): &(String, String)| json!({"stage": stage, "dir": dir});
    let doc = json!({
        "csv_dir": csv_dir.display().to_string(),
        "stages": stages,
        "kept": plan.kept.iter().map(entry).collect::<Vec<_>>(),
        "skipped": plan.skipped.iter().map(entry).collect::<Vec<_>>(),
    });
    let temp = tempfile::Builder::new()
        .prefix("solx-keep-plan-")
        .suffix(".json")
        .tempfile()
        .map_err(|e| format!("unable to create the full-plan temp file: {e}"))?;
    let (mut file, path) = temp
        .keep()
        .map_err(|e| format!("unable to keep the full-plan temp file: {e}"))?;
    file.write_all(to_python_json(&doc).as_bytes())
        .map_err(|e| format!("unable to write {}: {e}", path.display()))?;
    Ok(path.display().to_string())
}

// --- execution -------------------------------------------------------------------

enum Task {
    Enumerate(String),
    Touch(String, Vec<PathBuf>),
}

struct PoolState {
    queue: VecDeque<Task>,
    in_flight: usize,
    total_files: usize,
    failures: usize,
}

/// Renew `plan.kept`. Returns `(files_touched, failures)`.
///
/// With `jobs_n <= 1` runs serially (no pool - fast and deterministic for
/// small runs). Otherwise one worker pool runs both halves: enumerate a
/// directory, shard its files, and queue the batches as touch tasks, so a
/// single huge directory spreads its batches over every worker.
pub fn execute(plan: &Plan, jobs_n: u64, out: &Out) -> (usize, usize) {
    if jobs_n <= 1 {
        return execute_serial(plan, out);
    }

    let state = Mutex::new(PoolState {
        queue: plan
            .kept
            .iter()
            .map(|(_, d)| Task::Enumerate(d.clone()))
            .collect(),
        in_flight: 0,
        total_files: 0,
        failures: 0,
    });
    let ready = Condvar::new();
    let out = *out;

    std::thread::scope(|scope| {
        for _ in 0..jobs_n {
            scope.spawn(|| worker(&state, &ready, &out));
        }
    });

    let final_state = state.into_inner().expect("pool lock");
    (final_state.total_files, final_state.failures)
}

fn worker(state: &Mutex<PoolState>, ready: &Condvar, out: &Out) {
    loop {
        let task = {
            let mut s = state.lock().expect("pool lock");
            loop {
                if let Some(task) = s.queue.pop_front() {
                    s.in_flight += 1;
                    break task;
                }
                if s.in_flight == 0 {
                    // Nothing queued and nothing running: the pipeline drained.
                    ready.notify_all();
                    return;
                }
                s = ready.wait(s).expect("pool lock");
            }
        };

        match task {
            Task::Enumerate(d) => {
                let (_, files, msg) = enumerate_dir(&d);
                let mut s = state.lock().expect("pool lock");
                if msg == "ok" {
                    for batch in shard(files, BATCH) {
                        s.queue.push_back(Task::Touch(d.clone(), batch));
                    }
                } else if !msg.starts_with("skipped") {
                    s.failures += 1;
                    out.error(&format!("FAIL enumerate {d} :: {msg}"));
                }
                s.in_flight -= 1;
                ready.notify_all();
            }
            Task::Touch(d, batch) => {
                let (n, errs, msg) = touch_files(&batch);
                let mut s = state.lock().expect("pool lock");
                s.total_files += n;
                if errs > 0 {
                    s.failures += 1;
                    out.error(&format!("FAIL touch {d} :: {msg}"));
                }
                s.in_flight -= 1;
                ready.notify_all();
            }
        }
    }
}

fn execute_serial(plan: &Plan, out: &Out) -> (usize, usize) {
    let mut total_files = 0;
    let mut failures = 0;
    for (_, d) in &plan.kept {
        let (_, files, msg) = enumerate_dir(d);
        if msg != "ok" && !msg.starts_with("skipped") {
            failures += 1;
            out.error(&format!("FAIL enumerate {d} :: {msg}"));
            continue;
        }
        let count = files.len();
        for batch in shard(files, BATCH) {
            let (n, errs, tmsg) = touch_files(&batch);
            total_files += n;
            if errs > 0 {
                failures += 1;
                out.error(&format!("FAIL touch {d} :: {tmsg}"));
            }
        }
        if msg == "ok" && !out.json_mode {
            out.status(&format!("  ok {count:>7} files  {d}"));
        }
    }
    (total_files, failures)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn keep(include: &[&str], exclude: &[&str]) -> KeepRules {
        KeepRules::new(
            &include.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            &exclude.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        )
    }

    fn write_csv(path: &Path, dirs: &[&str]) {
        let mut lines = vec!["Directory,LastAccess,Size".to_string()];
        lines.extend(dirs.iter().map(|d| format!("{d},2026-01-01,1G")));
        fs::write(path, lines.join("\n") + "\n").unwrap();
    }

    fn stages_all() -> Vec<String> {
        STAGE_ORDER.iter().map(|s| s.to_string()).collect()
    }

    // ---- planning ------------------------------------------------------------

    #[test]
    fn load_csv_dirs_reads_directory_column() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("scratch-dirs-pending-removal.csv");
        write_csv(&p, &["/scratch/sparky/a", "/scratch/sparky/b"]);
        assert_eq!(
            load_csv_dirs(&p).unwrap(),
            ["/scratch/sparky/a", "/scratch/sparky/b"]
        );
    }

    #[test]
    fn load_csv_dirs_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_csv_dirs(&dir.path().join("absent.csv"))
            .unwrap()
            .is_empty());
    }

    #[test]
    fn load_csv_dirs_directory_not_first_column() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("x.csv");
        fs::write(&p, "User,Directory,Size\nsparky,/scratch/sparky/a,12G\n").unwrap();
        assert_eq!(load_csv_dirs(&p).unwrap(), ["/scratch/sparky/a"]);
    }

    #[test]
    fn load_csv_dirs_bom_header_yields_no_directories() {
        // A BOM is part of the first header cell's name, so the column
        // lookup misses and the file contributes nothing.
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("bom.csv");
        fs::write(&p, b"\xEF\xBB\xBFDirectory,Size\n/scratch/sparky/a,1G\n").unwrap();
        assert!(load_csv_dirs(&p).unwrap().is_empty());
        // With the Directory column not first, the BOM lands on another
        // header and the column still resolves.
        let p2 = dir.path().join("bom2.csv");
        fs::write(&p2, b"\xEF\xBB\xBFSize,Directory\n1G,/scratch/sparky/a\n").unwrap();
        assert_eq!(load_csv_dirs(&p2).unwrap(), ["/scratch/sparky/a"]);
    }

    #[test]
    fn load_csv_dirs_invalid_utf8_record_is_error() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("bad.csv");
        fs::write(&p, b"Directory,Size\n/scratch/sparky/\xFF\xFE,1G\n").unwrap();
        let err = load_csv_dirs(&p).unwrap_err();
        assert!(err.contains("unable to read"));
        assert!(err.contains("bad.csv"));
    }

    #[test]
    fn load_csv_dirs_unreadable_file_is_error() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("locked.csv");
        write_csv(&p, &["/scratch/sparky/a"]);
        fs::set_permissions(&p, fs::Permissions::from_mode(0o000)).unwrap();
        let err = load_csv_dirs(&p).unwrap_err();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(err.contains("unable to read"));
        assert!(err.contains("locked.csv"));
    }

    #[test]
    fn build_plan_filters_by_keep() {
        let dir = tempfile::tempdir().unwrap();
        write_csv(
            &dir.path().join("scratch-dirs-pending-removal.csv"),
            &["/scratch/sparky/proj-a", "/scratch/sparky/proj-z"],
        );
        write_csv(
            &dir.path().join("scratch-dirs-over-90days.csv"),
            &["/scratch/sparky/proj-b"],
        );
        let rules = keep(&["/scratch/sparky/proj-a", "/scratch/sparky/proj-b"], &[]);
        let plan = build_plan(dir.path(), &stages_all(), &rules).unwrap();
        let kept: Vec<&str> = plan.kept.iter().map(|(_, d)| d.as_str()).collect();
        assert_eq!(kept, ["/scratch/sparky/proj-a", "/scratch/sparky/proj-b"]);
        let skipped: Vec<&str> = plan.skipped.iter().map(|(_, d)| d.as_str()).collect();
        assert_eq!(skipped, ["/scratch/sparky/proj-z"]);
    }

    #[test]
    fn build_plan_dedupes_across_stages() {
        let dir = tempfile::tempdir().unwrap();
        write_csv(
            &dir.path().join("scratch-dirs-pending-removal.csv"),
            &["/scratch/sparky/a"],
        );
        write_csv(
            &dir.path().join("scratch-dirs-over-90days.csv"),
            &["/scratch/sparky/a"],
        );
        let rules = keep(&["/scratch/sparky/a"], &[]);
        let plan = build_plan(dir.path(), &stages_all(), &rules).unwrap();
        assert_eq!(plan.kept.len(), 1);
        assert_eq!(plan.kept[0].0, "pending"); // first stage wins
    }

    #[test]
    fn build_plan_exclude_carve_out() {
        let dir = tempfile::tempdir().unwrap();
        write_csv(
            &dir.path().join("scratch-dirs-pending-removal.csv"),
            &[
                "/scratch/sparky/proj/run-1",
                "/scratch/sparky/proj/__pycache__",
            ],
        );
        let rules = keep(&["/scratch/sparky/proj/**"], &["**/__pycache__"]);
        let plan = build_plan(dir.path(), &["pending".to_string()], &rules).unwrap();
        let kept: Vec<&str> = plan.kept.iter().map(|(_, d)| d.as_str()).collect();
        assert_eq!(kept, ["/scratch/sparky/proj/run-1"]);
        let skipped: Vec<&str> = plan.skipped.iter().map(|(_, d)| d.as_str()).collect();
        assert_eq!(skipped, ["/scratch/sparky/proj/__pycache__"]);
    }

    #[test]
    fn build_plan_negation_last_match_wins() {
        // `!` carve-outs within the include list (gitignore last-match-wins).
        let dir = tempfile::tempdir().unwrap();
        let rules = keep(&["/scratch/sparky/proj", "!**/__pycache__"], &[]);
        write_csv(
            &dir.path().join("scratch-dirs-pending-removal.csv"),
            &[
                "/scratch/sparky/proj/run",
                "/scratch/sparky/proj/__pycache__",
                "/scratch/sparky/x",
            ],
        );
        let plan = build_plan(dir.path(), &["pending".to_string()], &rules).unwrap();
        let kept: Vec<&str> = plan.kept.iter().map(|(_, d)| d.as_str()).collect();
        assert_eq!(kept, ["/scratch/sparky/proj/run"]);
    }

    // ---- shard / enumerate / touch (the renewal mechanism) ----------------------

    #[test]
    fn shard_even_batches() {
        let files: Vec<PathBuf> = (0..10).map(|i| PathBuf::from(format!("f{i}"))).collect();
        let batches = shard(files.clone(), 3);
        let sizes: Vec<usize> = batches.iter().map(|b| b.len()).collect();
        assert_eq!(sizes, [3, 3, 3, 1]);
        let flat: Vec<PathBuf> = batches.into_iter().flatten().collect();
        assert_eq!(flat, files);
    }

    #[test]
    fn shard_empty() {
        assert!(shard(Vec::new(), BATCH).is_empty());
    }

    #[test]
    fn enumerate_dir_lists_all_including_hidden_and_ignored() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.txt"), "x").unwrap();
        fs::write(dir.path().join(".hidden"), "x").unwrap();
        fs::create_dir(dir.path().join("sub")).unwrap();
        fs::write(dir.path().join("sub/b.txt"), "x").unwrap();
        // A .gitignore plus an ignored file: both must still be listed.
        fs::write(dir.path().join(".gitignore"), "ignored.txt\n").unwrap();
        fs::write(dir.path().join("ignored.txt"), "x").unwrap();

        let (_, files, msg) = enumerate_dir(dir.path().to_str().unwrap());
        assert_eq!(msg, "ok");
        assert!(files.iter().all(|p| p.is_file()));
        // 5 regular files: a.txt, .hidden, sub/b.txt, .gitignore, ignored.txt
        assert_eq!(files.len(), 5);
    }

    #[test]
    fn enumerate_dir_skips_symlinked_files() {
        // `find -type f` does not count symlinks; neither does the walker.
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("real.txt"), "x").unwrap();
        std::os::unix::fs::symlink(dir.path().join("real.txt"), dir.path().join("link.txt"))
            .unwrap();
        let (_, files, msg) = enumerate_dir(dir.path().to_str().unwrap());
        assert_eq!(msg, "ok");
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn enumerate_dir_not_a_directory() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("nope");
        let (_, files, msg) = enumerate_dir(missing.to_str().unwrap());
        assert!(files.is_empty());
        assert!(msg.starts_with("skipped"));
    }

    #[test]
    fn touch_files_refreshes_times() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("stale.txt");
        fs::write(&f, "x").unwrap();
        let old = FileTime::from_unix_time(FileTime::now().unix_seconds() - 8_640_000, 0);
        filetime::set_file_times(&f, old, old).unwrap();

        let (attempted, errors, _) = touch_files(std::slice::from_ref(&f));
        assert_eq!((attempted, errors), (1, 0));
        let mtime = FileTime::from_last_modification_time(&f.metadata().unwrap());
        assert!(mtime.unix_seconds() > FileTime::now().unix_seconds() - 10);
    }

    #[test]
    fn touch_files_missing_path_is_silent_skip() {
        let dir = tempfile::tempdir().unwrap();
        let ghost = dir.path().join("gone.txt");
        let (attempted, errors, msg) = touch_files(std::slice::from_ref(&ghost));
        assert_eq!((attempted, errors), (1, 0));
        assert_eq!(msg, "ok");
        assert!(!ghost.exists()); // never created
    }

    #[test]
    fn touch_files_empty_batch() {
        assert_eq!(touch_files(&[]), (0, 0, "ok".to_string()));
    }

    #[test]
    fn execute_serial_counts_and_skips() {
        let dir = tempfile::tempdir().unwrap();
        let real = dir.path().join("proj");
        fs::create_dir(&real).unwrap();
        fs::write(real.join("a"), "x").unwrap();
        fs::write(real.join("b"), "x").unwrap();
        let plan = Plan {
            kept: vec![
                ("pending".to_string(), real.display().to_string()),
                ("pending".to_string(), "/does/not/exist".to_string()),
            ],
            skipped: vec![],
        };
        let out = Out {
            json_mode: true,
            interactive: false,
        };
        let (files, failures) = execute(&plan, 1, &out);
        assert_eq!((files, failures), (2, 0));
    }

    #[test]
    fn execute_parallel_matches_serial_counts() {
        let dir = tempfile::tempdir().unwrap();
        let mut kept = Vec::new();
        for d in 0..5 {
            let sub = dir.path().join(format!("d{d}"));
            fs::create_dir(&sub).unwrap();
            for f in 0..7 {
                fs::write(sub.join(format!("f{f}")), "x").unwrap();
            }
            kept.push(("pending".to_string(), sub.display().to_string()));
        }
        let plan = Plan {
            kept,
            skipped: vec![],
        };
        let out = Out {
            json_mode: true,
            interactive: false,
        };
        let (files, failures) = execute(&plan, 4, &out);
        assert_eq!((files, failures), (35, 0));
    }

    #[test]
    fn default_jobs_within_bounds() {
        let n = default_jobs();
        assert!((1..=8).contains(&n));
    }
}
