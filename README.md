# markcompare

Side-by-side folder and file diff that opens in your browser. One Python file,
stdlib only — no install, no server, no dependencies.

```
python markcompare.py old_folder new_folder
```

Writes a single self-contained HTML file and opens it. That file is portable:
send it to someone and they see the same diff, offline.

## Why it stays fast

- Identical files are detected by size, then a byte compare — never diffed, never
  embedded in the output.
- Only changed files carry data, so the HTML stays small on large trees.
- Unchanged lines outside 3 lines of context are folded away, click to expand.
- Both sides live in one table, so scrolling is always aligned — no scroll-sync
  script to drift.
- `.git`, `node_modules`, `__pycache__`, `dist`, `build`, `.venv` and friends are
  skipped by default.

## Keys

| key | action |
|---|---|
| `j` / `k` | next / previous file |
| `n` / `p` | next / previous change |
| `/` | filter files |

## Options

```
-o, --out FILE   write the HTML here instead of a temp file
--no-open        do not launch the browser
--all-dirs       include .git, node_modules and friends
```

## Notes

Files over 2 MB and binary files are listed but not diffed. Tabs are shown as
four spaces. Comparing two single files works too:

```
python markcompare.py before.py after.py -o report.html
```

Run the built-in check with `python markcompare.py --selftest`.

## License

MIT
