//! Gitignore-style pattern matching for `[keep]` / `~/.solkeep`.
//!
//! This is a port of Python `pathspec`'s `GitIgnoreSpec` (the matcher the
//! Python solx compiles keep rules with), so include/exclude decisions are
//! byte-identical between the two implementations. The dialect is gitignore,
//! not a general glob language:
//!
//! * `*` and `?` never cross a `/`; `**` spans directories.
//! * `[...]` is a character class; a class with no closing `]` makes the
//!   whole pattern invalid, and an invalid pattern is discarded (it matches
//!   nothing) — git's behavior.
//! * `{a,b}` braces are literal characters, not alternation.
//! * A pattern of exactly `/` matches nothing.
//! * A pattern with no `/` (or only a trailing one) matches at any depth;
//!   one with an internal `/` is anchored to the root.
//! * `!` negates. The last matching pattern decides, except that an exact
//!   (non-ancestor) match takes precedence over ancestor-directory matches —
//!   git's re-include-from-excluded-directory edge case.
//!
//! Paths are matched as strings: one leading `/` (or a leading `./`) is
//! stripped, nothing else is canonicalized, and a pattern for a directory
//! also matches any path under it (including forms with a trailing slash).

use regex::Regex;

/// One compiled pattern line: negated or not, plus its anchored regex.
///
/// The regex carries a `ps_d` capture group on the slash that separates the
/// matched directory from a descendant path; a match where `ps_d`
/// participates is an ancestor-directory match (lower precedence), one
/// without it is an exact match.
struct CompiledPattern {
    include: bool,
    regex: Regex,
}

/// An ordered set of gitignore pattern lines compiled for matching.
pub struct GitIgnoreSpec {
    patterns: Vec<CompiledPattern>,
}

/// The regex group name marking an ancestor-directory match.
const DIR_MARK: &str = "ps_d";

impl GitIgnoreSpec {
    /// Compile pattern lines. Blank lines, comments, and invalid patterns
    /// are no-ops.
    pub fn from_lines<I, S>(lines: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let mut patterns = Vec::new();
        for line in lines {
            if let Some((raw_regex, include)) = pattern_to_regex(line.as_ref()) {
                if let Ok(regex) = Regex::new(&raw_regex) {
                    patterns.push(CompiledPattern { include, regex });
                }
            }
        }
        GitIgnoreSpec { patterns }
    }

    /// An empty spec (matches nothing).
    pub fn empty() -> Self {
        GitIgnoreSpec {
            patterns: Vec::new(),
        }
    }

    /// Whether `path` is matched (included) by this spec.
    ///
    /// Patterns are checked last-to-first; the first exact match decides,
    /// and an ancestor-directory match is used only when no pattern matches
    /// exactly.
    pub fn match_file(&self, path: &str) -> bool {
        let norm = normalize_file(path);
        let mut dir_match: Option<bool> = None;
        for pat in self.patterns.iter().rev() {
            let Some(caps) = pat.regex.captures(norm) else {
                continue;
            };
            if caps.name(DIR_MARK).is_some() {
                if dir_match.is_none() {
                    dir_match = Some(pat.include);
                }
            } else {
                return pat.include;
            }
        }
        dir_match.unwrap_or(false)
    }
}

/// Strip one leading `/` (absolute paths match root-anchored patterns) or a
/// leading `./`.
fn normalize_file(path: &str) -> &str {
    if let Some(rest) = path.strip_prefix('/') {
        rest
    } else if let Some(rest) = path.strip_prefix("./") {
        rest
    } else {
        path
    }
}

/// Translate one gitignore pattern line into `(regex, include)`.
/// `None` for a no-op line: blank, comment, the bare `/` pattern, or a
/// pattern with invalid range notation (discarded, like git).
fn pattern_to_regex(pattern: &str) -> Option<(String, bool)> {
    // Trailing whitespace is stripped unless escaped (`\ ` at end).
    let pattern = if pattern.ends_with("\\ ") {
        pattern
    } else {
        pattern.trim_end()
    };

    if pattern.is_empty() || pattern.starts_with('#') || pattern == "/" {
        return None;
    }

    let (include, pattern) = match pattern.strip_prefix('!') {
        Some(rest) => (false, rest),
        None => (true, pattern),
    };

    let mut segs: Vec<&str> = pattern.split('/').collect();
    let is_dir_pattern = segs.last() == Some(&"");

    // Normalize the segments.
    if segs[0].is_empty() {
        // Leading slash: anchored to the root.
        segs.remove(0);
    } else if segs.len() == 1 || (segs.len() == 2 && segs[1].is_empty()) {
        // Single segment (with or without trailing slash): match at any
        // depth, i.e. `**/{pattern}`.
        if segs[0] != "**" {
            segs.insert(0, "**");
        }
    }
    if segs.is_empty() {
        return None;
    }
    if segs.last() == Some(&"") {
        // Trailing slash: match everything under the directory.
        *segs.last_mut().unwrap() = "**";
    }
    // Collapse consecutive `**` segments.
    segs.dedup_by(|a, b| *a == "**" && *b == "**");

    let dir_mark_cg = format!("(?P<{DIR_MARK}>/)");

    // Whole-pattern special cases.
    if segs == ["**"] {
        return Some((
            if is_dir_pattern {
                dir_mark_cg
            } else {
                ".".into()
            },
            include,
        ));
    }
    if segs == ["**", "*"] {
        return Some((".".to_string(), include));
    }
    if segs == ["**", "*", "**"] {
        return Some((
            if is_dir_pattern {
                dir_mark_cg
            } else {
                "/".into()
            },
            include,
        ));
    }

    // Translate segment by segment.
    let mut regex = String::new();
    let mut need_slash = false;
    let end = segs.len() - 1;
    for (i, seg) in segs.iter().enumerate() {
        if *seg == "**" {
            if i == 0 {
                regex.push_str("^(?:.+/)?");
            } else if i < end {
                regex.push_str("(?:/.+)?");
                need_slash = true;
            } else {
                // Trailing `**`: any descendant (dir patterns mark the
                // separating slash).
                if is_dir_pattern {
                    regex.push_str(&dir_mark_cg);
                } else {
                    regex.push('/');
                }
            }
        } else {
            if i == 0 {
                regex.push('^');
            }
            if need_slash {
                regex.push('/');
            }
            if *seg == "*" {
                regex.push_str("[^/]+");
            } else {
                regex.push_str(&translate_segment_glob(seg)?);
            }
            if i == end {
                // Match the path itself, or anything under it.
                regex.push_str(&format!("(?:{dir_mark_cg}|$)"));
            }
            need_slash = true;
        }
    }
    Some((regex, include))
}

/// Translate one path-segment glob to a regex fragment. `None` when the
/// segment carries invalid range notation (an unclosed `[`), which discards
/// the whole pattern.
fn translate_segment_glob(seg: &str) -> Option<String> {
    let chars: Vec<char> = seg.chars().collect();
    let mut regex = String::new();
    let mut escape = false;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        i += 1;

        if escape {
            escape = false;
            push_literal(&mut regex, c);
        } else if c == '\\' {
            escape = true;
        } else if c == '*' {
            regex.push_str("[^/]*");
        } else if c == '?' {
            regex.push_str("[^/]");
        } else if c == '[' {
            // Character class: find the closing bracket. A leading `!`/`^`
            // negates; a `]` right after the (optional) negation is literal.
            let mut j = i;
            if j < chars.len() && (chars[j] == '!' || chars[j] == '^') {
                j += 1;
            }
            if j < chars.len() && chars[j] == ']' {
                j += 1;
            }
            while j < chars.len() && chars[j] != ']' {
                j += 1;
            }
            if j >= chars.len() {
                // Unclosed class: invalid range notation, discard pattern.
                return None;
            }
            j += 1; // one past the closing bracket
            regex.push('[');
            if chars[i] == '!' || chars[i] == '^' {
                regex.push('^');
                i += 1;
            }
            // Copy the class body. Backslashes are literal characters here;
            // characters this regex dialect treats specially inside a class
            // (`]` at the start, `[`, `&`, `~`) are escaped so the class
            // keeps plain gitignore semantics (ranges via `-` still work).
            for (k, &b) in chars[i..j].iter().enumerate() {
                match b {
                    '\\' => regex.push_str("\\\\"),
                    ']' if k + 1 < j - i => regex.push_str("\\]"),
                    '[' | '&' | '~' => {
                        regex.push('\\');
                        regex.push(b);
                    }
                    _ => regex.push(b),
                }
            }
            i = j;
        } else {
            push_literal(&mut regex, c);
        }
    }
    if escape {
        // Trailing bare backslash: invalid pattern.
        return None;
    }
    Some(regex)
}

/// Append `c` to `regex` as a literal character.
fn push_literal(regex: &mut String, c: char) {
    if matches!(
        c,
        '\\' | '.' | '+' | '*' | '?' | '(' | ')' | '|' | '[' | ']' | '{' | '}' | '^' | '$'
    ) {
        regex.push('\\');
    }
    regex.push(c);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(lines: &[&str]) -> GitIgnoreSpec {
        GitIgnoreSpec::from_lines(lines)
    }

    // Vectors generated from Python pathspec's GitIgnoreSpec — the
    // reference implementation this module must agree with.
    // Each row: (pattern lines, path, expected match).
    const VECTORS: &[(&[&str], &str, bool)] = &[
        // Braces are literal characters, never alternation.
        (&["/scratch/sparky/run{1,2}"], "/scratch/sparky/run1", false),
        (&["/scratch/sparky/run{1,2}"], "/scratch/sparky/run2", false),
        (
            &["/scratch/sparky/run{1,2}"],
            "/scratch/sparky/run1/sub",
            false,
        ),
        (
            &["/scratch/sparky/run{1,2}"],
            "/scratch/sparky/run{1,2}",
            true,
        ),
        (
            &["/scratch/sparky/run{1,2}"],
            "/scratch/sparky/run{1,2}/sub",
            true,
        ),
        // A pattern of exactly `/` matches nothing.
        (&["/"], "/scratch/sparky/anything", false),
        (&["/"], "/x", false),
        (&["/"], "/", false),
        // Unclosed `[` discards the pattern entirely.
        (&["/scratch/sparky/run[1"], "/scratch/sparky/run[1", false),
        (&["/scratch/sparky/run[1"], "/scratch/sparky/run1", false),
        (&["/scratch/sparky/run[1"], "/scratch/sparky/run[", false),
        // Trailing-slash path forms still match their include root.
        (&["/scratch/sparky/proj-a"], "/scratch/sparky/proj-a", true),
        (&["/scratch/sparky/proj-a"], "/scratch/sparky/proj-a/", true),
        (
            &["/scratch/sparky/proj-a"],
            "/scratch/sparky/proj-a//",
            true,
        ),
        (
            &["/scratch/sparky/proj-a"],
            "/scratch/sparky/proj-a/.",
            true,
        ),
        (
            &["/scratch/sparky/proj-a"],
            "/scratch/sparky/proj-a/deep/file.bin",
            true,
        ),
        // Only one leading slash is stripped; `//x` is not `/x`.
        (&["/scratch/sparky/proj-a"], "//scratch/sparky/proj", false),
        (
            &["/scratch/sparky/proj-a"],
            "/scratch/sparky/proj-ab",
            false,
        ),
        // `dir/**` matches strict descendants, not the directory itself.
        (
            &["/scratch/sparky/proj-b/**"],
            "/scratch/sparky/proj-b",
            false,
        ),
        (
            &["/scratch/sparky/proj-b/**"],
            "/scratch/sparky/proj-b/x",
            true,
        ),
        // Negation: last match wins, exact beats ancestor-directory.
        (
            &["/scratch/sparky/proj", "!**/__pycache__"],
            "/scratch/sparky/proj/run",
            true,
        ),
        (
            &["/scratch/sparky/proj", "!**/__pycache__"],
            "/scratch/sparky/proj/__pycache__",
            false,
        ),
        (
            &["/scratch/sparky/proj", "!**/__pycache__"],
            "/scratch/sparky/proj/a/__pycache__",
            false,
        ),
        (
            &["/scratch/sparky/proj", "!**/__pycache__"],
            "/scratch/sparky/x",
            false,
        ),
        (&["/a", "!/a/tmp", "/a/tmp/keepme"], "/a/x", true),
        (&["/a", "!/a/tmp", "/a/tmp/keepme"], "/a/tmp", false),
        (&["/a", "!/a/tmp", "/a/tmp/keepme"], "/a/tmp/other", false),
        (&["/a", "!/a/tmp", "/a/tmp/keepme"], "/a/tmp/keepme", true),
        (
            &["/a", "!/a/tmp", "/a/tmp/keepme"],
            "/a/tmp/keepme/sub",
            true,
        ),
        // Directory-only patterns (trailing slash) skip the bare path.
        (&["/scratch/sparky/exp*/"], "/scratch/sparky/exp1", false),
        (&["/scratch/sparky/exp*/"], "/scratch/sparky/exp1/f", true),
        (&["/scratch/sparky/exp*/"], "/scratch/sparky/exp", false),
        // Character classes.
        (&["/scratch/sparky/run[12]"], "/scratch/sparky/run1", true),
        (&["/scratch/sparky/run[12]"], "/scratch/sparky/run2", true),
        (&["/scratch/sparky/run[12]"], "/scratch/sparky/run3", false),
        (&["/scratch/sparky/run[12]"], "/scratch/sparky/run12", false),
        (&["/scratch/sparky/run[!1]"], "/scratch/sparky/run1", false),
        (&["/scratch/sparky/run[!1]"], "/scratch/sparky/run2", true),
        // `?` matches exactly one non-slash character.
        (&["/scratch/sparky/r?n"], "/scratch/sparky/run", true),
        (&["/scratch/sparky/r?n"], "/scratch/sparky/rn", false),
        (&["/scratch/sparky/r?n"], "/scratch/sparky/r/n", false),
        // `**` / `*` whole-pattern forms.
        (&["**"], "/anything", true),
        (&["**"], "/a/b", true),
        (&["*"], "/anything", true),
        (&["*"], "/a/b", true),
        (&["/scratch/**/deep"], "/scratch/deep", true),
        (&["/scratch/**/deep"], "/scratch/a/deep", true),
        (&["/scratch/**/deep"], "/scratch/a/b/deep", true),
        (&["/scratch/**/deep"], "/scratchdeep", false),
        // Anchoring rules for slash-less vs slash-ful patterns.
        (&["bare-name"], "/scratch/sparky/bare-name", true),
        (&["bare-name"], "/bare-name", true),
        (&["bare-name"], "/x/bare-name/y", true),
        (&["dir/sub"], "/dir/sub", true),
        (&["dir/sub"], "/x/dir/sub", false),
        (&["dir/sub"], "/dir/sub/y", true),
        // Spaces and other shell-special characters are plain literals.
        (&["/scratch/sparky/a b/c*"], "/scratch/sparky/a b/cx", true),
        (&["/scratch/sparky/a b/c*"], "/scratch/sparky/a b/d", false),
        // Comments and blanks are no-ops.
        (
            &["# comment", "", "/scratch/sparky/p"],
            "/scratch/sparky/p",
            true,
        ),
    ];

    #[test]
    fn matches_pathspec_reference_vectors() {
        for (lines, path, expected) in VECTORS {
            let got = spec(lines).match_file(path);
            assert_eq!(
                got, *expected,
                "patterns {lines:?} vs path {path:?}: got {got}, want {expected}"
            );
        }
    }

    #[test]
    fn unclosed_class_in_negation_is_discarded() {
        // The discarded `!`-pattern carves nothing out.
        let s = spec(&["/scratch/sparky", "!/scratch/sparky/skip[1"]);
        assert!(s.match_file("/scratch/sparky/skip[1"));
    }

    #[test]
    fn empty_spec_matches_nothing() {
        assert!(!GitIgnoreSpec::empty().match_file("/anything"));
    }
}
