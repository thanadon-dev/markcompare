#!/usr/bin/env python3
"""markcompare - side-by-side folder/file diff as one self-contained HTML file.

stdlib only. no install, no server, no dependencies.

    python markcompare.py DIR_A DIR_B
    python markcompare.py old.py new.py -o report.html
"""
import argparse
import filecmp
import html
import json
import os
import sys
import tempfile
import webbrowser
from difflib import SequenceMatcher

MAX_BYTES = 2 * 1024 * 1024
CONTEXT = 3
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".next", ".idea", ".mypy_cache", ".pytest_cache"}


def walk(root, skip):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if skip:
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out[rel] = full
    return out


def read_lines(path):
    """None = binary, oversized, or unreadable."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_BYTES or b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", "replace").replace("\t", "    ").splitlines()


def differs(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return True
        return not filecmp.cmp(a, b, shallow=False)
    except OSError:
        return True


def esc(s):
    return html.escape(s, quote=False)


def inline(a, b):
    """Character-level highlight for a changed line pair."""
    if len(a) > 400 or len(b) > 400:
        return esc(a), esc(b)
    left = right = ""
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        pa, pb = esc(a[i1:i2]), esc(b[j1:j2])
        if tag == "equal":
            left += pa
            right += pb
        else:
            if pa:
                left += "<i>" + pa + "</i>"
            if pb:
                right += "<i>" + pb + "</i>"
    return left, right


def fold(rows):
    """Mark unchanged rows further than CONTEXT from any change as collapsible."""
    keep = [False] * len(rows)
    for i, row in enumerate(rows):
        if row[0] != "eq":
            for k in range(max(0, i - CONTEXT), min(len(rows), i + CONTEXT + 1)):
                keep[k] = True
    for i, row in enumerate(rows):
        if row[0] == "eq" and not keep[i]:
            row[5] = 1
    return rows


def build_rows(a, b):
    """-> [tag, lnoA, htmlA, lnoB, htmlB, folded]"""
    rows = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(["eq", i1 + k + 1, esc(a[i1 + k]), j1 + k + 1, esc(b[j1 + k]), 0])
        elif tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                la = a[i1 + k] if i1 + k < i2 else None
                lb = b[j1 + k] if j1 + k < j2 else None
                if la is not None and lb is not None:
                    ha, hb = inline(la, lb)
                    rows.append(["ch", i1 + k + 1, ha, j1 + k + 1, hb, 0])
                elif la is not None:
                    rows.append(["del", i1 + k + 1, esc(la), 0, "", 0])
                else:
                    rows.append(["ins", 0, "", j1 + k + 1, esc(lb), 0])
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(["del", k + 1, esc(a[k]), 0, "", 0])
        else:
            for k in range(j1, j2):
                rows.append(["ins", 0, "", k + 1, esc(b[k]), 0])
    return fold(rows)


def one_side(lines, side):
    if side == "a":
        return [["del", i + 1, esc(l), 0, "", 0] for i, l in enumerate(lines)]
    return [["ins", 0, "", i + 1, esc(l), 0] for i, l in enumerate(lines)]


def compare(path_a, path_b, skip=True):
    """-> (files, data). files = [rel, status, nchanges]"""
    if os.path.isfile(path_a) and os.path.isfile(path_b):
        name = os.path.basename(path_a)
        left, right = {name: path_a}, {name: path_b}
    else:
        left, right = walk(path_a, skip), walk(path_b, skip)

    files, data = [], {}
    for rel in sorted(set(left) | set(right), key=str.lower):
        pa, pb = left.get(rel), right.get(rel)
        if pa and pb:
            if not differs(pa, pb):
                files.append([rel, "same", 0])
                continue
            la, lb = read_lines(pa), read_lines(pb)
            if la is None or lb is None:
                files.append([rel, "bin", 0])
                continue
            rows = build_rows(la, lb)
            status = "ch"
        else:
            lines = read_lines(pa or pb)
            if lines is None:
                files.append([rel, "bin", 0])
                continue
            rows = one_side(lines, "a" if pa else "b")
            status = "left" if pa else "right"
        data[rel] = rows
        files.append([rel, status, sum(1 for r in rows if r[0] != "eq")])
    return files, data


TEMPLATE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>markcompare</title>
<link rel=icon href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect x='1.5' y='1.5' width='8' height='8' fill='none' stroke='%23888' stroke-width='1.6'/%3E%3Crect x='6.5' y='6.5' width='8' height='8' fill='none' stroke='%23888' stroke-width='1.6'/%3E%3C/svg%3E">
<style>
:root{
 --bg:#fff; --fg:#1c1e21; --dim:#8b9096; --line:#e6e8eb; --panel:#fafbfc;
 --sel:#eef2f7; --empty:#fafafa;
 --del:#d13438; --ins:#1a7f37; --chg:#b26a00;
 --delbg:#ffeef0; --insbg:#e9f7ee; --chgbg:#fff8e6;
 --delin:#ffc9cd; --insin:#a7e6bd; --chgin:#ffe08a;
}
@media (prefers-color-scheme:dark){:root{
 --bg:#14161a; --fg:#dfe3e8; --dim:#767d87; --line:#252a31; --panel:#181b20;
 --sel:#22272e; --empty:#111317;
 --del:#f0666b; --ins:#4fc47c; --chg:#e0a337;
 --delbg:#2a1618; --insbg:#122318; --chgbg:#2a2210;
 --delin:#5c2226; --insin:#1d4a2e; --chgin:#5a4413;
}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:13px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;
 display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:12px;padding:9px 14px;border-bottom:1px solid var(--line);
 background:var(--panel);flex:none}
header svg{display:block;opacity:.75;flex:none}
.brand{font-weight:600;letter-spacing:-.01em;flex:none}
.roots{color:var(--dim);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.roots b{color:var(--fg);font-weight:500}
.legend{display:flex;gap:11px;font-size:11px;color:var(--dim);flex:none;align-items:center}
.legend span{display:flex;align-items:center;gap:5px}
.sq{width:8px;height:8px;border-radius:2px;flex:none;display:inline-block}
main{flex:1;display:flex;min-height:0}
aside{width:290px;flex:none;border-right:1px solid var(--line);display:flex;flex-direction:column;background:var(--panel)}
.tools{padding:8px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:7px}
#q{width:100%;padding:5px 8px;border:1px solid var(--line);border-radius:5px;background:var(--bg);
 color:var(--fg);font:inherit;font-size:12px}
#q:focus{outline:none;border-color:var(--dim)}
.tools label{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim);cursor:pointer}
#list{flex:1;overflow-y:auto;list-style:none;margin:0;padding:4px 0}
#list li{display:flex;align-items:center;gap:8px;padding:4px 10px;cursor:pointer;font-size:12px;
 white-space:nowrap;overflow:hidden}
#list li:hover{background:var(--sel)}
#list li.on{background:var(--sel);box-shadow:inset 2px 0 0 var(--fg)}
#list .p{overflow:hidden;text-overflow:ellipsis;flex:1}
#list .d{color:var(--dim)}
#list .n{color:var(--dim);font-size:10.5px;font-variant-numeric:tabular-nums}
section{flex:1;display:flex;flex-direction:column;min-width:0}
#head{display:flex;border-bottom:1px solid var(--line);font-size:11.5px;color:var(--dim);flex:none}
#head div{flex:1;width:50%;padding:6px 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#head div+div{border-left:1px solid var(--line)}
#pane{flex:1;overflow:auto;overflow-anchor:none}
table{width:100%;border-collapse:collapse;table-layout:fixed;
 font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace}
col.ln{width:46px}
td{padding:0 8px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}
td.n{text-align:right;color:var(--dim);user-select:none;padding:0 6px;
 font-variant-numeric:tabular-nums;border-right:1px solid var(--line)}
td.c+td.n{border-left:1px solid var(--line)}
tr.rdel td:nth-child(1),tr.rdel td:nth-child(2){background:var(--delbg)}
tr.rins td:nth-child(3),tr.rins td:nth-child(4){background:var(--insbg)}
tr.rch td{background:var(--chgbg)}
tr.rdel td:nth-child(3),tr.rdel td:nth-child(4),
tr.rins td:nth-child(1),tr.rins td:nth-child(2){background:var(--empty)}
tr.rdel i{background:var(--delin)}
tr.rins i{background:var(--insin)}
tr.rch i{background:var(--chgin)}
i{font-style:normal;border-radius:2px}
tr.h{display:none}
tr.fr td{background:var(--panel);color:var(--dim);cursor:pointer;text-align:center;font-size:11px;
 padding:3px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);user-select:none}
tr.fr:hover td{color:var(--fg)}
tr.cur td{box-shadow:inset 0 0 0 1px var(--dim)}
.msg{padding:36px;color:var(--dim);text-align:center;font-size:13px}
kbd{font:inherit;font-size:11px;border:1px solid var(--line);border-radius:3px;padding:0 4px;color:var(--dim)}
</style></head><body>
<header>
<svg width=16 height=16 viewBox="0 0 16 16"><rect x=1.5 y=1.5 width=8 height=8 fill=none stroke=currentColor stroke-width=1.6/><rect x=6.5 y=6.5 width=8 height=8 fill=none stroke=currentColor stroke-width=1.6/></svg>
<span class=brand>markcompare</span>
<span class=roots><b>__ROOT_A__</b> &nbsp;&#8594;&nbsp; <b>__ROOT_B__</b></span>
<span class=legend>
<span><i class=sq style="background:var(--chg)"></i>changed</span>
<span><i class=sq style="background:var(--ins)"></i>added</span>
<span><i class=sq style="background:var(--del)"></i>removed</span>
<span><kbd>j</kbd><kbd>k</kbd>file <kbd>n</kbd><kbd>p</kbd>hunk</span>
</span>
</header>
<main>
<aside>
 <div class=tools>
  <input id=q placeholder="filter files" autocomplete=off spellcheck=false>
  <label><input type=checkbox id=same> show identical files</label>
 </div>
 <ul id=list></ul>
</aside>
<section>
 <div id=head><div id=ha></div><div id=hb></div></div>
 <div id=pane><div class=msg>Select a file</div></div>
</section>
</main>
<script>
var FILES=__FILES__,DATA=__DATA__,RA=__RA__,RB=__RB__;
var COLOR={ch:'var(--chg)',left:'var(--del)',right:'var(--ins)',bin:'var(--dim)',same:'transparent'};
var list=document.getElementById('list'),pane=document.getElementById('pane'),
    q=document.getElementById('q'),same=document.getElementById('same'),
    ha=document.getElementById('ha'),hb=document.getElementById('hb');
var shown=[],cur=-1,hunks=[],hi=-1;

function paint(){
 var f=q.value.toLowerCase(),all=same.checked;
 shown=FILES.filter(function(x){return (all||x[1]!=='same')&&x[0].toLowerCase().indexOf(f)>=0});
 list.innerHTML=shown.map(function(x,i){
  var s=x[0].lastIndexOf('/')+1;
  return '<li data-i='+i+'><i class=sq style="background:'+COLOR[x[1]]+
   (x[1]==='same'?';box-shadow:inset 0 0 0 1px var(--line)':'')+'"></i>'+
   '<span class=p><span class=d>'+x[0].slice(0,s)+'</span>'+x[0].slice(s)+'</span>'+
   (x[2]?'<span class=n>'+x[2]+'</span>':'')+'</li>';
 }).join('')||'<li style="color:var(--dim);cursor:default">no files</li>';
 cur=-1;
}

function tr(r,extra){
 return '<tr class="r'+r[0]+(extra||'')+'"><td class=n>'+(r[1]||'')+'</td><td class=c>'+r[2]+
        '</td><td class=n>'+(r[3]||'')+'</td><td class=c>'+r[4]+'</td></tr>';
}

function show(i){
 var f=shown[i];
 if(!f) return;
 cur=i;
 for(var c=0;c<list.children.length;c++) list.children[c].classList.toggle('on',c===i);
 var li=list.children[i];
 if(li&&li.scrollIntoView) li.scrollIntoView({block:'nearest'});
 ha.textContent=f[1]==='right'?'-':RA+'/'+f[0];
 hb.textContent=f[1]==='left' ?'-':RB+'/'+f[0];
 var rows=DATA[f[0]];
 if(!rows){
  pane.innerHTML='<div class=msg>'+(f[1]==='bin'?'Binary or oversized file - content not compared':'Files are identical')+'</div>';
  hunks=[];return;
 }
 var out=[],k=0,g=0,j;
 while(k<rows.length){
  if(rows[k][5]){
   j=k; while(j<rows.length&&rows[j][5]) j++;
   if(j-k<4){ while(k<j) out.push(tr(rows[k++])); continue; }
   out.push('<tr class=fr data-g='+g+'><td colspan=4>'+(j-k)+' unchanged lines</td></tr>');
   while(k<j) out.push(tr(rows[k++],' h g'+g));
   g++;
  } else out.push(tr(rows[k++]));
 }
 pane.innerHTML='<table><col class=ln><col><col class=ln><col><tbody>'+out.join('')+'</tbody></table>';
 pane.scrollTop=0;
 hunks=[].slice.call(pane.querySelectorAll('tr.rch,tr.rdel,tr.rins')).filter(function(t){
  var p=t.previousElementSibling;
  return !p||!/^r(ch|del|ins)$/.test(p.className);
 });
 hi=-1;
}

function hunk(d){
 if(!hunks.length) return;
 hi=Math.max(0,Math.min(hunks.length-1,hi+d));
 hunks.forEach(function(t){t.classList.remove('cur')});
 hunks[hi].classList.add('cur');
 hunks[hi].scrollIntoView({block:'center'});
}

list.onclick=function(e){
 var li=e.target.closest('li[data-i]');
 if(li) show(+li.dataset.i);
};
pane.onclick=function(e){
 var fr=e.target.closest('tr.fr');
 if(!fr) return;
 pane.querySelectorAll('.g'+fr.dataset.g).forEach(function(t){t.classList.remove('h')});
 fr.parentNode.removeChild(fr);
};
q.oninput=paint;
same.onchange=paint;
document.onkeydown=function(e){
 if(e.target===q){ if(e.key==='Escape') q.blur(); return; }
 if(e.metaKey||e.ctrlKey||e.altKey) return;
 if(e.key==='/'){e.preventDefault();q.focus()}
 else if(e.key==='j'){e.preventDefault();show(Math.min(shown.length-1,cur+1))}
 else if(e.key==='k'){e.preventDefault();show(Math.max(0,cur-1))}
 else if(e.key==='n'){e.preventDefault();hunk(1)}
 else if(e.key==='p'){e.preventDefault();hunk(-1)}
};
paint();
if(shown.length) show(0);
</script></body></html>"""


def render(files, data, root_a, root_b):
    out = TEMPLATE
    for key, val in (
        ("__FILES__", json.dumps(files, separators=(",", ":"))),
        ("__DATA__", json.dumps(data, separators=(",", ":"))),
        ("__RA__", json.dumps(root_a)),
        ("__RB__", json.dumps(root_b)),
        ("__ROOT_A__", esc(root_a)),
        ("__ROOT_B__", esc(root_b)),
    ):
        out = out.replace(key, val)
    return out


def selftest():
    import shutil
    tmp = tempfile.mkdtemp(prefix="mc-test-")
    a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
    os.makedirs(a)
    os.makedirs(b)

    def write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    write(os.path.join(a, "same.txt"), "hello\n")
    write(os.path.join(b, "same.txt"), "hello\n")
    write(os.path.join(a, "edit.txt"), "one\ntwo\nthree\n")
    write(os.path.join(b, "edit.txt"), "one\nTWO\nthree\n")
    write(os.path.join(a, "gone.txt"), "bye\n")
    write(os.path.join(b, "new.txt"), "hi\n")

    files, data = compare(a, b)
    status = dict((f[0], f[1]) for f in files)
    assert status == {"same.txt": "same", "edit.txt": "ch",
                      "gone.txt": "left", "new.txt": "right"}, status
    assert "same.txt" not in data
    assert [r[0] for r in data["edit.txt"]] == ["eq", "ch", "eq"]
    assert "<i>TWO</i>" in data["edit.txt"][1][4], data["edit.txt"][1]
    assert [r[0] for r in data["gone.txt"]] == ["del"]
    assert [r[0] for r in data["new.txt"]] == ["ins"]

    # html escaping survives into the payload
    write(os.path.join(a, "x.txt"), "<script>\n")
    write(os.path.join(b, "x.txt"), "<b>\n")
    _, d2 = compare(a, b)
    assert "&lt;" in d2["x.txt"][0][2] and "<script>" not in d2["x.txt"][0][2]

    # folding: only lines within CONTEXT of a change stay visible
    long_a = ["L%d" % i for i in range(40)]
    long_b = list(long_a)
    long_b[20] = "CHANGED"
    rows = build_rows(long_a, long_b)
    assert sum(r[5] for r in rows) == 40 - (2 * CONTEXT + 1), sum(r[5] for r in rows)

    out = render(files, data, a, b)
    assert "__DATA__" not in out and "__ROOT_A__" not in out and "__RA__" not in out
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest ok")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="markcompare",
                                 description="side-by-side folder/file diff in one HTML file")
    ap.add_argument("a", nargs="?", help="left folder or file")
    ap.add_argument("b", nargs="?", help="right folder or file")
    ap.add_argument("-o", "--out", help="write HTML here instead of a temp file")
    ap.add_argument("--no-open", action="store_true", help="do not launch the browser")
    ap.add_argument("--all-dirs", action="store_true",
                    help="include .git, node_modules and friends")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.selftest:
        selftest()
        return 0
    if not args.a or not args.b:
        ap.error("need two paths")
    for p in (args.a, args.b):
        if not os.path.exists(p):
            ap.error("not found: %s" % p)

    root_a, root_b = os.path.abspath(args.a), os.path.abspath(args.b)
    files, data = compare(root_a, root_b, skip=not args.all_dirs)
    out = args.out or os.path.join(tempfile.gettempdir(), "markcompare.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(files, data, root_a, root_b))

    changed = sum(1 for f in files if f[1] != "same")
    print("%d file(s) scanned, %d differ -> %s" % (len(files), changed, out))
    if not args.no_open:
        webbrowser.open("file:///" + os.path.abspath(out).replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
