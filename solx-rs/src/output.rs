//! Output layer: human rendering vs machine-readable JSON.
//!
//! A CLI driven by an agent should not have to know a flag exists to get
//! parseable output. So:
//!
//! * When stdout is **not a TTY**, data commands emit JSON automatically; on
//!   a TTY they render plain aligned tables. The global `--json` flag forces
//!   JSON anywhere (a human on a terminal gets tables with no flag; an agent
//!   passes `--json`).
//! * All diagnostics, progress, and errors go to **stderr**, so stdout stays
//!   a clean data channel an agent can parse without stripping noise.
//! * Interactivity (whether we may *prompt*) is decided by **stdin**,
//!   separately from the stdout-format decision. A non-interactive session
//!   never blocks on a confirmation prompt.
//!
//! JSON documents are rendered byte-for-byte like Python's
//! `json.dumps(obj, indent=2)` plus a trailing newline: two-space indent,
//! `", "` / `": "` separators, and `\uXXXX` escapes for every non-ASCII
//! character. Payloads are built as [`serde_json::Value`] with the
//! `preserve_order` feature, so object keys serialize in insertion order.

use std::io::{IsTerminal, Write};

use serde_json::Value;

/// A resolved output target: format choice + interactivity.
#[derive(Clone, Copy, Debug)]
pub struct Out {
    /// Emit JSON on the data channel (stdout) instead of a human rendering.
    pub json_mode: bool,
    /// stdin is a TTY, so prompting a human is allowed.
    pub interactive: bool,
}

impl Out {
    /// Build an `Out`, auto-detecting format from the stdout TTY.
    ///
    /// `force_json` (the global `--json` flag) overrides the auto-detect.
    /// `interactive` reflects whether **stdin** is a TTY.
    pub fn auto(force_json: bool) -> Self {
        Out {
            json_mode: force_json || !std::io::stdout().is_terminal(),
            interactive: std::io::stdin().is_terminal(),
        }
    }

    /// A progress / context line. Goes to stderr in every mode.
    pub fn status(&self, msg: &str) {
        eprintln!("{msg}");
    }

    /// An error line. Goes to stderr in every mode.
    pub fn error(&self, msg: &str) {
        eprintln!("{msg}");
    }

    /// Write one clean JSON document to stdout (no color, no wrapping).
    pub fn json(&self, obj: &Value) {
        let mut stdout = std::io::stdout().lock();
        let _ = stdout.write_all(to_python_json(obj).as_bytes());
        let _ = stdout.write_all(b"\n");
        let _ = stdout.flush();
    }

    /// Render something to stdout in human mode.
    pub fn human(&self, text: &str) {
        println!("{text}");
    }

    /// Emit a result: JSON `data` in json mode, else the `human` render.
    ///
    /// `human` is a thunk so the (possibly expensive) rendering is only
    /// built when it will actually be shown. A `None` render prints nothing.
    pub fn emit(&self, data: &Value, human: impl FnOnce() -> Option<String>) {
        if self.json_mode {
            self.json(data);
        } else if let Some(rendered) = human() {
            self.human(&rendered);
        }
    }
}

/// Ask a yes/no question on stderr and read the answer from stdin.
///
/// Callers gate on [`Out::interactive`] first — a non-interactive session
/// must never reach a prompt. Empty input takes `default`; `y`/`yes`
/// (case-insensitive) is true, anything else false.
pub fn confirm(prompt: &str, default: bool) -> bool {
    let hint = if default { "[Y/n]" } else { "[y/N]" };
    eprint!("{prompt} {hint} ");
    let _ = std::io::stderr().flush();
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        return default;
    }
    let answer = line.trim().to_ascii_lowercase();
    if answer.is_empty() {
        return default;
    }
    matches!(answer.as_str(), "y" | "yes")
}

/// Render `v` exactly like Python's `json.dumps(v, indent=2)` (no trailing
/// newline; callers append one per document).
pub fn to_python_json(v: &Value) -> String {
    let mut buf = String::new();
    write_value(v, 0, &mut buf);
    buf
}

fn write_value(v: &Value, indent: usize, buf: &mut String) {
    match v {
        Value::Null => buf.push_str("null"),
        Value::Bool(b) => buf.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => buf.push_str(&n.to_string()),
        Value::String(s) => write_string(s, buf),
        Value::Array(items) => {
            if items.is_empty() {
                buf.push_str("[]");
                return;
            }
            buf.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                push_spaces(buf, indent + 2);
                write_value(item, indent + 2, buf);
                if i + 1 < items.len() {
                    buf.push(',');
                }
                buf.push('\n');
            }
            push_spaces(buf, indent);
            buf.push(']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                buf.push_str("{}");
                return;
            }
            buf.push_str("{\n");
            for (i, (key, val)) in map.iter().enumerate() {
                push_spaces(buf, indent + 2);
                write_string(key, buf);
                buf.push_str(": ");
                write_value(val, indent + 2, buf);
                if i + 1 < map.len() {
                    buf.push(',');
                }
                buf.push('\n');
            }
            push_spaces(buf, indent);
            buf.push('}');
        }
    }
}

fn push_spaces(buf: &mut String, n: usize) {
    for _ in 0..n {
        buf.push(' ');
    }
}

/// Escape a string like Python's `json.dumps` with `ensure_ascii=True`:
/// everything outside `0x20..=0x7E` becomes a `\uXXXX` escape (surrogate
/// pairs for astral-plane characters).
fn write_string(s: &str, buf: &mut String) {
    buf.push('"');
    for c in s.chars() {
        match c {
            '"' => buf.push_str("\\\""),
            '\\' => buf.push_str("\\\\"),
            '\n' => buf.push_str("\\n"),
            '\r' => buf.push_str("\\r"),
            '\t' => buf.push_str("\\t"),
            '\u{8}' => buf.push_str("\\b"),
            '\u{c}' => buf.push_str("\\f"),
            '\u{20}'..='\u{7e}' => buf.push(c),
            _ => {
                let cp = c as u32;
                if cp <= 0xFFFF {
                    buf.push_str(&format!("\\u{cp:04x}"));
                } else {
                    let v = cp - 0x10000;
                    let hi = 0xD800 + (v >> 10);
                    let lo = 0xDC00 + (v & 0x3FF);
                    buf.push_str(&format!("\\u{hi:04x}\\u{lo:04x}"));
                }
            }
        }
    }
    buf.push('"');
}

/// Render `s` like Python's `repr()` for the common case: single quotes,
/// switching to double quotes when the string contains a single quote (and
/// no double quote), with backslash escapes for the usual control characters.
pub fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn json_object_two_space_indent_ordered() {
        let v = json!({"b": 1, "a": [1, 2], "c": {"x": true}});
        assert_eq!(
            to_python_json(&v),
            "{\n  \"b\": 1,\n  \"a\": [\n    1,\n    2\n  ],\n  \"c\": {\n    \"x\": true\n  }\n}"
        );
    }

    #[test]
    fn json_empty_containers_stay_inline() {
        assert_eq!(to_python_json(&json!([])), "[]");
        assert_eq!(to_python_json(&json!({})), "{}");
        assert_eq!(
            to_python_json(&json!({"a": [], "b": {}})),
            "{\n  \"a\": [],\n  \"b\": {}\n}"
        );
    }

    #[test]
    fn json_strings_escape_non_ascii_like_python() {
        // Python: json.dumps("café — ok\t\x7f") == '"caf\\u00e9 \\u2014 ok\\t\\u007f"'
        let v = json!("café — ok\t\u{7f}");
        assert_eq!(to_python_json(&v), "\"caf\\u00e9 \\u2014 ok\\t\\u007f\"");
    }

    #[test]
    fn json_astral_plane_uses_surrogate_pairs() {
        let v = json!("🎉");
        assert_eq!(to_python_json(&v), "\"\\ud83c\\udf89\"");
    }

    #[test]
    fn json_null_and_numbers() {
        let v = json!({"keep": null, "n": 300});
        assert_eq!(to_python_json(&v), "{\n  \"keep\": null,\n  \"n\": 300\n}");
    }

    #[test]
    fn py_repr_quoting() {
        assert_eq!(py_repr("tcsh"), "'tcsh'");
        assert_eq!(py_repr("it's"), "\"it's\"");
        assert_eq!(py_repr("a\"b'c"), "'a\"b\\'c'");
        assert_eq!(py_repr("a\nb"), "'a\\nb'");
    }
}
