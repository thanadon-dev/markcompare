# markcompare

Side-by-side folder and file diff that opens in your browser. One Rust binary,
about 470 KB, nothing to install and no runtime to ship with it.

## As an app

```
markcompare
```

Double-click the exe for the same thing. It opens a small local app: pick two
folders, press Compare, keep comparing pairs without going back to the terminal.
Browse opens the native folder dialog, or paste a path straight in. Recent pairs
are remembered.

The app listens on 127.0.0.1 only and stops with Ctrl+C.

## As a command

```
markcompare old_folder new_folder
```

Writes a single self-contained HTML file and opens it. That file is portable:
send it to someone and they see the same diff, offline, with no markcompare on
their machine.

## Why it stays fast

- Identical files are detected by size, then a chunked byte compare — never
  diffed, never embedded in the output.
- Only changed files carry data, so the HTML stays small on large trees.
- Unchanged lines outside 3 lines of context are folded away, click to expand.
- Both sides live in one table, so scrolling is always aligned — no scroll-sync
  script to drift, and the column headers sit inside that table so they cannot
  drift either.
- `.git`, `node_modules`, `__pycache__`, `dist`, `build`, `target`, `.venv` and
  friends are skipped by default.

## Keys

| key | action |
|---|---|
| `j` / `k` | next / previous file |
| `n` / `p` | next / previous change |
| `/` | filter files (`Esc` clears) |

The toolbar shows `3 / 12` while you step through changes, with `<` `>` buttons
for the same thing. Drag the divider between the two sides to resize them,
double-click it to go back to even. The sidebar groups files by folder, shows
`+n -n` per file, and can be dragged wider.

## Options

```
-o, --out FILE     write the HTML here instead of a temp file
    --no-open      do not launch the browser
    --all-dirs     include .git, node_modules and friends
    --ignore-ws    treat lines that differ only in whitespace as equal
    --exclude PAT  skip paths matching a glob (repeatable, or comma separated)
    --port N       port for the app (default: any free port)
```

`--exclude` takes shell globs with `*` and `?`, matched case-insensitively
against both the full relative path and the file name:

```
markcompare old new --exclude "*.log, *.min.js, vendor/*"
```

## Build

```
cargo build --release
```

The binary lands in `target/release/`. `cargo test` runs the checks.

## Notes

Files over 2 MB and binary files are listed but not diffed. Tabs are shown as
four spaces. Comparing two single files works too:

```
markcompare before.rs after.rs -o report.html
```

Line diffs use the Patience algorithm, which groups changes in code more
readably than a plain shortest-edit script; character-level highlights inside a
changed line use Myers.

## License

MIT
