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
    """-> (files, data). files = [rel, status, adds, dels]"""
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
                files.append([rel, "same", 0, 0])
                continue
            la, lb = read_lines(pa), read_lines(pb)
            if la is None or lb is None:
                files.append([rel, "bin", 0, 0])
                continue
            rows = build_rows(la, lb)
            status = "ch"
        else:
            lines = read_lines(pa or pb)
            if lines is None:
                files.append([rel, "bin", 0, 0])
                continue
            rows = one_side(lines, "a" if pa else "b")
            status = "left" if pa else "right"
        data[rel] = rows
        adds = sum(1 for r in rows if r[0] in ("ins", "ch"))
        dels = sum(1 for r in rows if r[0] in ("del", "ch"))
        files.append([rel, status, adds, dels])
    return files, data


TEMPLATE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>markcompare</title>
<link rel=icon href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect x='1.5' y='1.5' width='8' height='8' fill='none' stroke='%23888' stroke-width='1.6'/%3E%3Crect x='6.5' y='6.5' width='8' height='8' fill='none' stroke='%23888' stroke-width='1.6'/%3E%3C/svg%3E">
<style>
:root{
 --bg:#ffffff; --fg:#16181d; --dim:#6e7681; --faint:#9aa1ab;
 --line:#e4e7ec; --panel:#f7f8fa; --sel:#e8effb; --empty:#fbfbfc;
 --acc:#2f6feb;
 --del:#cf222e; --ins:#1a7f37; --chg:#bf8700;
 --delbg:#fff0f1; --insbg:#eaf7ef; --chgbg:#fff9ec;
 --delin:#ffccd0; --insin:#a9e7c1; --chgin:#ffe2a0;
 --shadow:0 1px 0 rgba(16,22,32,.04);
}
@media (prefers-color-scheme:dark){:root{
 --bg:#0f1116; --fg:#e2e6ec; --dim:#7d858f; --faint:#5d646d;
 --line:#222731; --panel:#151820; --sel:#1c2740; --empty:#0c0e12;
 --acc:#5b91f5;
 --del:#f2686d; --ins:#4fc47c; --chg:#dda23a;
 --delbg:#2a1517; --insbg:#0f2418; --chgbg:#2a2210;
 --delin:#61242a; --insin:#1e4d30; --chgin:#5d4614;
 --shadow:0 1px 0 rgba(0,0,0,.4);
}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);
 font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI Variable Text","Segoe UI",Roboto,Inter,sans-serif;
 -webkit-font-smoothing:antialiased;display:flex;flex-direction:column;overflow:hidden}
:focus-visible{outline:2px solid var(--acc);outline-offset:1px;border-radius:4px}

header{display:flex;align-items:center;gap:14px;padding:0 14px;height:46px;flex:none;
 border-bottom:1px solid var(--line);background:var(--panel);box-shadow:var(--shadow)}
header svg{display:block;opacity:.8;flex:none}
.brand{font-weight:640;letter-spacing:-.015em;font-size:13.5px;flex:none}
.roots{display:flex;align-items:center;gap:8px;flex:1;min-width:0;color:var(--dim);font-size:12px}
.chip{padding:2px 9px;border:1px solid var(--line);border-radius:999px;background:var(--bg);
 color:var(--fg);font-size:11.5px;max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.legend{display:flex;gap:12px;font-size:11px;color:var(--dim);flex:none;align-items:center}
.legend span{display:flex;align-items:center;gap:5px;white-space:nowrap}
.sq{width:8px;height:8px;border-radius:2.5px;flex:none;display:inline-block}
kbd{font:inherit;font-size:10.5px;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;
 padding:1px 5px;color:var(--dim);background:var(--bg)}

main{flex:1;display:flex;min-height:0}
aside{width:300px;min-width:210px;max-width:560px;flex:none;background:var(--panel);
 border-right:1px solid var(--line);display:flex;flex-direction:column;resize:horizontal;overflow:hidden}
.tools{padding:10px;display:flex;flex-direction:column;gap:8px;border-bottom:1px solid var(--line)}
#q{width:100%;padding:6px 10px;border:1px solid var(--line);border-radius:7px;background:var(--bg);
 color:var(--fg);font:inherit;font-size:12px}
#q::placeholder{color:var(--faint)}
#q:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px color-mix(in srgb,var(--acc) 18%,transparent)}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11px;color:var(--dim)}
.row label{display:flex;align-items:center;gap:6px;cursor:pointer}
.row label input{accent-color:var(--acc);margin:0}
#sum{font-variant-numeric:tabular-nums;white-space:nowrap}
#sum b{font-weight:600}
.plus{color:var(--ins)} .minus{color:var(--del)}

#list{flex:1;overflow-y:auto;list-style:none;margin:0;padding:0 0 8px}
#list .g{position:sticky;top:0;z-index:1;background:var(--panel);color:var(--faint);
 font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;padding:9px 12px 4px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#list li[data-i]{display:flex;align-items:center;gap:8px;margin:0 6px;padding:5px 8px;border-radius:6px;
 cursor:pointer;font-size:12.5px;white-space:nowrap;overflow:hidden}
#list li[data-i]:hover{background:var(--sel)}
#list li.on{background:var(--sel);box-shadow:inset 2px 0 0 var(--acc)}
#list .p{overflow:hidden;text-overflow:ellipsis;flex:1}
#list .b{font-size:10.5px;font-variant-numeric:tabular-nums;flex:none;letter-spacing:-.01em}
#list .empty{padding:22px 12px;text-align:center;color:var(--faint);font-size:12px}

section{flex:1;display:flex;flex-direction:column;min-width:0}
#bar{display:flex;align-items:center;gap:12px;height:38px;padding:0 12px;flex:none;
 border-bottom:1px solid var(--line);background:var(--bg)}
#name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
 font-size:12.5px;font-weight:560}
#name span{color:var(--faint);font-weight:400}
#nav{display:flex;align-items:center;gap:2px;flex:none}
#nav button{border:1px solid var(--line);background:var(--bg);color:var(--dim);border-radius:6px;
 width:24px;height:24px;line-height:1;cursor:pointer;font-size:13px;padding:0}
#nav button:hover:not(:disabled){color:var(--fg);border-color:var(--dim)}
#nav button:disabled{opacity:.35;cursor:default}
#pos{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums;padding:0 8px;white-space:nowrap}
#head{display:flex;flex:none;border-bottom:1px solid var(--line);background:var(--panel);
 font-size:11px;color:var(--dim)}
#head div{flex:1;width:50%;padding:5px 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#head div+div{border-left:1px solid var(--line)}
#pane{flex:1;overflow:auto;overflow-anchor:none;background:var(--bg)}

table{width:100%;border-collapse:collapse;table-layout:fixed;
 font:12px/1.6 ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,"Liberation Mono",monospace}
col.ln{width:50px}
td{padding:0 10px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}
td.n{text-align:right;color:var(--faint);user-select:none;padding:0 7px;
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
i{font-style:normal;border-radius:3px;padding:1px 0}
tr.h{display:none}
tr.fr td{background:var(--panel);color:var(--faint);cursor:pointer;text-align:center;font-size:11px;
 padding:4px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);user-select:none;
 font-family:inherit}
tr.fr:hover td{color:var(--acc)}
tr.cur td{box-shadow:inset 0 1px 0 var(--acc),inset 0 -1px 0 var(--acc)}
.msg{padding:60px 24px;color:var(--faint);text-align:center;font-size:13px}
</style></head><body>
<header>
<svg width=16 height=16 viewBox="0 0 16 16"><rect x=1.5 y=1.5 width=8 height=8 fill=none stroke=currentColor stroke-width=1.6 rx="1.5"/><rect x=6.5 y=6.5 width=8 height=8 fill=none stroke=currentColor stroke-width=1.6 rx="1.5"/></svg>
<span class=brand>markcompare</span>
<span class=roots>
 <span class=chip title="__ROOT_A__">__TEXT_A__</span>
 <svg width=13 height=13 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round><path d="M5 12h13M13 6l6 6-6 6"/></svg>
 <span class=chip title="__ROOT_B__">__TEXT_B__</span>
</span>
<span class=legend>
<span><i class=sq style="background:var(--chg)"></i>changed</span>
<span><i class=sq style="background:var(--ins)"></i>added</span>
<span><i class=sq style="background:var(--del)"></i>removed</span>
<span><kbd>j</kbd><kbd>k</kbd>file</span>
<span><kbd>n</kbd><kbd>p</kbd>change</span>
</span>
</header>
<main>
<aside>
 <div class=tools>
  <input id=q placeholder="Filter files    /" autocomplete=off spellcheck=false>
  <div class=row>
   <span id=sum></span>
   <label><input type=checkbox id=same> identical</label>
  </div>
 </div>
 <ul id=list></ul>
</aside>
<section>
 <div id=bar>
  <span id=name></span>
  <span id=nav>
   <button id=prev title="Previous change (p)">&#8249;</button>
   <span id=pos></span>
   <button id=next title="Next change (n)">&#8250;</button>
  </span>
 </div>
 <div id=head><div id=ha></div><div id=hb></div></div>
 <div id=pane><div class=msg>Select a file</div></div>
</section>
</main>
<script>
var FILES=__FILES__,DATA=__DATA__,NA=__NAME_A__,NB=__NAME_B__;
var COLOR={ch:'var(--chg)',left:'var(--del)',right:'var(--ins)',bin:'var(--dim)',same:'transparent'};
var $=function(id){return document.getElementById(id)};
var list=$('list'),pane=$('pane'),q=$('q'),same=$('same'),fname=$('name'),pos=$('pos'),
    prev=$('prev'),next=$('next'),ha=$('ha'),hb=$('hb');
var shown=[],cur=-1,hunks=[],hi=-1;

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')}

function summary(){
 var c=0,a=0,r=0,adds=0,dels=0;
 FILES.forEach(function(f){
  if(f[1]==='same') return;
  if(f[1]==='ch') c++; else if(f[1]==='right') a++; else if(f[1]==='left') r++;
  adds+=f[2]; dels+=f[3];
 });
 var n=c+a+r;
 $('sum').innerHTML='<b>'+n+'</b> file'+(n===1?'':'s')+
   (adds?' <span class=plus>+'+adds+'</span>':'')+(dels?' <span class=minus>-'+dels+'</span>':'');
}

function paint(){
 var f=q.value.toLowerCase(),all=same.checked;
 shown=FILES.filter(function(x){return (all||x[1]!=='same')&&x[0].toLowerCase().indexOf(f)>=0});
 var out=[],last=null;
 shown.forEach(function(x,i){
  var s=x[0].lastIndexOf('/'),dir=s<0?'.':x[0].slice(0,s),base=x[0].slice(s+1);
  if(dir!==last){if(dir!=='.')out.push('<li class=g title="'+esc(dir)+'">'+esc(dir)+'</li>');last=dir}
  var badge=x[1]==='bin'?'<span class="b" style="color:var(--faint)">bin</span>':
   (x[2]?'<span class="b plus">+'+x[2]+'</span>':'')+(x[3]?'<span class="b minus">-'+x[3]+'</span>':'');
  out.push('<li data-i='+i+' title="'+esc(x[0])+'">'+
   '<i class=sq style="background:'+COLOR[x[1]]+
   (x[1]==='same'?';box-shadow:inset 0 0 0 1px var(--line)':'')+'"></i>'+
   '<span class=p>'+esc(base)+'</span>'+badge+'</li>');
 });
 list.innerHTML=out.join('')||'<li class=empty>No files match</li>';
 cur=-1;
}

function tr(r,extra){
 return '<tr class="r'+r[0]+(extra||'')+'"><td class=n>'+(r[1]||'')+'</td><td class=c>'+r[2]+
        '</td><td class=n>'+(r[3]||'')+'</td><td class=c>'+r[4]+'</td></tr>';
}

function mark(){
 pos.textContent=hunks.length?(hi<0?hunks.length+' changes':(hi+1)+' / '+hunks.length):'';
 prev.disabled=next.disabled=!hunks.length;
}

function show(i){
 var f=shown[i];
 if(!f) return;
 cur=i;
 var lis=list.querySelectorAll('li[data-i]');
 for(var c=0;c<lis.length;c++) lis[c].classList.toggle('on',+lis[c].dataset.i===i);
 var li=list.querySelector('li[data-i="'+i+'"]');
 if(li&&li.scrollIntoView) li.scrollIntoView({block:'nearest'});
 var s=f[0].lastIndexOf('/')+1;
 fname.innerHTML='<span>'+esc(f[0].slice(0,s))+'</span>'+esc(f[0].slice(s));
 ha.textContent=f[1]==='right'?'not in '+NA:NA;
 hb.textContent=f[1]==='left' ?'not in '+NB:NB;
 var rows=DATA[f[0]];
 if(!rows){
  pane.innerHTML='<div class=msg>'+(f[1]==='bin'?'Binary or oversized file &mdash; content not compared':'Files are identical')+'</div>';
  hunks=[];hi=-1;mark();return;
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
 hi=-1;mark();
}

function hunk(d){
 if(!hunks.length) return;
 hi=Math.max(0,Math.min(hunks.length-1,hi+d));
 hunks.forEach(function(t){t.classList.remove('cur')});
 hunks[hi].classList.add('cur');
 hunks[hi].scrollIntoView({block:'center'});
 mark();
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
prev.onclick=function(){hunk(-1)};
next.onclick=function(){hunk(1)};
q.oninput=function(){paint();if(shown.length)show(0)};
same.onchange=function(){paint();if(shown.length)show(0)};
document.onkeydown=function(e){
 if(e.target===q){ if(e.key==='Escape'){q.value='';paint();if(shown.length)show(0);q.blur()} return; }
 if(e.metaKey||e.ctrlKey||e.altKey) return;
 if(e.key==='/'){e.preventDefault();q.focus()}
 else if(e.key==='j'){e.preventDefault();show(Math.min(shown.length-1,cur+1))}
 else if(e.key==='k'){e.preventDefault();show(Math.max(0,cur-1))}
 else if(e.key==='n'){e.preventDefault();hunk(1)}
 else if(e.key==='p'){e.preventDefault();hunk(-1)}
};
summary();paint();
if(shown.length) show(0); else mark();
</script></body></html>"""


def render(files, data, root_a, root_b):
    out = TEMPLATE
    for key, val in (
        ("__FILES__", json.dumps(files, separators=(",", ":"))),
        ("__DATA__", json.dumps(data, separators=(",", ":"))),
        ("__NAME_A__", json.dumps(os.path.basename(root_a.rstrip(r"\/")) or root_a)),
        ("__NAME_B__", json.dumps(os.path.basename(root_b.rstrip(r"\/")) or root_b)),
        ("__TEXT_A__", esc(os.path.basename(root_a.rstrip(r"\/")) or root_a)),
        ("__TEXT_B__", esc(os.path.basename(root_b.rstrip(r"\/")) or root_b)),
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
    counts = dict((f[0], (f[2], f[3])) for f in files)
    assert status == {"same.txt": "same", "edit.txt": "ch",
                      "gone.txt": "left", "new.txt": "right"}, status
    assert "same.txt" not in data
    assert [r[0] for r in data["edit.txt"]] == ["eq", "ch", "eq"]
    assert "<i>TWO</i>" in data["edit.txt"][1][4], data["edit.txt"][1]
    assert [r[0] for r in data["gone.txt"]] == ["del"]
    assert [r[0] for r in data["new.txt"]] == ["ins"]
    assert counts == {"same.txt": (0, 0), "edit.txt": (1, 1),
                      "gone.txt": (0, 1), "new.txt": (1, 0)}, counts

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
    assert "__DATA__" not in out and "__ROOT_A__" not in out and "__NAME_A__" not in out
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
