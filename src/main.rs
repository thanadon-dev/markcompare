//! markcompare - side-by-side folder and file diff, rendered as one HTML page.
//!
//!     markcompare                     open the app
//!     markcompare DIR_A DIR_B         write a report and open it
//!     markcompare old.rs new.rs -o report.html

use serde_json::{json, Map, Value};
use similar::{capture_diff_slices, Algorithm, DiffOp};
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::Command;

const MAX_BYTES: u64 = 2 * 1024 * 1024;
const CONTEXT: usize = 3;
const SKIP_DIRS: &[&str] = &[
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".idea",
    ".mypy_cache", ".pytest_cache", "target",
];

const TOKENS: &str = include_str!("tokens.css");
const UI: &str = include_str!("ui.html");
const SHELL: &str = include_str!("shell.html");
const ICON: &str = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' \
     viewBox='0 0 16 16'%3E%3Crect x='1.5' y='1.5' width='8' height='8' fill='none' \
     stroke='%23888' stroke-width='1.6'/%3E%3Crect x='6.5' y='6.5' width='8' height='8' \
     fill='none' stroke='%23888' stroke-width='1.6'/%3E%3C/svg%3E";

#[derive(Clone, Default)]
struct Opts {
    all_dirs: bool,
    ignore_ws: bool,
    exclude: Vec<String>,
}

/// One rendered line pair. Serialised as [tag, lineA, htmlA, lineB, htmlB, folded].
struct Row {
    tag: &'static str,
    la: usize,
    ha: String,
    lb: usize,
    hb: String,
    fold: u8,
}

impl Row {
    fn to_json(&self) -> Value {
        json!([self.tag, self.la, self.ha, self.lb, self.hb, self.fold])
    }
}

// ---------------------------------------------------------------- text utils

fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
    out
}

/// Comparison key for a line: collapses whitespace when --ignore-ws is on.
fn key_of(line: &str, ignore_ws: bool) -> String {
    if !ignore_ws {
        return line.to_string();
    }
    line.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Character-level highlight for a changed line pair.
fn inline(a: &str, b: &str) -> (String, String) {
    if a.len() > 400 || b.len() > 400 {
        return (esc(a), esc(b));
    }
    let ca: Vec<char> = a.chars().collect();
    let cb: Vec<char> = b.chars().collect();
    let (mut left, mut right) = (String::new(), String::new());
    for op in capture_diff_slices(Algorithm::Myers, &ca, &cb) {
        let (tag, ra, rb) = op.as_tag_tuple();
        let pa = esc(&ca[ra].iter().collect::<String>());
        let pb = esc(&cb[rb].iter().collect::<String>());
        if tag == similar::DiffTag::Equal {
            left.push_str(&pa);
            right.push_str(&pb);
        } else {
            if !pa.is_empty() {
                left.push_str("<i>");
                left.push_str(&pa);
                left.push_str("</i>");
            }
            if !pb.is_empty() {
                right.push_str("<i>");
                right.push_str(&pb);
                right.push_str("</i>");
            }
        }
    }
    (left, right)
}

/// Mark unchanged rows further than CONTEXT from any change as collapsible.
fn fold(rows: &mut [Row]) {
    let mut keep = vec![false; rows.len()];
    for i in 0..rows.len() {
        if rows[i].tag != "eq" {
            let lo = i.saturating_sub(CONTEXT);
            let hi = (i + CONTEXT + 1).min(rows.len());
            for k in lo..hi {
                keep[k] = true;
            }
        }
    }
    for (i, row) in rows.iter_mut().enumerate() {
        if row.tag == "eq" && !keep[i] {
            row.fold = 1;
        }
    }
}

fn build_rows(a: &[String], b: &[String], ignore_ws: bool) -> Vec<Row> {
    let ka: Vec<String> = a.iter().map(|l| key_of(l, ignore_ws)).collect();
    let kb: Vec<String> = b.iter().map(|l| key_of(l, ignore_ws)).collect();
    let mut rows = Vec::new();

    for op in capture_diff_slices(Algorithm::Patience, &ka, &kb) {
        match op {
            DiffOp::Equal {
                old_index,
                new_index,
                len,
            } => {
                for k in 0..len {
                    rows.push(Row {
                        tag: "eq",
                        la: old_index + k + 1,
                        ha: esc(&a[old_index + k]),
                        lb: new_index + k + 1,
                        hb: esc(&b[new_index + k]),
                        fold: 0,
                    });
                }
            }
            DiffOp::Delete {
                old_index, old_len, ..
            } => {
                for k in old_index..old_index + old_len {
                    rows.push(Row {
                        tag: "del",
                        la: k + 1,
                        ha: esc(&a[k]),
                        lb: 0,
                        hb: String::new(),
                        fold: 0,
                    });
                }
            }
            DiffOp::Insert {
                new_index, new_len, ..
            } => {
                for k in new_index..new_index + new_len {
                    rows.push(Row {
                        tag: "ins",
                        la: 0,
                        ha: String::new(),
                        lb: k + 1,
                        hb: esc(&b[k]),
                        fold: 0,
                    });
                }
            }
            DiffOp::Replace {
                old_index,
                old_len,
                new_index,
                new_len,
            } => {
                for k in 0..old_len.max(new_len) {
                    let la = (k < old_len).then(|| &a[old_index + k]);
                    let lb = (k < new_len).then(|| &b[new_index + k]);
                    match (la, lb) {
                        (Some(x), Some(y)) => {
                            let (ha, hb) = inline(x, y);
                            rows.push(Row {
                                tag: "ch",
                                la: old_index + k + 1,
                                ha,
                                lb: new_index + k + 1,
                                hb,
                                fold: 0,
                            });
                        }
                        (Some(x), None) => rows.push(Row {
                            tag: "del",
                            la: old_index + k + 1,
                            ha: esc(x),
                            lb: 0,
                            hb: String::new(),
                            fold: 0,
                        }),
                        (None, Some(y)) => rows.push(Row {
                            tag: "ins",
                            la: 0,
                            ha: String::new(),
                            lb: new_index + k + 1,
                            hb: esc(y),
                            fold: 0,
                        }),
                        (None, None) => unreachable!(),
                    }
                }
            }
        }
    }
    fold(&mut rows);
    rows
}

fn one_side(lines: &[String], left: bool) -> Vec<Row> {
    lines
        .iter()
        .enumerate()
        .map(|(i, l)| {
            if left {
                Row {
                    tag: "del",
                    la: i + 1,
                    ha: esc(l),
                    lb: 0,
                    hb: String::new(),
                    fold: 0,
                }
            } else {
                Row {
                    tag: "ins",
                    la: 0,
                    ha: String::new(),
                    lb: i + 1,
                    hb: esc(l),
                    fold: 0,
                }
            }
        })
        .collect()
}

// ------------------------------------------------------------------ file i/o

/// None means binary, oversized, or unreadable: listed but not diffed.
fn read_lines(path: &Path) -> Option<Vec<String>> {
    let meta = fs::metadata(path).ok()?;
    if meta.len() > MAX_BYTES {
        return None;
    }
    let raw = fs::read(path).ok()?;
    if raw[..raw.len().min(8192)].contains(&0) {
        return None;
    }
    let text = String::from_utf8_lossy(&raw).replace('\t', "    ");
    Some(text.lines().map(str::to_string).collect())
}

fn fill(reader: &mut impl Read, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut n = 0;
    while n < buf.len() {
        match reader.read(&mut buf[n..])? {
            0 => break,
            got => n += got,
        }
    }
    Ok(n)
}

fn differs(a: &Path, b: &Path) -> bool {
    let (ma, mb) = match (fs::metadata(a), fs::metadata(b)) {
        (Ok(x), Ok(y)) => (x, y),
        _ => return true,
    };
    if ma.len() != mb.len() {
        return true;
    }
    let (fa, fb) = match (fs::File::open(a), fs::File::open(b)) {
        (Ok(x), Ok(y)) => (x, y),
        _ => return true,
    };
    let (mut ra, mut rb) = (BufReader::new(fa), BufReader::new(fb));
    let (mut ba, mut bb) = (vec![0u8; 65536], vec![0u8; 65536]);
    loop {
        let (na, nb) = match (fill(&mut ra, &mut ba), fill(&mut rb, &mut bb)) {
            (Ok(x), Ok(y)) => (x, y),
            _ => return true,
        };
        if na != nb {
            return true;
        }
        if na == 0 {
            return false;
        }
        if ba[..na] != bb[..nb] {
            return true;
        }
    }
}

/// Shell-style glob with `*` and `?`, matched case-insensitively.
fn glob_match(pat: &[u8], s: &[u8]) -> bool {
    let (mut p, mut i) = (0usize, 0usize);
    let (mut star, mut mark) = (usize::MAX, 0usize);
    while i < s.len() {
        if p < pat.len() && (pat[p] == b'?' || pat[p] == s[i]) {
            p += 1;
            i += 1;
        } else if p < pat.len() && pat[p] == b'*' {
            star = p;
            p += 1;
            mark = i;
        } else if star != usize::MAX {
            p = star + 1;
            mark += 1;
            i = mark;
        } else {
            return false;
        }
    }
    while p < pat.len() && pat[p] == b'*' {
        p += 1;
    }
    p == pat.len()
}

fn excluded(rel: &str, pats: &[String]) -> bool {
    if pats.is_empty() {
        return false;
    }
    let lower = rel.to_lowercase();
    let base = lower.rsplit('/').next().unwrap_or(&lower).to_string();
    pats.iter().any(|p| {
        let p = p.trim().to_lowercase();
        !p.is_empty()
            && (glob_match(p.as_bytes(), lower.as_bytes())
                || glob_match(p.as_bytes(), base.as_bytes()))
    })
}

fn walk_into(dir: &Path, prefix: &str, o: &Opts, out: &mut BTreeMap<String, PathBuf>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let rel = if prefix.is_empty() {
            name.clone()
        } else {
            format!("{}/{}", prefix, name)
        };
        match entry.file_type() {
            Ok(t) if t.is_dir() => {
                if !o.all_dirs && SKIP_DIRS.contains(&name.as_str()) {
                    continue;
                }
                if excluded(&rel, &o.exclude) {
                    continue;
                }
                walk_into(&entry.path(), &rel, o, out);
            }
            Ok(t) if t.is_file() => {
                if !excluded(&rel, &o.exclude) {
                    out.insert(rel, entry.path());
                }
            }
            _ => {}
        }
    }
}

// ----------------------------------------------------------------- comparing

/// Returns (files, data). files entries are [rel, status, adds, dels].
fn compare(a: &Path, b: &Path, o: &Opts) -> (Vec<Value>, Map<String, Value>) {
    let (mut left, mut right) = (BTreeMap::new(), BTreeMap::new());
    if a.is_file() && b.is_file() {
        let name = a
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "file".into());
        left.insert(name.clone(), a.to_path_buf());
        right.insert(name, b.to_path_buf());
    } else {
        walk_into(a, "", o, &mut left);
        walk_into(b, "", o, &mut right);
    }

    let mut names: Vec<String> = left.keys().chain(right.keys()).cloned().collect();
    names.sort_by_key(|n| n.to_lowercase());
    names.dedup();

    let mut files = Vec::new();
    let mut data = Map::new();

    for rel in names {
        let (pa, pb) = (left.get(&rel), right.get(&rel));
        let (rows, status) = match (pa, pb) {
            (Some(pa), Some(pb)) => {
                if !differs(pa, pb) {
                    files.push(json!([rel, "same", 0, 0]));
                    continue;
                }
                match (read_lines(pa), read_lines(pb)) {
                    (Some(la), Some(lb)) => (build_rows(&la, &lb, o.ignore_ws), "ch"),
                    _ => {
                        files.push(json!([rel, "bin", 0, 0]));
                        continue;
                    }
                }
            }
            (Some(pa), None) => match read_lines(pa) {
                Some(lines) => (one_side(&lines, true), "left"),
                None => {
                    files.push(json!([rel, "bin", 0, 0]));
                    continue;
                }
            },
            (None, Some(pb)) => match read_lines(pb) {
                Some(lines) => (one_side(&lines, false), "right"),
                None => {
                    files.push(json!([rel, "bin", 0, 0]));
                    continue;
                }
            },
            (None, None) => unreachable!(),
        };

        // an ignore-whitespace pass can leave a file with no visible changes
        if status == "ch" && rows.iter().all(|r| r.tag == "eq") {
            files.push(json!([rel, "same", 0, 0]));
            continue;
        }

        let adds = rows.iter().filter(|r| r.tag == "ins" || r.tag == "ch").count();
        let dels = rows.iter().filter(|r| r.tag == "del" || r.tag == "ch").count();
        data.insert(
            rel.clone(),
            Value::Array(rows.iter().map(Row::to_json).collect()),
        );
        files.push(json!([rel, status, adds, dels]));
    }
    (files, data)
}

// ----------------------------------------------------------------- rendering

fn base_name(p: &Path) -> String {
    p.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| p.to_string_lossy().into_owned())
}

fn render(files: &[Value], data: &Map<String, Value>, a: &Path, b: &Path) -> String {
    let (na, nb) = (base_name(a), base_name(b));
    let (sa, sb) = (a.to_string_lossy(), b.to_string_lossy());
    UI.replace("__TOKENS__", TOKENS)
        .replace("__ICON__", ICON)
        .replace("__FILES__", &Value::Array(files.to_vec()).to_string())
        .replace("__DATA__", &Value::Object(data.clone()).to_string())
        .replace("__NAME_A__", &json!(na).to_string())
        .replace("__NAME_B__", &json!(nb).to_string())
        .replace("__TEXT_A__", &esc(&na))
        .replace("__TEXT_B__", &esc(&nb))
        .replace("__ROOT_A__", &esc(&sa))
        .replace("__ROOT_B__", &esc(&sb))
}

fn shell() -> String {
    SHELL.replace("__TOKENS__", TOKENS).replace("__ICON__", ICON)
}

// -------------------------------------------------------------------- the app

fn recent_path() -> PathBuf {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".into());
    Path::new(&home).join(".markcompare.json")
}

fn load_recent() -> Vec<[String; 2]> {
    fs::read_to_string(recent_path())
        .ok()
        .and_then(|s| serde_json::from_str::<Vec<[String; 2]>>(&s).ok())
        .map(|mut v| {
            v.truncate(8);
            v
        })
        .unwrap_or_default()
}

fn save_recent(a: &str, b: &str) {
    let pair = [a.to_string(), b.to_string()];
    let mut items = load_recent();
    items.retain(|p| p != &pair);
    items.insert(0, pair);
    items.truncate(8);
    if let Ok(text) = serde_json::to_string(&items) {
        let _ = fs::write(recent_path(), text);
    }
}

/// Percent-decoding for our own `encodeURIComponent` output. `+` is left alone
/// on purpose: this decodes paths, not form fields, and a literal + is legal.
fn pct_decode(s: &str) -> String {
    fn hex(c: u8) -> Option<u8> {
        match c {
            b'0'..=b'9' => Some(c - b'0'),
            b'a'..=b'f' => Some(c - b'a' + 10),
            b'A'..=b'F' => Some(c - b'A' + 10),
            _ => None,
        }
    }
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex(b[i + 1]), hex(b[i + 2])) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query(url: &str, key: &str) -> String {
    let Some((_, qs)) = url.split_once('?') else {
        return String::new();
    };
    for pair in qs.split('&') {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        if k == key {
            return pct_decode(v);
        }
    }
    String::new()
}

fn open_url(url: &str) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let _ = Command::new("cmd")
            .args(["/C", "start", "", url])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
    }
    #[cfg(target_os = "macos")]
    let _ = Command::new("open").arg(url).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = Command::new("xdg-open").arg(url).spawn();
}

fn serve(port: u16) {
    use tiny_http::{Header, Response, Server};

    let server = match Server::http(("127.0.0.1", port)) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("cannot listen on port {}: {}", port, e);
            std::process::exit(1);
        }
    };
    let url = format!("http://127.0.0.1:{}/", server.server_addr().to_ip().unwrap().port());
    println!("markcompare running at {}   (Ctrl+C to stop)", url);
    open_url(&url);

    let html = Header::from_bytes(&b"Content-Type"[..], &b"text/html; charset=utf-8"[..]).unwrap();
    let json_h = Header::from_bytes(&b"Content-Type"[..], &b"application/json"[..]).unwrap();
    let text = Header::from_bytes(&b"Content-Type"[..], &b"text/plain; charset=utf-8"[..]).unwrap();

    for req in server.incoming_requests() {
        let url = req.url().to_string();
        let path = url.split('?').next().unwrap_or("/").to_string();

        let resp = match path.as_str() {
            "/" => Response::from_string(shell()).with_header(html.clone()),
            "/recent" => Response::from_string(json!(load_recent()).to_string())
                .with_header(json_h.clone()),
            "/pick" => {
                let at = query(&url, "at");
                let mut dialog = rfd::FileDialog::new();
                if !at.is_empty() && Path::new(&at).is_dir() {
                    dialog = dialog.set_directory(&at);
                }
                let picked = dialog
                    .pick_folder()
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_default();
                Response::from_string(json!({ "path": picked }).to_string())
                    .with_header(json_h.clone())
            }
            "/diff" => {
                let (a, b) = (query(&url, "a"), query(&url, "b"));
                let (pa, pb) = (PathBuf::from(&a), PathBuf::from(&b));
                if a.is_empty() || !pa.exists() {
                    Response::from_string(format!("Not found: {}", if a.is_empty() { "(empty)" } else { &a }))
                        .with_status_code(400)
                        .with_header(text.clone())
                } else if b.is_empty() || !pb.exists() {
                    Response::from_string(format!("Not found: {}", if b.is_empty() { "(empty)" } else { &b }))
                        .with_status_code(400)
                        .with_header(text.clone())
                } else {
                    let o = Opts {
                        all_dirs: query(&url, "all") == "1",
                        ignore_ws: query(&url, "ws") == "1",
                        exclude: split_pats(&query(&url, "x")),
                    };
                    let (pa, pb) = (abs(&pa), abs(&pb));
                    let (files, data) = compare(&pa, &pb, &o);
                    save_recent(&pa.to_string_lossy(), &pb.to_string_lossy());
                    Response::from_string(render(&files, &data, &pa, &pb)).with_header(html.clone())
                }
            }
            _ => Response::from_string("not found")
                .with_status_code(404)
                .with_header(text.clone()),
        };
        let _ = req.respond(resp);
    }
}

fn split_pats(s: &str) -> Vec<String> {
    s.split(',')
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .map(str::to_string)
        .collect()
}

fn abs(p: &Path) -> PathBuf {
    fs::canonicalize(p)
        .map(|c| {
            // strip the \\?\ prefix Windows canonicalize adds
            let s = c.to_string_lossy().into_owned();
            PathBuf::from(s.strip_prefix(r"\\?\").map(str::to_string).unwrap_or(s))
        })
        .unwrap_or_else(|_| p.to_path_buf())
}

// ------------------------------------------------------------------- the cli

const USAGE: &str = "\
markcompare - side-by-side folder and file diff

    markcompare                     open the app
    markcompare DIR_A DIR_B         write a report and open it

    -o, --out FILE     write the HTML here instead of a temp file
        --no-open      do not launch the browser
        --all-dirs     include .git, node_modules and friends
        --ignore-ws    treat lines that differ only in whitespace as equal
        --exclude PAT  skip paths matching a glob (repeatable, or comma separated)
        --port N       port for the app (default: any free port)
";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut positional: Vec<String> = Vec::new();
    let mut out: Option<String> = None;
    let mut no_open = false;
    let mut port: u16 = 0;
    let mut o = Opts::default();

    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        let next = |i: &mut usize| -> String {
            *i += 1;
            args.get(*i).cloned().unwrap_or_default()
        };
        match a {
            "-h" | "--help" => {
                print!("{}", USAGE);
                return;
            }
            "-o" | "--out" => out = Some(next(&mut i)),
            "--no-open" => no_open = true,
            "--all-dirs" => o.all_dirs = true,
            "--ignore-ws" => o.ignore_ws = true,
            "--exclude" => o.exclude.extend(split_pats(&next(&mut i))),
            "--port" => port = next(&mut i).parse().unwrap_or(0),
            _ => positional.push(a.to_string()),
        }
        i += 1;
    }

    if positional.is_empty() {
        serve(port);
        return;
    }
    if positional.len() != 2 {
        eprintln!("need two paths, or none at all to open the app\n\n{}", USAGE);
        std::process::exit(2);
    }
    let (pa, pb) = (PathBuf::from(&positional[0]), PathBuf::from(&positional[1]));
    for p in [&pa, &pb] {
        if !p.exists() {
            eprintln!("not found: {}", p.display());
            std::process::exit(2);
        }
    }
    let (pa, pb) = (abs(&pa), abs(&pb));
    let (files, data) = compare(&pa, &pb, &o);
    let target = out
        .map(PathBuf::from)
        .unwrap_or_else(|| std::env::temp_dir().join("markcompare.html"));
    if let Err(e) = fs::write(&target, render(&files, &data, &pa, &pb)) {
        eprintln!("cannot write {}: {}", target.display(), e);
        std::process::exit(1);
    }

    let changed = files
        .iter()
        .filter(|f| f[1].as_str() != Some("same"))
        .count();
    println!(
        "{} file(s) scanned, {} differ -> {}",
        files.len(),
        changed,
        target.display()
    );
    if !no_open {
        let full = abs(&target);
        open_url(&format!(
            "file:///{}",
            full.to_string_lossy().replace('\\', "/")
        ));
    }
}

// ----------------------------------------------------------------- the checks

#[cfg(test)]
mod tests {
    use super::*;

    struct Tmp(PathBuf);
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn setup(tag: &str) -> (Tmp, PathBuf, PathBuf) {
        let root = std::env::temp_dir().join(format!("mc-test-{}", tag));
        let _ = fs::remove_dir_all(&root);
        let (a, b) = (root.join("a"), root.join("b"));
        fs::create_dir_all(&a).unwrap();
        fs::create_dir_all(&b).unwrap();
        (Tmp(root), a, b)
    }

    fn write(dir: &Path, name: &str, text: &str) {
        fs::write(dir.join(name), text).unwrap();
    }

    fn status(files: &[Value]) -> BTreeMap<String, String> {
        files
            .iter()
            .map(|f| (f[0].as_str().unwrap().into(), f[1].as_str().unwrap().into()))
            .collect()
    }

    fn tags(data: &Map<String, Value>, key: &str) -> Vec<String> {
        data[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r[0].as_str().unwrap().to_string())
            .collect()
    }

    #[test]
    fn folder_statuses_and_counts() {
        let (_t, a, b) = setup("status");
        write(&a, "same.txt", "hello\n");
        write(&b, "same.txt", "hello\n");
        write(&a, "edit.txt", "one\ntwo\nthree\n");
        write(&b, "edit.txt", "one\nTWO\nthree\n");
        write(&a, "gone.txt", "bye\n");
        write(&b, "new.txt", "hi\n");

        let (files, data) = compare(&a, &b, &Opts::default());
        assert_eq!(
            status(&files),
            [
                ("edit.txt", "ch"),
                ("gone.txt", "left"),
                ("new.txt", "right"),
                ("same.txt", "same"),
            ]
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
        );
        assert!(!data.contains_key("same.txt"));
        assert_eq!(tags(&data, "edit.txt"), ["eq", "ch", "eq"]);
        assert!(data["edit.txt"][1][4].as_str().unwrap().contains("<i>TWO</i>"));
        assert_eq!(tags(&data, "gone.txt"), ["del"]);
        assert_eq!(tags(&data, "new.txt"), ["ins"]);

        let counts: BTreeMap<&str, (u64, u64)> = files
            .iter()
            .map(|f| {
                (
                    f[0].as_str().unwrap(),
                    (f[2].as_u64().unwrap(), f[3].as_u64().unwrap()),
                )
            })
            .collect();
        assert_eq!(counts["edit.txt"], (1, 1));
        assert_eq!(counts["gone.txt"], (0, 1));
        assert_eq!(counts["new.txt"], (1, 0));
        assert_eq!(counts["same.txt"], (0, 0));
    }

    #[test]
    fn html_is_escaped_before_it_reaches_the_page() {
        let (_t, a, b) = setup("escape");
        write(&a, "x.txt", "<script>\n");
        write(&b, "x.txt", "<b>\n");
        let (_, data) = compare(&a, &b, &Opts::default());
        let left = data["x.txt"][0][2].as_str().unwrap();
        assert!(left.contains("&lt;"));
        assert!(!left.contains("<script>"));
    }

    #[test]
    fn only_lines_near_a_change_stay_visible() {
        let a: Vec<String> = (0..40).map(|i| format!("L{}", i)).collect();
        let mut b = a.clone();
        b[20] = "CHANGED".into();
        let rows = build_rows(&a, &b, false);
        let folded: usize = rows.iter().map(|r| r.fold as usize).sum();
        assert_eq!(folded, 40 - (2 * CONTEXT + 1));
    }

    #[test]
    fn ignore_ws_hides_indentation_only_edits() {
        let (_t, a, b) = setup("ws");
        write(&a, "f.txt", "if x:\n    go()\n");
        write(&b, "f.txt", "if x:\n        go()\n");

        let (files, _) = compare(&a, &b, &Opts::default());
        assert_eq!(status(&files)["f.txt"], "ch");

        let opts = Opts {
            ignore_ws: true,
            ..Default::default()
        };
        let (files, data) = compare(&a, &b, &opts);
        assert_eq!(status(&files)["f.txt"], "same");
        assert!(!data.contains_key("f.txt"));
    }

    #[test]
    fn exclude_drops_matching_paths() {
        let (_t, a, b) = setup("exclude");
        fs::create_dir_all(a.join("logs")).unwrap();
        write(&a, "keep.txt", "one\n");
        write(&b, "keep.txt", "two\n");
        write(&a.join("logs"), "run.log", "noise\n");

        let opts = Opts {
            exclude: split_pats("*.log, logs"),
            ..Default::default()
        };
        let (files, _) = compare(&a, &b, &opts);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0][0].as_str().unwrap(), "keep.txt");
    }

    #[test]
    fn glob_handles_stars_and_anchors() {
        assert!(glob_match(b"*.log", b"run.log"));
        assert!(glob_match(b"logs/*", b"logs/run.log"));
        assert!(glob_match(b"*test*", b"src/my_test_file.rs"));
        assert!(glob_match(b"a?c", b"abc"));
        assert!(!glob_match(b"*.log", b"run.log.txt"));
        assert!(!glob_match(b"a?c", b"ac"));
        assert!(glob_match(b"*", b""));
    }

    #[test]
    fn percent_decoding_survives_windows_paths_and_unicode() {
        assert_eq!(pct_decode("D%3A%5Ctmp%5Cv1"), r"D:\tmp\v1");
        assert_eq!(pct_decode("a%2Bb"), "a+b");
        assert_eq!(pct_decode("a+b"), "a+b");
        assert_eq!(pct_decode("%E0%B8%81"), "ก");
        assert_eq!(pct_decode("bad%zz"), "bad%zz");
    }

    #[test]
    fn templates_have_no_placeholders_left() {
        let (_t, a, b) = setup("render");
        write(&a, "f.txt", "one\n");
        write(&b, "f.txt", "two\n");
        let (files, data) = compare(&a, &b, &Opts::default());
        let page = render(&files, &data, &a, &b);
        for token in ["__TOKENS__", "__ICON__", "__DATA__", "__FILES__", "__NAME_A__", "__ROOT_A__"] {
            assert!(!page.contains(token), "{} left in the diff page", token);
        }
        assert!(page.contains("--acc:"));
        let page = shell();
        assert!(!page.contains("__TOKENS__") && !page.contains("__ICON__"));
    }

    #[test]
    fn identical_big_files_are_never_diffed() {
        let (_t, a, b) = setup("identical");
        let body = "x".repeat(200_000);
        write(&a, "big.txt", &body);
        write(&b, "big.txt", &body);
        assert!(!differs(&a.join("big.txt"), &b.join("big.txt")));
        let (files, data) = compare(&a, &b, &Opts::default());
        assert_eq!(files[0][1].as_str().unwrap(), "same");
        assert!(data.is_empty());
    }
}
