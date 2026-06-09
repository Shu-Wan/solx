//! Single-file config under `$XDG_CONFIG_HOME/solx/config.toml`.
//!
//! The user runs `solx init` to write a starter file; everything else just
//! reads it. No `[shared]` merge — each `[jobs.<name>]` table is
//! self-contained, which keeps the schema obvious at the cost of repeating
//! a flag across templates if someone really wants that.

use std::fmt;
use std::path::{Path, PathBuf};

use ignore::gitignore::{Gitignore, GitignoreBuilder};

use crate::output::py_repr;

pub const CONFIG_FILENAME: &str = "config.toml";
pub const DEFAULT_START_TIMEOUT: &str = "10m";

/// Any user-facing config problem (missing file, bad schema).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError(pub String);

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ConfigError {}

/// One `[jobs.<name>]` table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobTemplate {
    pub name: String,
    pub partition: String,
    pub time: String,
    pub qos: Option<String>,
    pub gres: Option<String>,
    pub extra_args: Vec<String>,
}

/// Resolved `[keep]` include/exclude as compiled gitignore matchers.
#[derive(Debug)]
pub struct KeepRules {
    include: Gitignore,
    exclude: Gitignore,
    pub raw_include: Vec<String>,
    pub raw_exclude: Vec<String>,
}

impl KeepRules {
    pub fn new(include: &[String], exclude: &[String]) -> Self {
        KeepRules {
            include: build_gitignore(include),
            exclude: build_gitignore(exclude),
            raw_include: include.to_vec(),
            raw_exclude: exclude.to_vec(),
        }
    }

    /// Return `true` if `path` is included and not excluded.
    ///
    /// Matching follows gitignore semantics on absolute paths: a bare path
    /// pattern matches that directory and everything under it, and a `!`
    /// negation flips the most specific / latest match.
    pub fn matches(&self, path: &str) -> bool {
        if !gitignore_includes(&self.include, path) {
            return false;
        }
        !gitignore_includes(&self.exclude, path)
    }
}

/// Build one gitignore matcher rooted at `/` from pattern lines.
/// Comment and blank lines are skipped; an unparseable pattern is dropped.
fn build_gitignore(lines: &[String]) -> Gitignore {
    let mut builder = GitignoreBuilder::new("/");
    for line in lines {
        let _ = builder.add_line(None, line);
    }
    builder.build().unwrap_or_else(|_| Gitignore::empty())
}

/// Whether `path` (or a parent directory) is positively matched by `spec`,
/// with `!` whitelists winning where they apply.
fn gitignore_includes(spec: &Gitignore, path: &str) -> bool {
    spec.matched_path_or_any_parents(Path::new(path), false)
        .is_ignore()
}

#[derive(Debug)]
pub struct Config {
    pub default_shell: String,
    pub default_template: String,
    pub start_timeout_seconds: i64,
    /// `[jobs.<name>]` tables in file order.
    pub templates: Vec<(String, JobTemplate)>,
    pub keep: Option<KeepRules>,
}

impl Config {
    /// Look up a template by name; `ConfigError` if missing.
    pub fn template(&self, name: &str) -> Result<&JobTemplate, ConfigError> {
        if let Some((_, t)) = self.templates.iter().find(|(n, _)| n == name) {
            return Ok(t);
        }
        let mut names: Vec<&str> = self.templates.iter().map(|(n, _)| n.as_str()).collect();
        names.sort_unstable();
        let available = if names.is_empty() {
            "(none)".to_string()
        } else {
            names.join(", ")
        };
        Err(ConfigError(format!(
            "unknown job template {}. defined: {available}",
            py_repr(name)
        )))
    }
}

/// The user's home directory (`$HOME`).
pub fn home_dir() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".to_string()))
}

/// Resolve the config path honoring `XDG_CONFIG_HOME` with the usual fallback.
pub fn config_path() -> PathBuf {
    let base = match std::env::var("XDG_CONFIG_HOME") {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => home_dir().join(".config"),
    };
    base.join("solx").join(CONFIG_FILENAME)
}

/// Load and validate the config from `path`.
pub fn load(path: &Path) -> Result<Config, ConfigError> {
    if !path.exists() {
        return Err(ConfigError(format!(
            "no config at {}. run `solx init` to write a starter file.",
            path.display()
        )));
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| ConfigError(format!("unable to read config at {}: {e}", path.display())))?;
    let raw: toml::Table = text
        .parse()
        .map_err(|e| ConfigError(format!("invalid TOML in {}: {e}", path.display())))?;
    parse(&raw, &path.display().to_string())
}

fn parse(raw: &toml::Table, source: &str) -> Result<Config, ConfigError> {
    let default_shell = require_str(raw, "default_shell", source)?;
    let default_template = require_str(raw, "default_template", source)?;
    let timeout_str = match raw.get("start_timeout") {
        None => DEFAULT_START_TIMEOUT.to_string(),
        Some(toml::Value::String(s)) => s.clone(),
        Some(_) => {
            return Err(ConfigError(format!(
                "{source}: `start_timeout` must be a string like \"10m\""
            )))
        }
    };
    let start_timeout_seconds = parse_duration(&timeout_str)?;

    let jobs_raw = match raw.get("jobs") {
        Some(toml::Value::Table(t)) if !t.is_empty() => t,
        _ => {
            return Err(ConfigError(format!(
                "{source}: at least one [jobs.<name>] table is required"
            )))
        }
    };
    let mut templates = Vec::new();
    for (name, body) in jobs_raw {
        templates.push((name.clone(), parse_template(name, body, source)?));
    }
    if !templates.iter().any(|(n, _)| n == &default_template) {
        return Err(ConfigError(format!(
            "{source}: default_template={} is not defined under [jobs.*]",
            py_repr(&default_template)
        )));
    }

    let keep = parse_keep(raw.get("keep"), source)?;

    Ok(Config {
        default_shell,
        default_template,
        start_timeout_seconds,
        templates,
        keep,
    })
}

fn parse_template(
    name: &str,
    body: &toml::Value,
    source: &str,
) -> Result<JobTemplate, ConfigError> {
    let body = match body {
        toml::Value::Table(t) => t,
        _ => {
            return Err(ConfigError(format!(
                "{source}: [jobs.{name}] must be a table"
            )))
        }
    };
    let ctx = format!("{source}:[jobs.{name}]");
    Ok(JobTemplate {
        name: name.to_string(),
        partition: require_str(body, "partition", &ctx)?,
        time: require_str(body, "time", &ctx)?,
        qos: optional_str(body, "qos", &ctx)?,
        gres: optional_str(body, "gres", &ctx)?,
        extra_args: optional_str_list(body, "extra_args", &ctx)?,
    })
}

pub fn parse_keep(
    body: Option<&toml::Value>,
    source: &str,
) -> Result<Option<KeepRules>, ConfigError> {
    let body = match body {
        None => return Ok(None),
        Some(toml::Value::Table(t)) => t,
        Some(_) => return Err(ConfigError(format!("{source}: [keep] must be a table"))),
    };
    let ctx = format!("{source}:[keep]");
    let include = optional_str_list(body, "include", &ctx)?;
    let exclude = optional_str_list(body, "exclude", &ctx)?;
    if include.is_empty() {
        return Err(ConfigError(format!(
            "{source}: [keep].include must be a non-empty array"
        )));
    }
    Ok(Some(KeepRules::new(&include, &exclude)))
}

/// Load a gitignore-style `~/.solkeep` keep-list into [`KeepRules`].
///
/// The legacy `~/.solkeep` format: each line is a keep pattern, `!` negates
/// (carves a subtree out), `#`/blank lines are ignored, a bare path matches
/// that directory *and everything under it*, and the last matching rule wins.
/// The whole file becomes a single keep matcher (with an empty exclude).
/// Returns `None` if the file is missing or has no effective rules — so
/// `solx keep` can fall through to its "nothing to match" handling.
/// `~/.solkeep` is a deprecated fallback (see `keep::SOLKEEP_REMOVED_IN`);
/// the supported home is the config `[keep]`.
pub fn load_solkeep(path: &Path) -> Option<KeepRules> {
    if !path.exists() {
        return None;
    }
    let text = std::fs::read_to_string(path).ok()?;
    let lines: Vec<String> = text.lines().map(|l| l.to_string()).collect();
    let effective: Vec<String> = lines
        .iter()
        .filter(|ln| {
            let s = ln.trim();
            !s.is_empty() && !s.starts_with('#')
        })
        .cloned()
        .collect();
    if effective.is_empty() {
        return None;
    }
    Some(KeepRules {
        include: build_gitignore(&lines),
        exclude: Gitignore::empty(),
        raw_include: effective,
        raw_exclude: Vec::new(),
    })
}

fn require_str(body: &toml::Table, key: &str, ctx: &str) -> Result<String, ConfigError> {
    match body.get(key) {
        None => Err(ConfigError(format!(
            "{ctx}: required key `{key}` is missing"
        ))),
        Some(toml::Value::String(s)) if !s.is_empty() => Ok(s.clone()),
        Some(_) => Err(ConfigError(format!(
            "{ctx}: `{key}` must be a non-empty string"
        ))),
    }
}

fn optional_str(body: &toml::Table, key: &str, ctx: &str) -> Result<Option<String>, ConfigError> {
    match body.get(key) {
        None => Ok(None),
        Some(toml::Value::String(s)) if !s.is_empty() => Ok(Some(s.clone())),
        Some(_) => Err(ConfigError(format!(
            "{ctx}: `{key}` must be a non-empty string"
        ))),
    }
}

fn optional_str_list(body: &toml::Table, key: &str, ctx: &str) -> Result<Vec<String>, ConfigError> {
    let err = || ConfigError(format!("{ctx}: `{key}` must be an array of strings"));
    match body.get(key) {
        None => Ok(Vec::new()),
        Some(toml::Value::Array(items)) => items
            .iter()
            .map(|v| match v {
                toml::Value::String(s) => Ok(s.clone()),
                _ => Err(err()),
            })
            .collect(),
        Some(_) => Err(err()),
    }
}

/// Parse a string like `"10m"` / `"30s"` / `"1h"` into seconds.
pub fn parse_duration(text: &str) -> Result<i64, ConfigError> {
    let invalid = || {
        ConfigError(format!(
            "invalid duration {}; use forms like \"30s\", \"10m\", \"1h\"",
            py_repr(text)
        ))
    };
    let t = text.trim();
    let digits_end = t.find(|c: char| !c.is_ascii_digit()).ok_or_else(invalid)?;
    if digits_end == 0 {
        return Err(invalid());
    }
    let (digits, rest) = t.split_at(digits_end);
    let rest = rest.trim_start();
    let mut chars = rest.chars();
    let unit = chars.next().ok_or_else(invalid)?;
    if !chars.as_str().trim().is_empty() {
        return Err(invalid());
    }
    let n: i64 = digits.parse().map_err(|_| invalid())?;
    let mult = match unit.to_ascii_lowercase() {
        's' => 1,
        'm' => 60,
        'h' => 3600,
        _ => return Err(invalid()),
    };
    Ok(n * mult)
}

/// Split a `~/.solkeep` file into `([keep].include, [keep].exclude)`.
///
/// `.solkeep` is one gitignore-style list; the import folds it into a config
/// `[keep]` block so an existing keep-list carries over without rewriting.
/// Plain lines become `include`, `!`-prefixed lines become `exclude` (the
/// `!` dropped); `#`/blank lines are skipped. Returns `None` if the file is
/// missing or has no `include` patterns. This is a best-effort import of the
/// common "broad includes + `!` carve-outs" shape — review the result with
/// `solx config show`.
pub fn import_solkeep(path: &Path) -> Option<(Vec<String>, Vec<String>)> {
    if !path.exists() {
        return None;
    }
    let text = std::fs::read_to_string(path).ok()?;
    let mut include = Vec::new();
    let mut exclude = Vec::new();
    for raw in text.lines() {
        let s = raw.trim();
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        if let Some(carved) = s.strip_prefix('!') {
            let carve = carved.trim();
            if !carve.is_empty() {
                // A bare `!` carves nothing — drop it rather than emit "".
                exclude.push(carve.to_string());
            }
        } else {
            include.push(s.to_string());
        }
    }
    if include.is_empty() {
        // A keep-list with no keep patterns is nothing to import.
        return None;
    }
    Some((include, exclude))
}

/// `true` if `path`'s rules can't be split into include/exclude faithfully.
///
/// `~/.solkeep` is gitignore *last-match-wins*; the config `[keep]` block is
/// `include AND NOT exclude` (see [`KeepRules::matches`]). The two agree only
/// when every `!` carve-out comes *after* the positive rules it carves. A
/// positive rule appearing *after* a `!` line is an order-dependent
/// re-include that the split into separate include/exclude lists silently
/// drops — so `solx config import-solkeep` warns when it detects one rather
/// than quietly keeping fewer directories.
pub fn solkeep_is_order_sensitive(path: &Path) -> bool {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return false,
    };
    let mut seen_carve = false;
    for raw in text.lines() {
        let s = raw.trim();
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        if s.starts_with('!') {
            seen_carve = true;
        } else if seen_carve {
            return true;
        }
    }
    false
}

/// The text that `solx init` writes to a fresh config.toml.
///
/// With no `keep`, the `[keep]` block is a commented placeholder using the
/// `sparky` placeholder. When `keep` is given (imported from `~/.solkeep`
/// via [`import_solkeep`]), an active `[keep]` block is written instead.
/// `default_shell` sets the `default_shell` value (the `solx init`
/// walkthrough can pick it).
pub fn starter_config_text(
    keep: Option<&(Vec<String>, Vec<String>)>,
    default_shell: &str,
) -> String {
    let base = STARTER_CONFIG_BASE.replace(
        "default_shell = \"bash\"",
        &format!("default_shell = {}", toml_str(default_shell)),
    );
    let block = match keep {
        Some((include, exclude)) => render_keep_block(include, exclude, "~/.solkeep"),
        None => KEEP_PLACEHOLDER.to_string(),
    };
    base + &block
}

/// Render `s` as a TOML basic string, escaping every char TOML forbids.
///
/// Besides backslash and double-quote, control characters (other than tab)
/// are illegal in a TOML basic string and must be `\uXXXX`-escaped —
/// otherwise a keep pattern carrying a stray control byte would render an
/// unparseable config. Tab is emitted as `\t`.
pub fn toml_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Render a `[keep]` TOML block from include/exclude pattern lists.
///
/// Used by `solx config import-solkeep` to append a migrated keep-list to an
/// existing config.toml. `source` names where the patterns came from, for
/// the provenance comment (the command passes the actual keep-list path).
pub fn render_keep_block(include: &[String], exclude: &[String], source: &str) -> String {
    let mut lines = vec![
        format!("# [keep] imported from {source} — directories `solx keep` renews"),
        "# when Sol flags them. Patterns are gitignore-style (** for recursion).".to_string(),
        "[keep]".to_string(),
        "include = [".to_string(),
    ];
    lines.extend(include.iter().map(|p| format!("  {},", toml_str(p))));
    lines.push("]".to_string());
    if !exclude.is_empty() {
        lines.push("exclude = [".to_string());
        lines.extend(exclude.iter().map(|p| format!("  {},", toml_str(p))));
        lines.push("]".to_string());
    }
    lines.join("\n") + "\n"
}

const STARTER_CONFIG_BASE: &str = r#"# solx config — see https://github.com/Shu-Wan/solx/blob/main/solx/README.md
#
# Used by `solx job jump` when dropping into a shell on a compute node.
default_shell = "bash"

# Default template for `solx job start` when invoked without an argument.
default_template = "default"

# Cap on how long `solx job start` waits for the queue. CLI flag --timeout
# overrides per-run.
start_timeout = "10m"


# Job templates. Run `solx job start <name>` to allocate one.
# Each table is self-contained; repeat flags across templates if needed.

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"


"#;

const KEEP_PLACEHOLDER: &str = r#"# Scratch paths to keep alive when Sol flags them in a warning CSV
# *and* `solx keep` runs. Replace `sparky` with your ASURITE.
# Patterns use gitignore-style globs (** for recursion).
# Uncomment + edit to enable:
#
# [keep]
# include = ["/scratch/sparky/your-project", "/scratch/sparky/experiments/**"]
# exclude = ["**/__pycache__", "**/.venv"]
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    pub const SAMPLE_CONFIG_TOML: &str = r#"default_shell = "zsh"
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

    fn write_config(dir: &Path, text: &str) -> PathBuf {
        let p = dir.join("config.toml");
        fs::write(&p, text).unwrap();
        p
    }

    #[test]
    fn load_full_config() {
        let dir = tempfile::tempdir().unwrap();
        let c = load(&write_config(dir.path(), SAMPLE_CONFIG_TOML)).unwrap();
        assert_eq!(c.default_shell, "zsh");
        assert_eq!(c.default_template, "default");
        assert_eq!(c.start_timeout_seconds, 300);
        let names: Vec<&str> = c.templates.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, ["default", "debug", "gpu"]); // file order preserved

        let gpu = c.template("gpu").unwrap();
        assert_eq!(gpu.partition, "public");
        assert_eq!(gpu.gres.as_deref(), Some("gpu:a100:1"));
        assert_eq!(gpu.time, "0-4");
        assert_eq!(gpu.qos, None);
        assert_eq!(gpu.extra_args, ["--mem=64G", "--cpus-per-task=8"]);
    }

    #[test]
    fn template_lookup_missing_errors() {
        let dir = tempfile::tempdir().unwrap();
        let c = load(&write_config(dir.path(), SAMPLE_CONFIG_TOML)).unwrap();
        let err = c.template("nonexistent").unwrap_err();
        assert_eq!(
            err.0,
            "unknown job template 'nonexistent'. defined: debug, default, gpu"
        );
    }

    #[test]
    fn load_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let err = load(&dir.path().join("absent.toml")).unwrap_err();
        assert!(err.0.contains("run `solx init`"));
    }

    #[test]
    fn invalid_toml() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(dir.path(), "default_shell = [unclosed array");
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("invalid TOML"));
    }

    #[test]
    fn required_default_shell() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_template = \"default\"\n[jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("default_shell"));
        assert!(err.0.contains("required key"));
    }

    #[test]
    fn required_default_template() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_shell = \"bash\"\n[jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("default_template"));
    }

    #[test]
    fn at_least_one_jobs_table() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_shell = \"bash\"\ndefault_template = \"x\"\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err
            .0
            .contains("at least one [jobs.<name>] table is required"));
    }

    #[test]
    fn default_template_must_exist() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_shell = \"bash\"\ndefault_template = \"missing\"\n\n[jobs.default]\npartition = \"x\"\ntime = \"1-0\"\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err
            .0
            .contains("default_template='missing' is not defined under [jobs.*]"));
    }

    #[test]
    fn template_required_keys() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_shell = \"bash\"\ndefault_template = \"default\"\n\n[jobs.default]\npartition = \"x\"\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("`time`"));
    }

    #[test]
    fn extra_args_must_be_string_array() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(
            dir.path(),
            "default_shell = \"bash\"\ndefault_template = \"default\"\n\n[jobs.default]\npartition = \"x\"\ntime = \"1-0\"\nextra_args = [1, 2]\n",
        );
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("extra_args"));
    }

    #[test]
    fn keep_match_include_only() {
        let keep = KeepRules::new(&["/scratch/sparky/proj-a/**".to_string()], &[]);
        assert!(keep.matches("/scratch/sparky/proj-a/data.csv"));
        assert!(!keep.matches("/scratch/sparky/proj-b/data.csv"));
    }

    #[test]
    fn keep_exclude_carve_out() {
        let keep = KeepRules::new(
            &["/scratch/sparky/proj-a/**".to_string()],
            &["**/__pycache__/**".to_string(), "**/.venv/**".to_string()],
        );
        assert!(keep.matches("/scratch/sparky/proj-a/run/data.csv"));
        assert!(!keep.matches("/scratch/sparky/proj-a/run/__pycache__/x.pyc"));
        assert!(!keep.matches("/scratch/sparky/proj-a/.venv/lib/x.py"));
    }

    #[test]
    fn keep_bare_path_matches_dir_and_descendants() {
        let keep = KeepRules::new(&["/scratch/sparky/proj-a".to_string()], &[]);
        assert!(keep.matches("/scratch/sparky/proj-a"));
        assert!(keep.matches("/scratch/sparky/proj-a/deep/file.bin"));
        assert!(!keep.matches("/scratch/sparky/proj-ab"));
    }

    #[test]
    fn keep_exclude_dir_pattern_matches_descendant_dirs() {
        // The config-sample shape: exclude ["**/__pycache__", "**/.venv"]
        // must filter a flagged __pycache__ leaf directory.
        let keep = KeepRules::new(
            &["/scratch/sparky/proj/**".to_string()],
            &["**/__pycache__".to_string(), "**/.venv".to_string()],
        );
        assert!(keep.matches("/scratch/sparky/proj/run-1"));
        assert!(!keep.matches("/scratch/sparky/proj/__pycache__"));
        assert!(!keep.matches("/scratch/sparky/proj/sub/.venv"));
    }

    #[test]
    fn keep_requires_include() {
        let mut table = toml::Table::new();
        table.insert(
            "exclude".to_string(),
            toml::Value::Array(vec![toml::Value::String("x".to_string())]),
        );
        let err = parse_keep(Some(&toml::Value::Table(table)), "t").unwrap_err();
        assert!(err.0.contains("non-empty array"));
    }

    #[test]
    fn keep_absent_is_none() {
        assert!(parse_keep(None, "t").unwrap().is_none());
    }

    #[test]
    fn parse_duration_forms() {
        assert_eq!(parse_duration("30s").unwrap(), 30);
        assert_eq!(parse_duration("10m").unwrap(), 600);
        assert_eq!(parse_duration("1h").unwrap(), 3600);
        assert_eq!(parse_duration(" 5M ").unwrap(), 300);
    }

    #[test]
    fn parse_duration_invalid() {
        let err = parse_duration("never").unwrap_err();
        assert_eq!(
            err.0,
            "invalid duration 'never'; use forms like \"30s\", \"10m\", \"1h\""
        );
        assert!(parse_duration("10x").is_err());
        assert!(parse_duration("m").is_err());
        assert!(parse_duration("10m extra").is_err());
    }

    #[test]
    fn config_path_honors_xdg() {
        // Avoid mutating process env in-test; exercise via integration tests.
        // Here just confirm the suffix shape.
        let p = config_path();
        assert!(p.ends_with("solx/config.toml"));
    }

    #[test]
    fn starter_config_loads_clean() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_config(dir.path(), &starter_config_text(None, "bash"));
        let c = load(&p).unwrap();
        assert_eq!(c.default_shell, "bash");
        assert_eq!(c.default_template, "default");
        assert!(c.template("default").is_ok());
        assert!(c.template("debug").is_ok());
        assert!(c.keep.is_none()); // commented out in starter; user uncomments
    }

    #[test]
    fn starter_config_no_maintainer_name() {
        let text = starter_config_text(None, "bash");
        assert!(!text.contains("swan16"));
        assert!(!text.contains("<asurite>"));
        assert!(text.contains("sparky")); // in the commented [keep] example
    }

    #[test]
    fn load_unreadable_is_config_error() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("config.toml");
        fs::create_dir(&p).unwrap(); // exists, but reading a directory fails
        let err = load(&p).unwrap_err();
        assert!(err.0.contains("unable to read"));
    }

    #[test]
    fn load_solkeep_rules() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(".solkeep");
        fs::write(
            &p,
            "# comment\n/scratch/sparky/proj\n!/scratch/sparky/proj/**/__pycache__\n",
        )
        .unwrap();
        let rules = load_solkeep(&p).unwrap();
        assert!(rules.matches("/scratch/sparky/proj/src")); // kept (prefix)
        assert!(!rules.matches("/scratch/sparky/proj/a/__pycache__")); // negated
        assert!(!rules.matches("/scratch/sparky/other")); // not listed
    }

    #[test]
    fn load_solkeep_missing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_solkeep(&dir.path().join("nope")).is_none());
    }

    #[test]
    fn load_solkeep_comments_only() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(".solkeep");
        fs::write(&p, "# just a comment\n\n").unwrap();
        assert!(load_solkeep(&p).is_none());
    }

    #[test]
    fn import_solkeep_splits_include_exclude() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(".solkeep");
        fs::write(
            &p,
            "# comment\n/scratch/sparky/proj\n/scratch/sparky/exp/**\n!**/__pycache__\n",
        )
        .unwrap();
        let (include, exclude) = import_solkeep(&p).unwrap();
        assert_eq!(include, ["/scratch/sparky/proj", "/scratch/sparky/exp/**"]);
        assert_eq!(exclude, ["**/__pycache__"]);
    }

    #[test]
    fn import_solkeep_missing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(import_solkeep(&dir.path().join("nope")).is_none());
    }

    #[test]
    fn import_solkeep_no_includes() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join(".solkeep");
        fs::write(&p, "# only comments\n!**/__pycache__\n").unwrap();
        assert!(import_solkeep(&p).is_none());
    }

    #[test]
    fn solkeep_order_sensitivity() {
        let dir = tempfile::tempdir().unwrap();
        let lossy = dir.path().join("lossy");
        fs::write(&lossy, "/a\n!/a/tmp\n/a/tmp/keepme\n").unwrap();
        assert!(solkeep_is_order_sensitive(&lossy));

        let clean = dir.path().join("clean");
        fs::write(&clean, "/a\n/b/**\n!**/__pycache__\n").unwrap();
        assert!(!solkeep_is_order_sensitive(&clean));

        assert!(!solkeep_is_order_sensitive(&dir.path().join("absent")));
    }

    #[test]
    fn starter_config_with_imported_keep_round_trips() {
        let dir = tempfile::tempdir().unwrap();
        let keep = (
            vec!["/scratch/sparky/proj".to_string()],
            vec!["**/__pycache__".to_string()],
        );
        let p = write_config(dir.path(), &starter_config_text(Some(&keep), "bash"));
        let c = load(&p).unwrap();
        let rules = c.keep.unwrap();
        assert!(rules.matches("/scratch/sparky/proj/src"));
        assert!(!rules.matches("/scratch/sparky/proj/a/__pycache__"));
    }

    #[test]
    fn render_keep_block_shape() {
        let block = render_keep_block(
            &["/scratch/sparky/proj-a".to_string()],
            &["**/__pycache__".to_string()],
            "/home/sparky/.solkeep",
        );
        assert_eq!(
            block,
            "# [keep] imported from /home/sparky/.solkeep — directories `solx keep` renews\n\
             # when Sol flags them. Patterns are gitignore-style (** for recursion).\n\
             [keep]\n\
             include = [\n  \"/scratch/sparky/proj-a\",\n]\n\
             exclude = [\n  \"**/__pycache__\",\n]\n"
        );
    }

    #[test]
    fn toml_str_escapes() {
        assert_eq!(toml_str("plain"), "\"plain\"");
        assert_eq!(toml_str("a\"b"), "\"a\\\"b\"");
        assert_eq!(toml_str("a\\b"), "\"a\\\\b\"");
        assert_eq!(toml_str("a\tb"), "\"a\\tb\"");
        assert_eq!(toml_str("a\u{1}b"), "\"a\\u0001b\"");
    }
}
