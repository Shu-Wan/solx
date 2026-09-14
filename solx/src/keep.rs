//! `solx keep` - renew scratch files Sol has flagged, filtered by `[keep]`.
//!
//! Read Sol's warning CSVs from `--csv-dir`, intersect the flagged
//! paths with the `[keep]` include/exclude globs from config, and
//! refresh timestamps (`touch -a -m -c` semantics) on only the intersection.
//! Only what Sol has explicitly flagged is renewed - never a wholesale
//! `/scratch` walk.
//!
//! Execution is entry-level-sharded: a streaming pipeline over one worker
//! pool - enumerate a kept directory, split its files and subdirectories
//! into evenly-sized batches, and touch the batches across the pool. A
//! single huge directory fans out into many batches, so `-j` scales the
//! parallelism of the whole run including its largest directory, not just
//! the count of directories.
//!
//! This is metadata-heavy NFS I/O. On Sol run it on a compute node or the
//! DTN (`ssh soldtn`), not a throttled login node.

use std::collections::VecDeque;
use std::collections::{BTreeMap, HashSet};
use std::ffi::CString;
use std::io::Write;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};

use serde_json::{json, Value};

use crate::config::KeepRules;
use crate::output::{confirm, to_python_json, Out};

pub const STAGE_ORDER: [&str; 3] = ["pending", "over90", "inactive"];
pub const STAGES_ALL: &str = "all";
const UNIFIED_CSV: &str = "sol-scratch-cleanup.csv";

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

/// The paths `solx keep` would touch (`kept`) vs filter out (`skipped`),
/// each tagged with the warning stage that flagged it.
#[derive(Debug, Default, Clone)]
pub struct Plan {
    pub kept: Vec<(String, String)>,
    pub skipped: Vec<(String, String)>,
}

// --- planning ----------------------------------------------------------------

/// Return `Directory`, or `Path` when absent, from a legacy warning CSV.
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
    Ok(load_csv_rows(csv_path, false)?
        .into_iter()
        .map(|(_, path)| path)
        .collect())
}

/// Read legacy paths or unified (stage, path) rows. Unknown unified actions
/// and types are errors so a changed schema cannot silently omit warnings.
fn load_csv_rows(csv_path: &Path, unified: bool) -> Result<Vec<(String, String)>, String> {
    let read_err =
        |e: &dyn std::fmt::Display| format!("unable to read {}: {e}", csv_path.display());
    let mut file = match std::fs::File::open(csv_path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(read_err(&e)),
    };
    let has_bom = (|| {
        use std::io::Read;
        let mut head = [0u8; 3];
        let n = file.read(&mut head)?;
        Ok::<_, std::io::Error>(n == 3 && head == [0xEF, 0xBB, 0xBF])
    })()
    .map_err(|e| read_err(&e))?;
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_path(csv_path)
        .map_err(|e| read_err(&e))?;
    let headers = reader.headers().map_err(|e| read_err(&e))?;
    // csv strips a leading UTF-8 BOM before exposing headers. The has_bom
    // check below preserves only the legacy first-column Directory behavior.
    let dir_idx = match headers
        .iter()
        .enumerate()
        .position(|(i, name)| name == "Directory" && !(i == 0 && has_bom && !unified))
        .or_else(|| headers.iter().position(|name| name == "Path"))
    {
        Some(i) => i,
        None if !unified => return Ok(Vec::new()),
        None => return Err(read_err(&"missing Path or Directory column")),
    };
    let action_idx = headers.iter().position(|name| name == "Action");
    let type_idx = headers.iter().position(|name| name == "Type");
    if unified && (action_idx.is_none() || type_idx.is_none()) {
        return Err(read_err(&"missing Action or Type column"));
    }
    let mut dirs = Vec::new();
    for record in reader.records() {
        let record = record.map_err(|e| read_err(&e))?;
        let stage = if unified {
            let action = record.get(action_idx.unwrap()).unwrap_or("").trim();
            let stage = match action {
                "MARKED FOR REMOVAL" => "pending",
                "Final Warning" => "over90",
                "Warning" => "inactive",
                _ => return Err(read_err(&format!("unknown Action {action:?}"))),
            };
            let kind = record.get(type_idx.unwrap()).unwrap_or("").trim();
            if !matches!(kind, "directory" | "file") {
                return Err(read_err(&format!("unknown Type {kind:?}")));
            }
            stage
        } else {
            ""
        };
        let path = record.get(dir_idx).unwrap_or("").trim();
        if unified && path.is_empty() {
            return Err(read_err(&"missing or empty path in unified row"));
        }
        if !path.is_empty() {
            dirs.push((stage.to_string(), path.to_string()));
        }
    }
    Ok(dirs)
}

/// Combine both CSV formats and split selected stages' paths into kept/skipped.
pub fn build_plan(csv_dir: &Path, stages: &[String], keep: &KeepRules) -> Result<Plan, String> {
    let mut plan = Plan::default();
    let mut seen: HashSet<String> = HashSet::new();
    let unified = load_csv_rows(&csv_dir.join(UNIFIED_CSV), true)?;
    for stage in stages {
        let legacy = load_csv_dirs(&csv_dir.join(stage_file(stage)))?;
        for d in unified
            .iter()
            .filter(|(s, _)| s == stage)
            .map(|(_, p)| p.clone())
            .chain(legacy)
        {
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
//   enumerate_dir  -- walk a kept directory, return its entries
//   touch_entries  -- refresh timestamps on a batch of those entries
// touch is the expensive half (one metadata write per entry), so it is
// sharded into batches and spread across the pool.

/// Entries found during one directory walk.
#[derive(Debug, Default)]
pub struct Walk {
    /// Regular files under the directory (`find DIR -type f`).
    pub files: Vec<PathBuf>,
    /// The directory itself plus every subdirectory (`find DIR -type d`).
    pub dirs: Vec<PathBuf>,
    /// `ok`, a skip note, or the last walk error.
    pub msg: String,
}

/// List a flagged regular file itself, or the entries under a directory.
///
/// Includes hidden and ignored entries, but not symlinks. `dirs` includes the
/// root. Missing paths and unsupported types are skipped. On error, returns
/// entries found with the last error.
pub fn enumerate_dir(directory: &str) -> Walk {
    let metadata = match std::fs::symlink_metadata(directory) {
        Ok(metadata) => metadata,
        Err(e) => {
            return Walk {
                msg: if e.kind() == std::io::ErrorKind::NotFound {
                    "skipped: missing path".to_string()
                } else {
                    e.to_string()
                },
                ..Walk::default()
            }
        }
    };
    if metadata.is_file() {
        return Walk {
            files: vec![PathBuf::from(directory)],
            msg: "ok".to_string(),
            ..Walk::default()
        };
    }
    if !metadata.is_dir() {
        return Walk {
            msg: "skipped: not a regular file or directory".to_string(),
            ..Walk::default()
        };
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
    let mut dirs = Vec::new();
    let mut walk_error: Option<String> = None;
    for entry in walker {
        match entry {
            Ok(e) => match e.file_type() {
                Some(t) if t.is_file() => files.push(e.into_path()),
                Some(t) if t.is_dir() => dirs.push(e.into_path()),
                _ => {}
            },
            Err(e) => walk_error = Some(e.to_string()),
        }
    }
    if let Some(msg) = walk_error {
        return Walk { files, dirs, msg };
    }
    Walk {
        files,
        dirs,
        msg: "ok".to_string(),
    }
}

/// Set one path's atime+mtime to now - `touch -a -m` on a single entry.
///
/// A NULL `times` is the "both stamps to now" form, which per utimensat(2)
/// needs only **write permission**. Explicit stamps need **ownership**, so
/// any helper that passes them (`filetime::set_file_times`) EPERMs on a
/// collaborator's file inside your own `/scratch` tree.
fn touch_now(path: &Path) -> std::io::Result<()> {
    let c_path = CString::new(path.as_os_str().as_bytes())
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
    // SAFETY: `c_path` is a valid NUL-terminated string that outlives the
    // call, and a null `times` is the documented set-both-to-now form.
    let rc = unsafe { libc::utimensat(libc::AT_FDCWD, c_path.as_ptr(), std::ptr::null(), 0) };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

/// Refresh atime+mtime on a batch of entries (`touch -a -m -c` semantics).
///
/// Returns `(renewed, errors, message)`, the message naming the first
/// failure plus how many followed - a whole shard can fail, and that has to
/// be legible without `BATCH` lines. An entry deleted between enumeration
/// and touch is neither renewed nor an error, and nothing is ever created.
#[cfg(test)]
pub fn touch_entries(paths: &[PathBuf]) -> (usize, usize, String) {
    touch_entries_with_report(paths, &mut Renewal::default())
}

fn touch_entries_with_report(paths: &[PathBuf], report: &mut Renewal) -> (usize, usize, String) {
    touch_entries_using(paths, report, touch_now)
}

fn touch_entries_using(
    paths: &[PathBuf],
    report: &mut Renewal,
    mut touch: impl FnMut(&Path) -> std::io::Result<()>,
) -> (usize, usize, String) {
    let mut renewed = 0;
    let mut errors = 0;
    let mut msg = "ok".to_string();
    for p in paths {
        match touch(p) {
            Ok(()) => renewed += 1,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => {
                errors += 1;
                if e.kind() == std::io::ErrorKind::PermissionDenied {
                    report.record_unwritable(p);
                }
                if errors == 1 {
                    msg = format!("touch {}: {e}", p.display());
                }
            }
        }
    }
    if errors > 1 {
        msg = format!("{msg} (and {} more in this batch)", errors - 1);
    }
    (renewed, errors, msg)
}

/// Split a flat entry list into evenly-sized batches for the touch pool.
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
            out.status("no flagged paths matched - nothing to do.");
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
                 paths, or -n to preview.",
                plan.kept.len()
            ));
            return 2;
        }
        if !confirm(
            &format!(
                "Touch atime and mtime on {} flagged paths?",
                plan.kept.len()
            ),
            false,
        ) {
            out.status("aborted");
            return 1;
        }
    }

    let renewal = execute(&plan, opts.jobs_n, out);
    let unwritable = renewal.unwritable_json();
    if !renewal.unwritable.is_empty() {
        let count: usize = renewal.unwritable.values().map(|group| group.count).sum();
        out.error(&format!(
            "warning: {count} entries not renewed (permission denied)"
        ));
        for group in &unwritable {
            let empty = match group["all_empty"].as_bool() {
                Some(true) => "all empty directories",
                Some(false) => "includes files or non-empty directories",
                None => "directory contents could not be checked",
            };
            out.error(&format!(
                "  {}: {} entries, {empty}; {}",
                group["owner"].as_str().unwrap_or("unknown"),
                group["count"],
                group["sample"][0].as_str().unwrap_or("")
            ));
        }
        out.error("  -> ask the owner to renew them or grant write permission (chmod g+w for your group).");
    }

    if out.json_mode {
        let kept_truncated = plan.kept.len() > JSON_LIST_CAP;
        let mut summary = json!({
            "renewed": true,
            "dirs": plan.kept.len(),
            "files_touched": renewal.files,
            "dirs_touched": renewal.dirs,
            "failures": renewal.failures,
            "runtime_skipped_count": renewal.skipped_count,
            "runtime_skipped": renewal.skipped,
            "runtime_skipped_truncated": renewal.skipped_count > JSON_LIST_CAP,
            "unwritable": unwritable,
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
        let failed = if renewal.failures > 0 {
            format!(" · {} failed", renewal.failures)
        } else {
            String::new()
        };
        out.status(&format!(
            "done {} flagged paths · touched {} files + {} dirs{failed} · {} skipped",
            plan.kept.len(),
            renewal.files,
            renewal.dirs,
            renewal.skipped_count
        ));
    }
    if renewal.failures > 0 {
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
            "full plan ({} paths): {path}",
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
    Touch(String, Vec<PathBuf>, Kind),
}

/// Which counter a touched batch lands in.
#[derive(Clone, Copy)]
enum Kind {
    Files,
    Dirs,
}

/// What a renewal pass actually renewed: entries that got fresh stamps
/// (an entry that vanished mid-run is in neither count), and failed
/// *operations* - one per entry that couldn't be touched, plus one per
/// directory that couldn't be walked.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Renewal {
    pub files: usize,
    pub dirs: usize,
    pub failures: usize,
    pub skipped_count: usize,
    pub skipped: Vec<Value>,
    pub unwritable: BTreeMap<u32, Unwritable>,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct Unwritable {
    count: usize,
    // None means emptiness is unknown; a known non-empty entry makes it false.
    all_empty: Option<bool>,
    sample: Vec<String>,
}

impl Renewal {
    fn add(&mut self, other: &Renewal) {
        self.files += other.files;
        self.dirs += other.dirs;
        self.failures += other.failures;
        self.skipped_count += other.skipped_count;
        self.skipped.extend(
            other
                .skipped
                .iter()
                .take(JSON_LIST_CAP - self.skipped.len())
                .cloned(),
        );
        for (uid, group) in &other.unwritable {
            let entry = self.unwritable.entry(*uid).or_insert_with(|| Unwritable {
                all_empty: Some(true),
                ..Unwritable::default()
            });
            entry.count += group.count;
            entry.all_empty = combine_empty(entry.all_empty, group.all_empty);
            entry.sample.extend(
                group
                    .sample
                    .iter()
                    .take(JSON_LIST_CAP - entry.sample.len())
                    .cloned(),
            );
        }
    }

    fn record_skip(&mut self, path: &str, reason: &str, out: &Out) {
        self.skipped_count += 1;
        if self.skipped.len() < JSON_LIST_CAP {
            self.skipped.push(json!({"path": path, "reason": reason}));
            out.error(&format!("SKIP {path} :: {reason}"));
        }
    }

    fn record_unwritable(&mut self, path: &Path) {
        let Ok(meta) = std::fs::symlink_metadata(path) else {
            return;
        };
        let empty = if meta.is_dir() {
            std::fs::read_dir(path)
                .ok()
                .and_then(|mut entries| match entries.next() {
                    None => Some(true),
                    Some(Ok(_)) => Some(false),
                    Some(Err(_)) => None,
                })
        } else {
            Some(false)
        };
        let group = self
            .unwritable
            .entry(meta.uid())
            .or_insert_with(|| Unwritable {
                all_empty: Some(true),
                ..Unwritable::default()
            });
        group.count += 1;
        group.all_empty = combine_empty(group.all_empty, empty);
        if group.sample.len() < JSON_LIST_CAP {
            group.sample.push(path.display().to_string());
        }
    }

    fn unwritable_json(&self) -> Vec<Value> {
        self.unwritable
            .iter()
            .map(|(uid, group)| {
                json!({
                    "owner": owner_name(*uid),
                    "uid": uid,
                    "count": group.count,
                    "all_empty": group.all_empty,
                    "sample": group.sample,
                    "sample_truncated": group.count > JSON_LIST_CAP,
                })
            })
            .collect()
    }
}

fn combine_empty(left: Option<bool>, right: Option<bool>) -> Option<bool> {
    match (left, right) {
        (Some(false), _) | (_, Some(false)) => Some(false),
        (Some(true), Some(true)) => Some(true),
        _ => None,
    }
}

fn owner_name(uid: u32) -> String {
    // Resolve once per owner after workers finish; keep numeric UIDs usable
    // when the account service or the system's id command is unavailable.
    std::process::Command::new("id")
        .args(["-nu", &uid.to_string()])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| uid.to_string())
}

/// The serial mode's per-directory progress line: what the directory
/// actually renewed, with no `ok` tag once anything failed (`ok 1386 files`
/// over a directory where every touch was refused is under-reporting).
fn dir_status_line(one: &Renewal, directory: &str) -> String {
    let (tag, failed) = if one.failures > 0 {
        ("fail", format!(" · {} failed", one.failures))
    } else {
        ("ok", String::new())
    };
    format!(
        "  {tag:<4} {:>7} files {:>6} dirs{failed}  {directory}",
        one.files, one.dirs
    )
}

struct PoolState {
    queue: VecDeque<Task>,
    in_flight: usize,
    renewal: Renewal,
}

/// Renew `plan.kept`.
///
/// With `jobs_n <= 1` runs serially (no pool - fast and deterministic for
/// small runs). Otherwise one worker pool runs both halves: enumerate a
/// directory, shard its entries, and queue the batches as touch tasks, so a
/// single huge directory spreads its batches over every worker.
pub fn execute(plan: &Plan, jobs_n: u64, out: &Out) -> Renewal {
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
        renewal: Renewal::default(),
    });
    let ready = Condvar::new();
    let out = *out;

    std::thread::scope(|scope| {
        for _ in 0..jobs_n {
            scope.spawn(|| worker(&state, &ready, &out));
        }
    });

    state.into_inner().expect("pool lock").renewal
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
                let walk = enumerate_dir(&d);
                let mut s = state.lock().expect("pool lock");
                let skipped = walk.msg.starts_with("skipped");
                if skipped {
                    s.renewal.record_skip(&d, &walk.msg, out);
                }
                if walk.msg != "ok" && !skipped {
                    s.renewal.failures += 1;
                    out.error(&format!("FAIL enumerate {d} :: {}", walk.msg));
                }
                if !skipped {
                    for batch in shard(walk.files, BATCH) {
                        s.queue
                            .push_back(Task::Touch(d.clone(), batch, Kind::Files));
                    }
                    for batch in shard(walk.dirs, BATCH) {
                        s.queue.push_back(Task::Touch(d.clone(), batch, Kind::Dirs));
                    }
                }
                s.in_flight -= 1;
                ready.notify_all();
            }
            Task::Touch(d, batch, kind) => {
                let mut report = Renewal::default();
                let (n, errs, msg) = touch_entries_with_report(&batch, &mut report);
                let mut s = state.lock().expect("pool lock");
                s.renewal.add(&report);
                match kind {
                    Kind::Files => s.renewal.files += n,
                    Kind::Dirs => s.renewal.dirs += n,
                }
                if errs > 0 {
                    s.renewal.failures += errs;
                    out.error(&format!("FAIL touch {d} :: {msg}"));
                }
                s.in_flight -= 1;
                ready.notify_all();
            }
        }
    }
}

fn execute_serial(plan: &Plan, out: &Out) -> Renewal {
    let mut renewal = Renewal::default();
    for (_, d) in &plan.kept {
        let walk = enumerate_dir(d);
        if walk.msg.starts_with("skipped") {
            renewal.record_skip(d, &walk.msg, out);
            continue;
        }
        let mut one = Renewal::default();
        if walk.msg != "ok" {
            one.failures += 1;
            out.error(&format!("FAIL enumerate {d} :: {}", walk.msg));
        }
        for (batch, kind) in shard(walk.files, BATCH)
            .into_iter()
            .map(|b| (b, Kind::Files))
            .chain(shard(walk.dirs, BATCH).into_iter().map(|b| (b, Kind::Dirs)))
        {
            let (n, errs, tmsg) = touch_entries_with_report(&batch, &mut one);
            match kind {
                Kind::Files => one.files += n,
                Kind::Dirs => one.dirs += n,
            }
            if errs > 0 {
                one.failures += errs;
                out.error(&format!("FAIL touch {d} :: {tmsg}"));
            }
        }
        if !out.json_mode {
            out.status(&dir_status_line(&one, d));
        }
        renewal.add(&one);
    }
    renewal
}

#[cfg(test)]
mod tests {
    use super::*;
    use filetime::FileTime;
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
    fn unified_fixture_maps_all_stages_and_dedupes_legacy() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join(UNIFIED_CSV),
            include_str!("../../evals/mocks/home/sol-scratch-cleanup.csv"),
        )
        .unwrap();
        write_csv(
            &dir.path().join(stage_file("inactive")),
            &[
                "/scratch/sparky/my-project/runs/2025-12",
                "/scratch/sparky/legacy-only",
            ],
        );
        let rules = keep(&["/scratch/sparky"], &[]);
        let plan = build_plan(dir.path(), &stages_all(), &rules).unwrap();
        assert_eq!(plan.kept.len(), 4);
        assert_eq!(plan.kept[0].0, "pending");
        assert_eq!(
            plan.kept[1],
            (
                "over90".into(),
                "/scratch/sparky/my-project/backup.jsonl".into()
            )
        );
        assert_eq!(plan.kept[2].0, "inactive");
        for stage in STAGE_ORDER {
            let plan = build_plan(dir.path(), &[stage.into()], &rules).unwrap();
            assert!(plan.kept.iter().all(|(s, _)| s == stage));
            assert_eq!(plan.kept.len(), if stage == "inactive" { 3 } else { 1 });
        }
    }

    #[test]
    fn unified_paths_support_quoting_bom_and_keep_excludes() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join(UNIFIED_CSV),
            concat!(
                "\u{feff}Action,Type,Path\n",
                "Warning,file,\"/scratch/sparky/a,b\"\n",
                "Final Warning,directory,/scratch/sparky/.cache\n",
            ),
        )
        .unwrap();
        let plan = build_plan(
            dir.path(),
            &stages_all(),
            &keep(&["/scratch/sparky"], &["**/.cache"]),
        )
        .unwrap();
        assert_eq!(
            plan.kept,
            [("inactive".into(), "/scratch/sparky/a,b".into())]
        );
        assert_eq!(plan.skipped.len(), 1);
    }

    #[test]
    fn unified_invalid_schema_is_a_named_error() {
        let dir = tempfile::tempdir().unwrap();
        for text in [
            "Action,Type\nWarning,file\n",
            "Type,Path\nfile,/scratch/sparky/a\n",
            "Action,Type,Path\nUnknown,file,/scratch/sparky/a\n",
            "Action,Type,Path\nWarning,symlink,/scratch/sparky/a\n",
            "Action,Type,Path\nWarning,file\n",
            "Action,Type,Path\nWarning,file,\n",
            "Action,Type,Path\nWarning,file,   \n",
        ] {
            fs::write(dir.path().join(UNIFIED_CSV), text).unwrap();
            let error = build_plan(dir.path(), &stages_all(), &keep(&["/scratch/sparky"], &[]))
                .unwrap_err();
            assert!(error.contains(UNIFIED_CSV), "{error}");
        }
    }

    #[test]
    fn load_csv_dirs_accepts_path_when_directory_is_absent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("legacy.csv");
        fs::write(&path, "Path,Size\n/scratch/sparky/a,1\n").unwrap();
        assert_eq!(load_csv_dirs(&path).unwrap(), ["/scratch/sparky/a"]);
        fs::write(
            &path,
            "Path,Directory\n/scratch/sparky/a,/scratch/sparky/b\n",
        )
        .unwrap();
        assert_eq!(load_csv_dirs(&path).unwrap(), ["/scratch/sparky/b"]);
    }

    #[test]
    fn permission_failures_group_by_owner_and_preserve_counts() {
        let dir = tempfile::tempdir().unwrap();
        let paths: Vec<_> = (0..JSON_LIST_CAP + 2)
            .map(|i| dir.path().join(i.to_string()))
            .collect();
        for path in &paths {
            fs::create_dir(path).unwrap();
        }
        let mut report = Renewal::default();
        let (renewed, errors, msg) = touch_entries_using(&paths, &mut report, |_| {
            Err(std::io::Error::from_raw_os_error(libc::EACCES))
        });
        assert_eq!((renewed, errors), (0, paths.len()));
        assert!(msg.contains("and 101 more"));
        let groups = report.unwritable_json();
        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0]["count"], paths.len());
        assert_eq!(groups[0]["uid"], fs::metadata(&paths[0]).unwrap().uid());
        assert_eq!(groups[0]["all_empty"], true);
        assert_eq!(groups[0]["sample"].as_array().unwrap().len(), JSON_LIST_CAP);
        assert_eq!(groups[0]["sample_truncated"], true);

        fs::write(paths[0].join("child"), "x").unwrap();
        let mut more = Renewal::default();
        more.record_unwritable(&paths[0]);
        report.add(&more);
        assert_eq!(report.unwritable_json()[0]["all_empty"], false);
        assert_eq!(report.unwritable_json()[0]["count"], paths.len() + 1);
    }

    #[test]
    fn permission_diagnostics_do_not_assume_unreadable_dirs_are_empty() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let locked = dir.path().join("locked");
        fs::create_dir(&locked).unwrap();
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
        let mut report = Renewal::default();
        report.record_unwritable(&locked);
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o700)).unwrap();
        assert!(report.unwritable_json()[0]["all_empty"].is_null());
    }

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

        let walk = enumerate_dir(dir.path().to_str().unwrap());
        assert_eq!(walk.msg, "ok");
        assert!(walk.files.iter().all(|p| p.is_file()));
        // 5 regular files: a.txt, .hidden, sub/b.txt, .gitignore, ignored.txt
        assert_eq!(walk.files.len(), 5);
        // The flagged directory itself comes first, then `sub`.
        assert_eq!(
            walk.dirs,
            [dir.path().to_path_buf(), dir.path().join("sub")]
        );
    }

    #[test]
    fn enumerate_dir_skips_symlinked_dirs() {
        // `find -type d` does not count a symlink to a directory, and the
        // walker does not descend into one either.
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("real")).unwrap();
        fs::write(dir.path().join("real/inside.txt"), "x").unwrap();
        std::os::unix::fs::symlink(dir.path().join("real"), dir.path().join("link")).unwrap();
        let walk = enumerate_dir(dir.path().to_str().unwrap());
        assert_eq!(walk.msg, "ok");
        assert_eq!(walk.dirs.len(), 2); // root + real
        assert_eq!(walk.files.len(), 1); // real/inside.txt, not through the link
    }

    #[test]
    fn enumerate_dir_skips_symlinked_files() {
        // `find -type f` does not count symlinks; neither does the walker.
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("real.txt"), "x").unwrap();
        std::os::unix::fs::symlink(dir.path().join("real.txt"), dir.path().join("link.txt"))
            .unwrap();
        let walk = enumerate_dir(dir.path().to_str().unwrap());
        assert_eq!(walk.msg, "ok");
        assert_eq!(walk.files.len(), 1);
    }

    #[test]
    fn enumerate_dir_not_a_directory() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("nope");
        let walk = enumerate_dir(missing.to_str().unwrap());
        assert!(walk.files.is_empty());
        assert!(walk.dirs.is_empty());
        assert!(walk.msg.starts_with("skipped"));
    }

    /// Backdate an entry so a renewal is visible.
    fn backdate(p: &Path) {
        let old = FileTime::from_unix_time(FileTime::now().unix_seconds() - 8_640_000, 0);
        filetime::set_file_times(p, old, old).unwrap();
    }

    fn is_fresh(p: &Path) -> bool {
        let mtime = FileTime::from_last_modification_time(&p.metadata().unwrap());
        mtime.unix_seconds() > FileTime::now().unix_seconds() - 10
    }

    #[test]
    fn touch_entries_refreshes_times() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("stale.txt");
        fs::write(&f, "x").unwrap();
        backdate(&f);

        let (renewed, errors, _) = touch_entries(std::slice::from_ref(&f));
        assert_eq!((renewed, errors), (1, 0));
        assert!(is_fresh(&f));
    }

    #[test]
    fn touch_entries_refreshes_a_directory() {
        // A directory's own stamp only moves when an entry is added or
        // removed, so `keep` has to touch it directly.
        let dir = tempfile::tempdir().unwrap();
        let sub = dir.path().join("sub");
        fs::create_dir(&sub).unwrap();
        backdate(&sub);

        let (renewed, errors, _) = touch_entries(std::slice::from_ref(&sub));
        assert_eq!((renewed, errors), (1, 0));
        assert!(is_fresh(&sub));
    }

    #[test]
    fn touch_entries_missing_path_is_silent_skip() {
        let dir = tempfile::tempdir().unwrap();
        let ghost = dir.path().join("gone.txt");
        let (renewed, errors, msg) = touch_entries(std::slice::from_ref(&ghost));
        assert_eq!((renewed, errors), (0, 0)); // not renewed, not a failure
        assert_eq!(msg, "ok");
        assert!(!ghost.exists()); // never created
    }

    #[test]
    fn touch_entries_counts_every_failure_in_the_batch() {
        // Every failure counts, and the message names the first one plus
        // how many followed - a whole shard can fail, and one line must not
        // stand for 2000.
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("regular.txt");
        fs::write(&f, "x").unwrap();
        // A path *through* a regular file is ENOTDIR, not NotFound.
        let bad: Vec<PathBuf> = (0..3).map(|i| f.join(format!("ghost-{i}"))).collect();
        let batch: Vec<PathBuf> = bad.iter().cloned().chain([f.clone()]).collect();

        let (renewed, errors, msg) = touch_entries(&batch);
        assert_eq!((renewed, errors), (1, 3));
        assert!(
            msg.starts_with(&format!("touch {}", bad[0].display())),
            "{msg}"
        );
        assert!(msg.ends_with("(and 2 more in this batch)"), "{msg}");
    }

    #[test]
    fn touch_entries_empty_batch() {
        assert_eq!(touch_entries(&[]), (0, 0, "ok".to_string()));
    }

    #[test]
    fn dir_status_line_reports_renewed_counts() {
        assert_eq!(
            dir_status_line(
                &Renewal {
                    files: 1386,
                    dirs: 694,
                    failures: 0,
                    ..Renewal::default()
                },
                "/scratch/sparky/proj"
            ),
            "  ok      1386 files    694 dirs  /scratch/sparky/proj"
        );
    }

    #[test]
    fn dir_status_line_drops_the_ok_tag_when_anything_failed() {
        // What the walk found is not what got renewed: a directory whose
        // every touch was refused must not print as `ok`.
        assert_eq!(
            dir_status_line(
                &Renewal {
                    files: 0,
                    dirs: 0,
                    failures: 1386,
                    ..Renewal::default()
                },
                "/scratch/sparky/proj"
            ),
            "  fail       0 files      0 dirs · 1386 failed  /scratch/sparky/proj"
        );
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
        // Two files plus the kept directory itself; the missing dir is a
        // benign skip, not a failure.
        let result = execute(&plan, 1, &out);
        assert_eq!((result.files, result.dirs, result.failures), (2, 1, 0));
        assert_eq!(result.skipped_count, 1);
        assert_eq!(result.skipped[0]["path"], "/does/not/exist");
    }

    #[test]
    fn execute_renews_accessible_entries_after_partial_walk() {
        use std::os::unix::fs::PermissionsExt;

        for jobs_n in [1, 4] {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path().join(format!("proj-{jobs_n}"));
            let accessible = root.join("accessible.txt");
            let locked = root.join("locked");
            fs::create_dir_all(&locked).unwrap();
            fs::write(&accessible, "x").unwrap();
            fs::write(locked.join("inaccessible.txt"), "x").unwrap();
            backdate(&root);
            backdate(&accessible);
            fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();

            let plan = Plan {
                kept: vec![("pending".to_string(), root.display().to_string())],
                skipped: vec![],
            };
            let out = Out {
                json_mode: true,
                interactive: false,
            };
            let renewal = execute(&plan, jobs_n, &out);

            fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(renewal.failures, 1);
            assert!(renewal.files > 0, "jobs={jobs_n}: {renewal:?}");
            assert!(renewal.dirs > 0, "jobs={jobs_n}: {renewal:?}");
            assert!(is_fresh(&root), "jobs={jobs_n}: root was not renewed");
            assert!(
                is_fresh(&accessible),
                "jobs={jobs_n}: accessible file was not renewed"
            );
        }
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
        // 5 dirs x 7 files, plus each of the 5 kept dirs themselves.
        assert_eq!(
            execute(&plan, 4, &out),
            Renewal {
                files: 35,
                dirs: 5,
                failures: 0,
                ..Renewal::default()
            }
        );
    }

    #[test]
    fn default_jobs_within_bounds() {
        let n = default_jobs();
        assert!((1..=8).contains(&n));
    }
}
