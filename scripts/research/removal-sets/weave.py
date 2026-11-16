"""Reference implementation of the model: the program is a function of which requests are on.

Every line ever written is kept, with the request that wrote it, the request that removed it, and
the line it replaced. Lines live inside program units (a function, a class, an import-from line, a
constant, the docstring) that carry their own identity across renames and moves.

    present(unit) = its request is on, or something present still uses it        (kept by use)
    present(line) = its unit is present, its request is on, no request that is on removed it,
                    and the line it replaced (if any) was written by a request that is on

usage:
  weave.py validate <clone>            history reproduced? single requests? the event group?
  weave.py all <clone> <workdir>       smoke-check every on/off combination
  weave.py export <clone> <out.json> [all-results.json]
env: PKG, KEY (answer key); EXTRA="sha:name,sha:name" appends commits after the recorded ones
"""
import ast
import builtins
import difflib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

mode, clone = sys.argv[1], Path(sys.argv[2])
PKG = os.environ["PKG"]
SRCROOT = os.environ.get("SRCROOT", "")
if os.environ.get("GITLOG"):
    _log = subprocess.run(["git", "-C", str(clone), "log", "--first-parent", "--reverse", "--format=%H%x09%s", os.environ.get("REV", "HEAD"), "--",
                           SRCROOT + PKG] + [x for x in os.environ.get("ALSO", "").split(",") if x], capture_output=True, text=True).stdout.splitlines()
    VERS = [l.split("\t")[0] for l in _log]
    SUBJ = [l.split("\t", 1)[1] for l in _log]
    NAMES = [v[:7] for v in VERS]
else:
    _eps = json.load(open(os.environ["KEY"]))["episodes"][PKG]
    VERS = [e["sha"] for e in _eps]
    NAMES = ["init"] + [e["session"] for e in _eps[1:]]
    SUBJ = [e["subject"] for e in _eps]
if os.environ.get("KEEP"):
    _keep = [int(x) for x in os.environ["KEEP"].split(",")]
    _nm = []
    for _j, _i in enumerate(_keep):
        _prev = _keep[_j - 1] if _j else -1
        _nm.append(NAMES[_i] if _i - _prev == 1 else f"uncaptured {_prev + 1}-{_i}")
    VERS, SUBJ, NAMES = [VERS[i] for i in _keep], [SUBJ[i] for i in _keep], _nm
for item in filter(None, os.environ.get("EXTRA", "").split(",")):
    sha, name = item.split(":")
    VERS.append(sha)
    NAMES.append(name)
    SUBJ.append(name)
N = len(VERS)
ALL = frozenset(range(N))


def git(*args):
    return subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)


def show(rev, path):
    p = git("show", f"{rev}:{path}")
    return p.stdout if p.returncode == 0 else None


def tracked(rev):
    return [p for p in git("ls-tree", "-r", "--name-only", rev).stdout.split("\n")
            if p.endswith(".py") or p == "README.md"]


EXT = tuple(os.environ.get("EXT", ".py").split(","))
BRACES = (".ts", ".tsx", ".js", ".mjs", ".cjs")


def ts_tree(text):
    from tree_sitter import Parser
    from ugh.entities.extract import _language
    return Parser(_language("typescript")).parse(text.encode("utf-8"))


def ts_units(text):
    """Top-level statements of a TypeScript/JavaScript file, the same shape split_units returns."""
    lines = text.splitlines(keepends=True)
    root = ts_tree(text).root_node
    body = [n for n in root.children if n.type != "comment"]
    if not body:
        return [("file", text, 1)] if text else []

    def name_of(n):
        f = n.child_by_field_name("name")
        return f.text.decode("utf-8", "replace") if f is not None else None

    def key(n):
        if n.type == "export_statement":
            inner = n.child_by_field_name("declaration")
            return key(inner) if inner is not None else "stmt " + n.text.decode("utf-8", "replace")[:40]
        if n.type in ("function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration",
                      "interface_declaration", "type_alias_declaration", "enum_declaration"):
            return "def " + (name_of(n) or "?")
        if n.type in ("lexical_declaration", "variable_declaration"):
            for c in n.named_children:
                if c.type == "variable_declarator" and name_of(c):
                    return "assign " + name_of(c)
        if n.type == "import_statement":
            src = n.child_by_field_name("source")
            return "from " + (src.text.decode("utf-8", "replace").strip("\"'") if src is not None else "?")
        return "stmt " + n.text.decode("utf-8", "replace")[:40].replace("\n", " ")

    out, prev, seen = [], 0, {}
    for i, n in enumerate(body):
        end = n.end_point[0] + 1 if i < len(body) - 1 else len(lines)
        if end <= prev:
            continue  # two statements on one line: the first one's unit keeps the line
        k = key(n)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            k = f"{k} #{seen[k]}"
        out.append((k, "".join(lines[prev:end]), prev + 1))
        prev = end
    return out


def ts_parents(text):
    """{row: row of the line it sits under} from the syntax tree: the first line of the nearest
    enclosing construct that starts on an earlier row (a block's header, or a statement's first line)."""
    root = ts_tree(text).root_node
    out = {}
    for r, line in enumerate(text.split("\n")):
        if not line.strip():
            continue
        col = len(line) - len(line.lstrip())
        node = root.descendant_for_point_range((r, col), (r, col))
        while node is not None and node.parent is not None:
            if node.start_point[0] < r:
                out[r] = node.start_point[0]
                break
            node = node.parent
    return out


def ts_references(path, text, table, top_by_file):
    """{line number: {(file, name)}} for names a line mentions that are defined at the top of this
    file or imported from another project file. Shadowing by locals is not modelled."""
    here = os.path.dirname(path)
    named, spaces = {}, {}
    for m in re.finditer(r"import\s+(?:type\s+)?(?:\{([^}]*)\}|\*\s+as\s+(\w+)|(\w+))\s+from\s+[\"'](\.[^\"']*)[\"']", text):
        base = os.path.normpath(os.path.join(here, m.group(4)))
        target = next((c for c in (base + ".ts", base + ".tsx", base + ".js", base + "/index.ts", base) if c in table), None)
        if not target:
            continue
        if m.group(1):
            for part in m.group(1).split(","):
                bits = part.replace("type ", "").split(" as ")
                if bits[0].strip():
                    named[bits[-1].strip()] = (target, bits[0].strip())
        elif m.group(2):
            spaces[m.group(2)] = target
    mine = top_by_file.get(path, set())
    out = {}
    for i, line in enumerate(text.split("\n"), 1):
        code = re.sub(r"//.*$", "", re.sub(r"([\"'`])(?:\\.|(?!\1).)*\1", "", line))
        if code.lstrip().startswith("import "):
            for nm, tgt in named.items():
                if re.search(r"\b" + re.escape(nm) + r"\b", code):
                    out.setdefault(i, set()).add(tgt)
            continue
        for m in re.finditer(r"(?:\b(\w+)\.)?\b([A-Za-z_$][\w$]*)\b", code):
            ns, nm = m.group(1), m.group(2)
            if ns in spaces:
                out.setdefault(i, set()).add((spaces[ns], nm))
            elif ns is None and nm in named:
                out.setdefault(i, set()).add(named[nm])
            elif ns is None and nm in mine:
                out.setdefault(i, set()).add((path, nm))
    return out


def ts_errors(text):
    def walk(node):
        return (1 if node.type == "ERROR" or node.is_missing else 0) + sum(walk(c) for c in node.children)
    return walk(ts_tree(text).root_node)


_BASE_ERRS = {}


def parses(path, text, base=None):
    """Whether the text is syntactically sound. For brace languages the test is relative: no more
    syntax errors than the current version of the file already has under this parser."""
    if path.endswith(".py"):
        try:
            ast.parse(text)
            return True, ""
        except SyntaxError as exc:
            return False, exc.msg.split("(")[0].strip()[:48]
    if path.endswith(BRACES):
        if path not in _BASE_ERRS:
            _BASE_ERRS[path] = ts_errors(base) if base else 0
        return ts_errors(text) <= _BASE_ERRS[path], "syntax error"
    return True, ""


def key_of(node, i):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return f"def {node.name}"
    if isinstance(node, ast.ImportFrom):
        return f"from {node.module}"
    if isinstance(node, ast.Import):
        return "import " + ",".join(sorted(a.name for a in node.names))
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return f"assign {node.targets[0].id}"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and i == 0:
        return "docstring"
    if isinstance(node, ast.If):
        return "if " + ast.unparse(node.test)[:40]
    return "stmt " + ast.unparse(node)[:40]


def split_units(path, text):
    """[(key, text, first_line)] covering the file; blank lines before a statement belong to it."""
    if path.endswith(BRACES):
        return ts_units(text)
    if not path.endswith(".py"):
        return [("file", text, 1)]
    lines = text.splitlines(keepends=True)
    body = ast.parse(text).body
    if not body:  # nothing but comments or blank lines
        return [("file", text, 1)] if text else []
    out, prev, seen = [], 0, {}
    for i, node in enumerate(body):
        end = node.end_lineno if i < len(body) - 1 else len(lines)
        k = key_of(node, i)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            k = f"{k} #{seen[k]}"
        out.append((k, "".join(lines[prev:end]), prev + 1))
        prev = end
    return out


def ws(s):
    return " ".join(s.split())


class Line:
    __slots__ = ("id", "texts", "born", "addh", "delh", "uses", "alive", "slot", "luses")

    def __init__(self, lid, text, born, addh):
        self.id, self.texts, self.born, self.addh, self.delh = lid, [(born, text)], born, addh, None
        self.uses, self.alive, self.slot, self.luses = set(), True, lid, {}


class Hunk:
    __slots__ = ("id", "req", "unit", "dels")

    def __init__(self, hid, req, unit):
        self.id, self.req, self.unit, self.dels = hid, req, unit, []


class Unit:
    def __init__(self, uid, key, born, path):
        self.uid, self.keys, self.born, self.died = uid, [(born, key)], born, None
        self.places, self.lines = [(born, path)], []


indent_hist = {}  # path -> [(k, indent width)]
units, lines, hunks, fileweave = {}, {}, {}, {}  # uid -> Unit, id -> Line, id -> Hunk, path -> [(uid, placement k)]


def update_lines(u, text, k):
    active = [ln for ln in u.lines if ln.alive]
    cur = text.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(None, [ws(ln.texts[-1][1]) for ln in active], [ws(c) for c in cur], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ln, c in zip(active[i1:i2], cur[j1:j2]):
                if ln.texts[-1][1] != c:
                    ln.texts.append((k, c))  # same line, respaced
            continue
        old = active[i1:i2]
        h = Hunk(len(hunks), k, u.uid)
        hunks[h.id] = h
        for ln in old:
            ln.delh, ln.alive = h.id, False
            h.dels.append(ln.id)
        anchor = old[-1] if old else (active[i1 - 1] if i1 > 0 else None)
        pos = u.lines.index(anchor) + 1 if anchor is not None else 0
        taken, small = set(), len(old) * (j2 - j1) <= 2500
        for n, c in enumerate(cur[j1:j2]):
            ln = Line(len(lines), c, k, h.id)
            if small:
                best, score = None, 0.55
                for oi, o in enumerate(old):
                    if oi not in taken:
                        r = difflib.SequenceMatcher(None, ws(o.texts[-1][1]), ws(c), autojunk=False).ratio()
                        if r > score:
                            best, score = oi, r
                if best is not None:
                    taken.add(best)
                    ln.slot = old[best].slot
            elif n < len(old):
                ln.slot = old[n].slot
            lines[ln.id] = ln
            u.lines.insert(pos + n, ln)


import io
import re
import tokenize

HEADER = re.compile(r"^(?:async\s+)?(if|elif|else|for|while|try|except|finally|with|def|class|match|case)\b.*:\s*(#.*)?$")
CLAUSE = {"elif": ("if", "elif"), "else": ("if", "elif", "for", "while", "try", "except"),
          "except": ("try", "except"), "finally": ("try", "except", "else")}
CHILD_AT = {}  # header slot -> [(k, did it have anything under it in what was saved at k)]
slot_struct = {}  # slot -> [(k, parent slot, indent relative to parent, lead slot, is header)]


COMPOUND = {"if", "elif", "else", "for", "while", "try", "except", "finally", "with", "def", "class", "match", "case"}


def logical_rows(text):
    """(cont, header): cont maps each continuation row of a statement to the row it starts on
    (rows are 0-based); header maps a starting row to its keyword when the statement opens a block.
    None if the text does not tokenize."""
    cont, header = {}, {}
    start = kw = last = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            if tok.type == tokenize.NEWLINE:
                if start is not None:
                    for r in range(start + 1, tok.start[0]):
                        cont[r] = start
                    if kw in COMPOUND and last == ":":
                        header[start] = kw
                start = kw = last = None
                continue
            if start is None:
                start, kw = tok.start[0] - 1, tok.string
            elif kw == "async" and tok.type == tokenize.NAME and last == "async":
                kw = tok.string
            for r in range(tok.start[0], tok.end[0]):
                if r > start:
                    cont[r] = start
            last = tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return cont, header


def update_struct(u, k, path):
    if path.endswith(BRACES):
        alive = [ln for ln in u.lines if ln.alive]
        up = ts_parents("".join(ln.texts[-1][1] for ln in alive))
        indents = [len(ln.texts[-1][1]) - len(ln.texts[-1][1].lstrip(" ")) for ln in alive]
        for row, ln in enumerate(alive):
            if not ln.texts[-1][1].strip():
                continue
            if row in up and alive[up[row]].texts[-1][1].strip():
                st = (alive[up[row]].slot, indents[row] - indents[up[row]], None, False, True)
            else:
                st = (None, indents[row], None, False, False)
            hist = slot_struct.setdefault(ln.slot, [])
            if not hist or hist[-1][1:] != st:
                hist.append((k,) + st)
        return
    if not path.endswith(".py"):
        return
    alive = [ln for ln in u.lines if ln.alive]
    info = logical_rows("".join(ln.texts[-1][1] for ln in alive))
    cont, header = info if info else ({}, {})
    indents = [len(ln.texts[-1][1]) - len(ln.texts[-1][1].lstrip(" ")) for ln in alive]
    stack, last_at = [], {}
    heads, under = [], set()
    for row, ln in enumerate(alive):
        text = ln.texts[-1][1]
        if not text.strip():
            continue
        indent = indents[row]
        if row in cont:  # the rest of a statement, or the inside of a string: hangs off its first row
            st = (alive[cont[row]].slot, indent - indents[cont[row]], None, False, True)
        else:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent, pind = (stack[-1][1], stack[-1][0]) if stack else (None, 0)
            kw = header.get(row)
            lead = None
            if kw in CLAUSE and indent in last_at and last_at[indent][0] in CLAUSE[kw]:
                lead = last_at[indent][1]
            st = (parent, indent - pind, lead, kw is not None, False)
            if kw is not None:
                heads.append(ln.slot)
            if parent is not None and not text.lstrip().startswith("#"):
                under.add(parent)
            if not text.lstrip().startswith("#"):
                for deeper in [i for i in last_at if i > indent]:
                    del last_at[deeper]
                if kw:
                    stack.append((indent, ln.slot))
                    last_at[indent] = (kw, ln.slot)
                else:
                    last_at.pop(indent, None)
        hist = slot_struct.setdefault(ln.slot, [])
        if not hist or hist[-1][1:] != st:
            hist.append((k,) + st)
    for slot in heads:   # a block saved with nothing under it is what its author saved, not something to tidy away
        CHILD_AT.setdefault(slot, []).append((k, slot in under))


def indent_width(text, fallback):
    steps, level = {}, [0]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.INDENT:
                n = len(tok.string.replace("\t", "    "))
                steps[n - level[-1]] = steps.get(n - level[-1], 0) + 1
                level.append(n)
            elif tok.type == tokenize.DEDENT and len(level) > 1:
                level.pop()
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return fallback
    return max(steps, key=steps.get) if steps else fallback


def similar(a, b):
    return difflib.SequenceMatcher(None, [ws(x) for x in a.splitlines()], [ws(x) for x in b.splitlines()],
                                   autojunk=False).ratio()


LOCALS = {}  # path -> {line number: {local name: line numbers that bind it}}, from the last references() call


def set_local_uses(path, rows):
    """rows: [(line number, Line)] of the file as it stands; record which lines bind what each line reads."""
    at = dict(rows)
    for ln in at.values():
        ln.luses = {}
    for lineno, names in LOCALS.get(path, {}).items():
        if lineno in at:
            at[lineno].luses = {nm: frozenset(at[b].id for b in bl if b in at) for nm, bl in names.items()}


def references(path, text, table, top_by_file):
    """{line number: {(file, name) or (file, None)}} for the project names each line refers to."""
    if path.endswith(BRACES):
        LOCALS[path] = {}
        return ts_references(path, text, table, top_by_file)
    if not path.endswith(".py"):
        return {}
    out, tree = {}, ast.parse(text)

    def file_of(mod):
        for cand in (SRCROOT + mod.replace(".", "/") + ".py", SRCROOT + mod.replace(".", "/") + "/__init__.py"):
            if cand in table:
                return cand
        return None

    alias, sym = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == PKG:
            for a in n.names:
                sub = file_of(f"{n.module}.{a.name}")
                if sub:
                    alias[a.asname or a.name] = sub
                    out.setdefault(n.lineno, set()).add((sub, None))
                elif file_of(n.module):
                    sym[a.asname or a.name] = (file_of(n.module), a.name)
                    out.setdefault(n.lineno, set()).add((file_of(n.module), a.name))
    mine = top_by_file.get(path, set())

    def visit(node, local):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            local = local | {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)} | {
                t.id for t in ast.walk(node) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in alias:
            out.setdefault(node.lineno, set()).add((alias[node.value.id], node.attr))
            out[node.lineno].add((path, node.value.id))   # and the import line that brought the name in
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local:
            if node.id in sym:
                out.setdefault(node.lineno, set()).add(sym[node.id])
                out[node.lineno].add((path, node.id))
            elif node.id in mine:
                out.setdefault(node.lineno, set()).add((path, node.id))
        for child in ast.iter_child_nodes(node):
            visit(child, local)

    visit(tree, set())
    loc = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        binds = {}
        for a in ast.walk(fn.args):
            if isinstance(a, ast.arg):
                binds.setdefault(a.arg, set()).add(fn.lineno)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                binds.setdefault(n.id, set()).add(n.lineno)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
                binds.setdefault(n.name, set()).add(n.lineno)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in binds and n.lineno not in binds[n.id]:
                loc.setdefault(n.lineno, {}).setdefault(n.id, set()).update(binds[n.id])
    LOCALS[path] = loc
    return out


def build():
    prev = {}  # (path, key) -> uid, as of the previous version
    for k, rev in enumerate(VERS):
        cur, order = {}, {}
        for path in tracked(rev):
            if path.endswith(".py"):
                hist = indent_hist.setdefault(path, [])
                width = indent_width(show(rev, path), hist[-1][1] if hist else 4)
                if not hist or hist[-1][1] != width:
                    hist.append((k, width))
            for key, seg, first in split_units(path, show(rev, path)):
                cur[(path, key)] = (seg, first)
                order.setdefault(path, []).append(key)
        gone = [fk for fk in prev if fk not in cur]
        ident = {}
        for fk in [fk for fk in cur if fk not in prev]:  # a unit that reappears under a new name or file
            best = max(gone, key=lambda g: similar("".join(l.texts[-1][1] for l in units[prev[g]].lines if l.alive),
                                                   cur[fk][0]), default=None)
            if best and fk[1].split(" ")[0] == best[1].split(" ")[0] == "def" and similar(
                    "".join(l.texts[-1][1] for l in units[prev[best]].lines if l.alive), cur[fk][0]) >= 0.6:
                ident[fk] = prev[best]
                gone.remove(best)
        now = {}
        for path, keys in order.items():
            weave = fileweave.setdefault(path, [])
            last = None
            for key in keys:
                fk = (path, key)
                if fk in prev:
                    u = units[prev[fk]]
                elif fk in ident:
                    u = units[ident[fk]]
                    u.keys.append((k, key))
                    if u.places[-1][1] != path:
                        u.places.append((k, path))
                        at = weave.index(last) + 1 if last in weave else 0
                        weave.insert(at, (u.uid, k))
                else:
                    u = Unit(len(units), key, k, path)
                    units[u.uid] = u
                    at = weave.index(last) + 1 if last in weave else 0
                    weave.insert(at, (u.uid, k))
                update_lines(u, cur[fk][0], k)
                update_struct(u, k, path)
                now[fk] = u.uid
                last = next(e for e in weave if e[0] == u.uid and e[1] == u.places[-1][0])
        for fk in gone:
            u = units[prev[fk]]
            u.died = k
            for ln in u.lines:
                ln.alive = False
        # what each line of this version refers to
        table = {p for p, _ in cur}
        top, binder = {}, {}
        for (p, key), (seg, _) in cur.items():
            if not p.endswith(".py"):
                continue
            for node in ast.parse(seg).body:
                names = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.Import):
                    names = [(a.asname or a.name).split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):   # also imports from inside the package: code that uses the name uses this line
                    names = [a.asname or a.name for a in node.names]
                for nm in names:
                    top.setdefault(p, set()).add(nm)
                    binder[(p, nm)] = now[(p, key)]
        head_unit = {}
        for (p, key), uid in now.items():
            head_unit.setdefault(p, uid)
            if key == "docstring":
                head_unit[p] = uid
        for path in order:
            refs = references(path, show(rev, path), table, top)
            set_local_uses(path, [(cur[(path, key)][1] + off, ln) for key in order[path]
                                  for off, ln in enumerate(l for l in units[now[(path, key)]].lines if l.alive)])
            for key in order[path]:
                u, (seg, first) = units[now[(path, key)]], cur[(path, key)]
                for off, ln in enumerate(l for l in u.lines if l.alive):
                    for tf, name in refs.get(first + off, ()):
                        tgt = head_unit.get(tf) if name is None else binder.get((tf, name))
                        if tgt is not None and tgt != u.uid:
                            ln.uses.add(tgt)
        prev = now


def width_at(path, k):
    hist = [w for kk, w in indent_hist.get(path, []) if kk <= k]
    return hist[-1] if hist else None


def reindent(text, src, dst):
    if not src or not dst or src == dst:
        return text
    body = text.lstrip(" ")
    n = len(text) - len(body)
    return " " * (n // src * dst) + body if n and n % src == 0 and body.strip() else text


RULE = os.environ.get("RULE", "rewrite")
USES_LINES = {}  # line id -> ids of the lines that declare what it uses
KEEP_PLACES = set()  # (commit, definition): where that commit put the definition holds although the commit is off
ALSO_DEL = {}  # line id -> further hunks that removed it (edits made while the first remover was off)
STAY_DEAD = set()  # definitions whose deletion is not undone, although the request that deleted them is off
USE_TOKENS = {}  # line id -> {(name as written, id of the line that declares it)}
DECL_NAME = {}   # id of a declaring line -> the name it declares


def current_names(files):
    """A name is a pointer to a declaration. Print each one as that declaration is called in the
    program being shown, so old code that comes back follows later renames."""
    if not USE_TOKENS:
        return files
    shown = {x for ids_ in LAST_MAP.values() for x in ids_}
    called = {}
    for y in shown:
        if len(DECL_NAME.get(y, ())) == 1:
            called[lines[y].slot] = next(iter(DECL_NAME[y]))
    out = dict(files)
    for path, ids_ in LAST_MAP.items():
        if path not in files:
            continue
        rows_, changed = files[path].splitlines(keepends=True), False
        for n_, x in enumerate(ids_):
            for name, t in USE_TOKENS.get(x, ()):
                new = called.get(lines[t].slot) if len(DECL_NAME.get(t, ())) == 1 else None
                if new and new != name and n_ < len(rows_):
                    fixed = re.sub(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", new, rows_[n_])
                    if fixed != rows_[n_]:
                        rows_[n_], changed = fixed, True
        if changed:
            out[path] = "".join(rows_)
    return out

HUNK_LINES, _ROOTS = {}, {}
CLASS_LINE = re.compile(r"^\s*(export\s+)?(default\s+)?(abstract\s+)?class\b")


def member_roots(hid):
    """The functions a change touched: for each of its lines, the function (or, inside a class, the
    method) it sits in, named by the line that opens it."""
    if os.environ.get("MEMBER_KEEP", "unit") == "unit":   # coarser variant: the whole top-level definition
        return frozenset({-1 - hunks[hid].unit})
    if hid in _ROOTS:
        return _ROOTS[hid]
    if not HUNK_LINES:
        for ln in lines.values():
            HUNK_LINES.setdefault(ln.addh, []).append(ln)
    roots = set()
    for ln in HUNK_LINES.get(hid, ()):
        chain, slot, guard = [], ln.slot, 0
        while slot is not None and guard < 60:
            chain.append(slot)
            hist = slot_struct.get(slot)
            slot = hist[-1][1] if hist else None
            guard += 1
        if not chain or ln.slot not in slot_struct:
            continue
        top = lines.get(chain[-1])
        in_class = top is not None and CLASS_LINE.match(top.texts[-1][1]) is not None
        roots.add(chain[-2] if in_class and len(chain) >= 2 else chain[-1])
    _ROOTS[hid] = frozenset(roots)
    return _ROOTS[hid]



BIRTH = {}  # unit -> the block that introduced it
UNIT_HUNKS = {}
LAST_MAP = {}  # path -> line ids of the last program() output, row by row


def program(on, keep=True, strict=False, forced=frozenset(), rule=None, off_hunks=frozenset()):
    rule = rule or RULE
    if not BIRTH:
        for u_ in units.values():
            BIRTH[u_.uid] = next((l_.addh for l_ in u_.lines if l_.born == u_.born), None)
        for h_ in hunks.values():
            UNIT_HUNKS.setdefault(h_.unit, []).append(h_)
    """({path: text}, kept units {uid: referring line}, kept hunks {hunk: rewriting hunk}, dormant
    line ids) for the requests in `on`.

    Default: nothing a request that is on depends on is taken away. A unit something still uses
    stays; a block a later request rewrote stays as that request left it. `strict` takes those out
    as well, and the edits that sat on them go dormant."""
    if os.environ.get("TOUCH_KEEPS") == "1":   # a definition that a request still on changed stays, though its creator is off
        forced = frozenset(forced) | {BIRTH[u_.uid] for u_ in units.values() if u_.born not in on and BIRTH.get(u_.uid) is not None
                                      and any(h_.req in on and h_.id not in off_hunks for h_ in UNIT_HUNKS.get(u_.uid, ()))}
    kept = {}
    given = frozenset(forced)
    while True:
        def here(u):
            return ((u.born in on or u.uid in kept or BIRTH.get(u.uid) in forced) and BIRTH.get(u.uid) not in off_hunks
                    and not (u.died is not None and u.died in on) and u.uid not in STAY_DEAD)

        wants = lambda h: h.req in on or (h.req == units[h.unit].born and (h.unit in kept or BIRTH.get(h.unit) in forced))
        eff, kept_h = set(), {}
        if strict:
            for h in hunks.values():  # ids are chronological
                if h.id not in off_hunks and here(units[h.unit]) and wants(h) and all(lines[d].addh in eff for d in h.dels):
                    eff.add(h.id)
        else:
            todo = [h.id for h in hunks.values() if h.id not in off_hunks and here(units[h.unit]) and (wants(h) or h.id in forced)]
            eff.update(todo)
            for hid in forced:
                if hid in eff:
                    kept_h[hid] = hid
            while todo and rule != "slot":
                h = hunks[todo.pop()]
                for d in h.dels:
                    a = lines[d].addh
                    if a not in eff:
                        eff.add(a)
                        kept_h[a] = h.id
                        todo.append(a)
            # when part of what a switched-off request did inside a function stays because someone
            # rewrote it, everything that request did inside that function stays: a function is never
            # left as a mix of two versions that did not exist together
            grew = rule != "slot" and bool(kept_h) and os.environ.get("MEMBER_KEEP", "unit") != "0"
            while grew:
                grew = False
                for a in list(kept_h):
                    ha = hunks[a]
                    if ha.req in on:
                        continue
                    roots = member_roots(a)
                    if not roots:
                        continue
                    for h in UNIT_HUNKS.get(ha.unit, ()):
                        if h.req == ha.req and h.id not in eff and h.id not in off_hunks and member_roots(h.id) & roots:
                            eff.add(h.id)
                            kept_h[h.id] = kept_h[a]
                            grew = True
                            stack = [h.id]
                            while stack:
                                x = hunks[stack.pop()]
                                for d in x.dels:
                                    a2 = lines[d].addh
                                    if a2 not in eff:
                                        eff.add(a2)
                                        kept_h[a2] = x.id
                                        stack.append(a2)
        files, used, dormant = {}, {}, []
        for path, weave in fileweave.items():
            out, ids = [], []
            hist = indent_hist.get(path, [])
            dst_width = ([w for kk, w in hist if kk in on] or [hist[0][1] if hist else None])[-1]
            for uid, pk in weave:
                u = units[uid]
                if (u.died is not None and u.died in on) or uid in STAY_DEAD:
                    continue
                if not here(u):
                    dormant.extend(ln.id for ln in u.lines if ln.born in on and ln.born != u.born
                                   and not (ln.delh is not None and hunks[ln.delh].req in on))
                    continue
                if pk != max(k for k, _ in u.places if k in on or k == u.born or (k, uid) in KEEP_PLACES):
                    continue
                effon = on | {u.born}
                if off_hunks:
                    reqs = {}
                    for h_ in UNIT_HUNKS.get(uid, ()):
                        reqs.setdefault(h_.req, []).append(h_.id in off_hunks)
                    effon = effon - {k_ for k_, v_ in reqs.items() if all(v_)}
                rows = []  # dicts: ln, text, slot, parent, lead, header, cont, ind (slot None = passes through)
                born_at, indent_of = {}, {}
                newest = {}
                if rule == "slot":
                    for ln in u.lines:
                        if ln.addh in eff and not (ln.delh is not None and ln.delh in eff):
                            if ln.slot not in newest or ln.born > newest[ln.slot].born:
                                newest[ln.slot] = ln
                for ln in u.lines:
                    if ln.addh not in eff:
                        if ln.born in on and not (ln.delh is not None and hunks[ln.delh].req in on):
                            dormant.append(ln.id)
                        continue
                    if (ln.delh is not None and ln.delh in eff) or any(d in eff for d in ALSO_DEL.get(ln.id, ())):
                        continue
                    if rule == "slot" and newest.get(ln.slot) is not ln:
                        continue
                    kv, text = max((t for t in ln.texts if t[0] in effon or t[0] == ln.born or (t[0], uid) in KEEP_PLACES), key=lambda t: t[0])
                    hist = slot_struct.get(ln.slot)
                    if not hist or not text.strip():
                        rows.append({"ln": ln, "text": text, "slot": None})
                        continue
                    st = None
                    for v in reversed(hist):
                        if v[1] is not None and v[1] not in born_at:
                            continue
                        if v[0] in effon or (v[0], uid) in KEEP_PLACES or (v[1] is not None and born_at[v[1]] == v[0]):
                            st = v
                            break
                    if st is None:
                        st = next((v for v in reversed(hist) if v[1] is None or v[1] in born_at), None)
                    if st is None or (st[3] is not None and st[3] not in born_at):
                        if ln.born in on:
                            dormant.append(ln.id)
                        continue
                    rel = st[2]
                    src = width_at(([p_ for kk, p_ in u.places if kk <= st[0]] or [path])[-1], st[0])
                    if not st[5] and src and dst_width and src != dst_width and rel > 0 and rel % src == 0:
                        rel = rel // src * dst_width
                    ind = max(0, (indent_of[st[1]] if st[1] is not None else 0) + rel)
                    born_at[ln.slot], indent_of[ln.slot] = ln.born, ind
                    rows.append({"ln": ln, "text": text, "slot": ln.slot, "parent": st[1], "lead": st[3],
                                 "header": st[4], "cont": st[5], "ind": ind})
                while True:  # a block left with nothing but comments goes, and so does what hung off it
                    there = {r["slot"] for r in rows if r["slot"] is not None}
                    full = {r["parent"] for r in rows if r["slot"] is not None and r["parent"] is not None
                            and not r["cont"] and not r["text"].lstrip().startswith("#")}
                    led = {r["lead"] for r in rows if r["slot"] is not None and r["lead"] is not None}
                    drop = [r for r in rows if r["slot"] is not None and (
                        (r["parent"] is not None and r["parent"] not in there)
                        or (r["lead"] is not None and r["lead"] not in there)
                        or (r["header"] and r["slot"] not in full and next(
                            (has for kk, has in reversed(CHILD_AT.get(r["slot"], ())) if kk in effon or (kk, uid) in KEEP_PLACES), True))
                        or (r["header"] and re.match(r"try\s*:", r["text"].lstrip()) and r["slot"] not in led))]
                    if not drop:
                        break
                    gone_ids = {id(r) for r in drop}
                    dormant.extend(r["ln"].id for r in drop if r["ln"].born in on)
                    rows = [r for r in rows if id(r) not in gone_ids]
                for r in rows:
                    out.append(r["text"] if r["slot"] is None else " " * r["ind"] + r["text"].lstrip(" "))
                    ids.append(r["ln"].id)
                    for tgt in r["ln"].uses:
                        used.setdefault(tgt, r["ln"].id)
            if any(x.strip() for x in out):
                files[path] = "".join(out)
                LAST_MAP[path] = ids
        more = {t: by for t, by in used.items()
                if not here(units[t]) and t not in STAY_DEAD and t not in kept
                and not (units[t].died is not None and units[t].died in on)} if keep and not strict else {}
        need = set()
        if keep and not strict and USES_LINES:
            shown = {x for ids_ in LAST_MAP.values() for x in ids_}
            for x in shown:
                for t in USES_LINES.get(x, ()):
                    if t in shown:
                        continue
                    if lines[t].delh is None or hunks[lines[t].delh].req not in on:
                        need.add(lines[t].addh)   # what it uses can stay: keep that
                        if BIRTH.get(hunks[lines[t].addh].unit) is not None:
                            need.add(BIRTH[hunks[lines[t].addh].unit])
                    elif lines[x].delh is not None and os.environ.get("RESTORE_RULE", "0") == "1":
                        need.add(lines[x].delh)   # what it uses is gone for good: do not bring this line back
            need -= forced
        if not more and not need:
            return current_names(files), kept, kept_h, dormant
        kept.update(more)
        forced = forced | need


SMOKE = ("import sys, os, runpy; d = sys.argv[1]; sys.path.insert(0, d); sys.argv = ['check.py'];"
         "runpy.run_path(os.path.join(d, 'check.py'), run_name='__main__')")


def materialize(files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        fp = dest / path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text)
    data = dest / "data"
    if not data.exists():
        data.symlink_to((clone / "data").resolve(), target_is_directory=True)


def smoke(files, dest):
    materialize(files, dest)
    if not (dest / "check.py").exists():
        return "no check.py"
    try:
        p = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", SMOKE, str(dest)], capture_output=True, text=True,
                           cwd=str(dest.parent), timeout=120)
    except subprocess.TimeoutExpired:
        return "timeout"
    if p.returncode == 0:
        return ""
    return ((p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or ["failed"])[-1])[:140]


def describe(kept, kept_h, dormant):
    k = [f"used: {units[u].places[0][1]}::{units[u].keys[-1][1]} ({NAMES[units[u].born]}) <- {NAMES[lines[by].born]}"
         for u, by in kept.items()]
    k += [f"rewritten: a block of {NAMES[hunks[a].req]} in {units[hunks[a].unit].places[0][1]}::{units[hunks[a].unit].keys[-1][1]} <- {NAMES[hunks[b].req]}"
          for a, b in kept_h.items()]
    d = {}
    for lid in dormant:
        d[NAMES[lines[lid].born]] = d.get(NAMES[lines[lid].born], 0) + 1
    return k, d


def failing_frames(files, dest):
    """Where the check fails: [(path, line number)] from the outermost frame in to the innermost."""
    materialize(files, dest)
    p = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", SMOKE, str(dest)], capture_output=True, text=True,
                       cwd=str(dest.parent), timeout=120)
    subprocess.run(["rm", "-rf", str(dest)])
    if p.returncode == 0:
        return None
    frames = []
    for m in re.finditer(r'File "([^"]+)", line (\d+)', p.stderr):
        f = m.group(1)
        if str(dest) in f:
            frames.append((f.split(str(dest) + "/", 1)[1], int(m.group(2))))
    return frames


def checked_closure(seed, dest, limit=8):
    """closure(), extended while the check still fails: the innermost failing statement written by a
    request later than the seed joins the set. Returns (set, ok, blocks added because of the check)."""
    first = min(hunks[h].req for h in seed)
    F, added = closure(seed), []
    for _ in range(limit):
        files = program(ALL, strict=True, off_hunks=F)[0]
        line_ids = {p: list(v) for p, v in LAST_MAP.items()}
        frames = failing_frames(files, dest)
        if frames is None:
            return F, True, added
        pick = None
        for path, n in reversed(frames):
            ids = line_ids.get(path, [])
            # the map lists only rows that carry a line; count rows of the rendered file
            if 0 < n <= len(ids):
                ln = lines[ids[n - 1]]
                if ln.addh not in F and ln.born > first:
                    pick = ln.addh
                    break
        if pick is None:
            return F, False, added
        added.append(pick)
        F = closure(F | {pick})
    return F, False, added


def undefined_reads(files):
    """[(path, line number)] where a name is read that no enclosing scope, the module, or Python binds."""
    out = []
    always = set(dir(builtins)) | {"__file__", "__name__", "__path__", "__doc__"}

    def binds(node):
        names = set()
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
                continue  # its body is another scope
            if isinstance(n, ast.Lambda):
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                names.add(n.name)
            stack.extend(ast.iter_child_nodes(n))
        return names

    def visit(node, scope, path):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            scope = scope | {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)} | binds(node)
        elif isinstance(node, ast.ClassDef):
            scope = scope | binds(node)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            scope = scope | {n.id for g in node.generators for n in ast.walk(g.target) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in scope:
            out.append((path, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, scope, path)

    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        visit(tree, always | binds(tree), path)
    return out


def arity_mismatches(files):
    """[((path, line of the call), (path, line of the def))] where a call cannot bind to a definition
    it reaches: a required parameter nobody passes, too many positionals, or an unknown keyword."""
    trees, defs = {}, {}
    for path, text in files.items():
        if path.endswith(".py"):
            try:
                trees[path] = ast.parse(text)
            except SyntaxError:
                continue
            for node in trees[path].body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.setdefault(node.name, []).append((path, node))
    out = []
    for path, tree in trees.items():
        local = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        imported, modules = {}, {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == PKG:
                for a in n.names:
                    target = SRCROOT + (n.module + "." + a.name).replace(".", "/") + ".py"
                    if target in trees:
                        modules[a.asname or a.name] = target
                    else:
                        imported[a.asname or a.name] = a.name
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or any(isinstance(a, ast.Starred) for a in call.args) \
                    or any(k.arg is None for k in call.keywords):
                continue
            f = call.func
            if isinstance(f, ast.Name):
                name = f.id if f.id in local else imported.get(f.id)
                cands = [c for c in defs.get(name, []) if f.id not in local or c[0] == path] if name else []
            elif isinstance(f, ast.Attribute):
                if isinstance(f.value, ast.Name) and f.value.id in modules:
                    cands = [c for c in defs.get(f.attr, []) if c[0] == modules[f.value.id]]
                elif isinstance(f.value, ast.Name) and f.value.id in ("self", "cls"):
                    cands = []
                else:
                    cands = defs.get(f.attr, [])
            else:
                cands = []
            kws = {k.arg for k in call.keywords}
            for dpath, d in cands:
                a = d.args
                pos = [x.arg for x in a.posonlyargs + a.args]
                required = pos[: len(pos) - len(a.defaults)]
                kwonly_req = [x.arg for x, dv in zip(a.kwonlyargs, a.kw_defaults) if dv is None]
                bad = any(nm not in kws for nm in required[len(call.args):]) or any(nm not in kws for nm in kwonly_req)
                bad = bad or (len(call.args) > len(pos) and a.vararg is None)
                bad = bad or (a.kwarg is None and any(k not in pos and k not in {x.arg for x in a.kwonlyargs} for k in kws))
                if bad:
                    out.append(((path, call.lineno), (dpath, d.lineno)))
    return out


BASE_UNDEF = None
BASE_ARITY = None


def closure(seed):
    """The smallest set of blocks containing `seed` that nothing outside it needs: a block outside
    needs one inside if it sits in it, rewrote its lines, or uses a name it introduced."""
    global BASE_UNDEF, BASE_ARITY
    if BASE_UNDEF is None:
        base_files = program(ALL, keep=False)[0]
        base_map = {p: list(v) for p, v in LAST_MAP.items()}
        BASE_ARITY = {(base_map[c[0]][c[1] - 1], base_map[d[0]][d[1] - 1]) for c, d in arity_mismatches(base_files)}
        BASE_UNDEF = {base_map[p][n - 1] for p, n in undefined_reads(base_files) if 0 < n <= len(base_map.get(p, []))}
    F = set(seed)
    while True:
        files_, _, _, dormant = program(ALL, strict=True, off_hunks=frozenset(F))
        rows_ = {p: list(v) for p, v in LAST_MAP.items()}
        more = {lines[l].addh for l in dormant} - F
        for p, n in undefined_reads(files_):  # a line that reads a name nothing binds any more
            if 0 < n <= len(rows_.get(p, [])) and rows_[p][n - 1] not in BASE_UNDEF:
                more.add(lines[rows_[p][n - 1]].addh)
        for (cp, cn), (dp, dn) in arity_mismatches(files_):
            if 0 < cn <= len(rows_.get(cp, [])) and 0 < dn <= len(rows_.get(dp, [])):
                c_, d_ = rows_[cp][cn - 1], rows_[dp][dn - 1]
                if (c_, d_) not in BASE_ARITY:
                    more.add(max(lines[c_].addh, lines[d_].addh))  # the newer line is the dependent
        more -= F
        gone = {u.uid for u in units.values() if BIRTH.get(u.uid) in F}
        for ln in lines.values():
            if ln.addh in F or not (ln.delh is None or ln.delh in F):
                continue
            if ln.uses & gone:
                more.add(ln.addh)
        if not more:
            return frozenset(F)
        F |= more


def wanted(p):   # ALSO: further folders tracked together with the package (its tests, for instance)
    return (p.startswith(SRCROOT + PKG + "/") or any(p.startswith(x + "/") for x in os.environ.get("ALSO", "").split(",") if x)) and p.endswith(EXT)


def build_incremental():
    """Same weave as build(), reading only the files each commit changed."""
    import hashlib
    state, segs, prev, top_by_file, binder = {}, {}, {}, {}, {}
    expected = []
    for k, rev in enumerate(VERS):
        changed, removed = [], []
        if k == 0:
            changed = [p for p in git("ls-tree", "-r", "--name-only", rev).stdout.split("\n") if wanted(p)]
        else:
            for row in git("diff-tree", "-r", "--name-status", "--no-renames", VERS[k - 1], rev).stdout.splitlines():
                st, p = row.split("\t", 1)
                if wanted(p):
                    (removed if st == "D" else changed).append(p)
        for p in changed:
            state[p] = show(rev, p) or ""
        for p in removed:
            state.pop(p, None)
        old = {p: {key: seg for key, seg, _ in segs.get(p, [])} for p in changed}
        for p in removed:
            segs.pop(p, None)
        for p in changed:
            if p.endswith(".py"):
                hist = indent_hist.setdefault(p, [])
                width = indent_width(state[p], hist[-1][1] if hist else 4)
                if not hist or hist[-1][1] != width:
                    hist.append((k, width))
            try:
                segs[p] = split_units(p, state[p])
            except (SyntaxError, ValueError):
                segs[p] = [("file", state[p], 1)]
        cur_keys = {(p, key) for p in changed for key, _, _ in segs[p]}
        gone = [fk for fk in prev if (fk[0] in old or fk[0] in removed) and fk not in cur_keys]
        segof = {(p, key): seg for p in changed for key, seg, _ in segs[p]}
        ident = {}
        alive_text = lambda uid: "".join(l.texts[-1][1] for l in units[uid].lines if l.alive)
        for fk in sorted(fk for fk in cur_keys if fk not in prev and fk[1].startswith("def ")):
            cands = [g for g in gone if g[1].startswith("def ")]
            if not cands or len(cands) > 40:
                continue
            best = max(cands, key=lambda g: similar(alive_text(prev[g]), segof[fk]))
            if similar(alive_text(prev[best]), segof[fk]) >= 0.6:
                ident[fk] = prev[best]
                gone.remove(best)
                del prev[best]
        for p in changed:
            weave = fileweave.setdefault(p, [])
            last = None
            before = [prev[(p, k2)] for k2 in old[p] if (p, k2) in prev]
            after = [prev[(p, key)] for key, _, _ in segs[p] if (p, key) in prev]
            stay = set()
            for a, b, n in difflib.SequenceMatcher(None, before, after, autojunk=False).get_matching_blocks():
                stay.update(before[a:a + n])
            for key, seg, first in segs[p]:
                fk = (p, key)
                fresh = True
                if fk in prev:
                    u = units[prev[fk]]
                    fresh = old[p].get(key) != seg
                    if u.uid not in stay:  # reordered within the file
                        u.places.append((k, p))
                        weave.insert(weave.index(last) + 1 if last in weave else 0, (u.uid, k))
                elif fk in ident:
                    u = units[ident[fk]]
                    u.keys.append((k, key))
                    if u.places[-1][1] != p:
                        u.places.append((k, p))
                        weave.insert(weave.index(last) + 1 if last in weave else 0, (u.uid, k))
                else:
                    u = Unit(len(units), key, k, p)
                    units[u.uid] = u
                    weave.insert(weave.index(last) + 1 if last in weave else 0, (u.uid, k))
                if fresh:
                    update_lines(u, seg, k)
                    update_struct(u, k, p)
                prev[fk] = u.uid
                last = next(e for e in weave if e[0] == u.uid and e[1] == u.places[-1][0])
        for fk in gone:
            u = units[prev.pop(fk)]
            u.died = k
            for ln in u.lines:
                ln.alive = False
        for p in removed:
            top_by_file.pop(p, None)
        for p in changed:
            top_by_file[p] = set()
            for key, seg, _ in segs[p]:
                if key == "file":
                    continue
                if p.endswith(BRACES):
                    if key.startswith(("def ", "assign ")):
                        nm = key.split(" ")[1]
                        top_by_file[p].add(nm)
                        binder[(p, nm)] = prev[(p, key)]
                    continue
                for node in ast.parse(seg).body:
                    names = []
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names = [node.name]
                    elif isinstance(node, ast.Assign):
                        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    elif isinstance(node, ast.Import):
                        names = [(a.asname or a.name).split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom):   # also imports from inside the package: code that uses the name uses this line
                        names = [a.asname or a.name for a in node.names]
                    for nm in names:
                        top_by_file[p].add(nm)
                        binder[(p, nm)] = prev[(p, key)]
        table = set(state)
        for p in changed:
            if segs[p] and segs[p][0][0] == "file":
                continue
            refs = references(p, state[p], table, top_by_file)
            set_local_uses(p, [(first + off, ln) for key, seg, first in segs[p]
                               for off, ln in enumerate(l for l in units[prev[(p, key)]].lines if l.alive)])
            for key, seg, first in segs[p]:
                u = units[prev[(p, key)]]
                for off, ln in enumerate(l for l in u.lines if l.alive):
                    for tf, name in refs.get(first + off, ()):
                        if name is None:
                            tgt = prev.get((tf, "docstring"), prev.get((tf, segs[tf][0][0])) if segs.get(tf) else None)
                        else:
                            tgt = binder.get((tf, name))
                        if tgt is not None and tgt != u.uid and units[tgt].died is None:
                            ln.uses.add(tgt)
        expected.append(hashlib.sha1("\0".join(f"{p}\n{t}" for p, t in sorted(state.items()) if t.strip()).encode()).hexdigest())
    return expected, state


if mode == "scale":
    import hashlib
    import statistics
    import time
    t0 = time.time()
    expected, head = build_incremental()
    print(f"{clone.name}/{SRCROOT}{PKG}: {N} first-parent commits, {len(head)} files, {sum(t.count(chr(10)) for t in head.values())} lines at HEAD; "
          f"{len(units)} units, {len(lines)} line records, {len(hunks)} hunks; built in {time.time() - t0:.0f}s")
    exact = 0
    step = max(1, N // 60)
    sample = list(range(0, N, step)) + [N - 1]
    for k in sample:
        files, _, _, _ = program(frozenset(range(k + 1)), keep=False)
        got = hashlib.sha1("\0".join(f"{p}\n{t}" for p, t in sorted(files.items())).encode()).hexdigest()
        exact += got == expected[k]
        if got != expected[k] and os.environ.get("DEBUG") and not globals().get("_shown"):
            globals()["_shown"] = 1
            import difflib as _d
            for p in sorted(set(files) | {q for q in git("ls-tree", "-r", "--name-only", VERS[k]).stdout.split("\n") if wanted(q)}):
                want = show(VERS[k], p) or ""
                have = files.get(p, "")
                if want.strip() and want != have:
                    print(f"first mismatch at version {k} ({SUBJ[k][:50]}), file {p}: want {len(want.splitlines())} lines, have {len(have.splitlines())}")
                    for dl in list(_d.unified_diff(want.splitlines(), have.splitlines(), "recorded", "rebuilt", lineterm="", n=1))[:24]:
                        print("   ", dl[:130])
                    break
    print(f"history: {exact} of {len(sample)} sampled recorded states rebuilt byte for byte")
    if os.environ.get("CLOSURE"):  # the self-contained set around each commit, taken out whole
        valid = alone = 0
        grow = []
        todo = list(range(1, N))
        if os.environ.get("MASKS"):
            import random
            todo = sorted(random.Random(5).sample(todo, min(int(os.environ["MASKS"]), len(todo))))
        kinds = {}
        for i in todo:
            mine = frozenset(h.id for h in hunks.values() if h.req == i)
            F = closure(mine)
            files = program(ALL, strict=True, off_hunks=F)[0]
            ok = True
            for p, t in files.items():
                if t != head.get(p):
                    try:
                        ast.parse(t)
                    except SyntaxError as exc:
                        ok = False
                        kinds[exc.msg.split("(")[0].strip()[:40]] = kinds.get(exc.msg.split("(")[0].strip()[:40], 0) + 1
                        break
            valid += ok
            alone += F == mine
            a_ = sum(1 for ln in lines.values() if ln.addh in mine and ln.delh is None)
            b_ = sum(1 for ln in lines.values() if ln.addh in F and ln.delh is None)
            grow.append(b_ - a_)
        n = len(todo)
        print(f"self-contained set around each commit, taken out whole ({n} trials): every file parses in {valid} ({100 * valid // n}%); "
              f"{alone} commits ({100 * alone // n}%) are self-contained as written; median lines of other commits taken along: {statistics.median(grow)}")
        print("  why it does not parse:", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))
        sys.exit(0)
    valid = clean = 0
    tie_counts, dorm, broke = [], [], []
    for i in range(1, N):
        files, kept, kept_h, dormant = program(ALL - {i})
        shown_ = {x for ids_ in LAST_MAP.values() for x in ids_}
        own_ = [l.id for l in lines.values() if l.born == i and l.delh is None]
        if own_:
            globals().setdefault("_share", []).append(sum(1 for x in own_ if x not in shown_) / len(own_))
        ok = True
        for p, t in files.items():
            if t != head.get(p):
                good, msg = parses(p, t, head.get(p))
                if not good:
                    ok = False
                    kinds = globals().setdefault("_kinds", {})
                    kinds[msg] = kinds.get(msg, 0) + 1
                    if os.environ.get("DEBUG") and p.endswith(BRACES) and len(globals().setdefault("_ex", [])) < 4:
                        def first_err(node):
                            if node.type == "ERROR" or node.is_missing:
                                return node
                            for c in node.children:
                                if c.has_error or c.is_missing:
                                    r = first_err(c)
                                    if r is not None:
                                        return r
                            return None
                        e = first_err(ts_tree(t).root_node)
                        row = e.start_point[0] if e is not None else 0
                        ids = LAST_MAP.get(p, [])
                        tl = t.splitlines()
                        ctx = "\n".join(f"      {n + 1:5d} [{SUBJ[lines[ids[n]].born][:22]:22s}] {tl[n][:92]}"
                                        for n in range(max(0, row - 5), min(len(tl), row + 4)) if n < len(ids))
                        globals()["_ex"].append(f"  taking out [{SUBJ[i][:50]}] breaks {p} near line {row + 1}:\n{ctx}")
                    break
        nt = len(kept) + sum(1 for a in kept_h if hunks[a].req == i)
        valid += ok
        clean += ok and nt == 0
        tie_counts.append(nt)
        dorm.append(len(dormant))
        if not ok:
            broke.append(SUBJ[i][:60])
    n = N - 1
    print(f"one commit off at a time ({n} trials): every file still parses in {valid} ({100 * valid // n}%); "
          f"parses with nothing kept in {clean} ({100 * clean // n}%)")
    b = lambda lo, hi: sum(lo <= t <= hi for t in tie_counts)
    print(f"  things kept because something still on depends on them: 0: {b(0, 0)}, 1-3: {b(1, 3)}, 4-10: {b(4, 10)}, more: {b(11, 10**9)}; "
          f"median dormant lines {statistics.median(dorm)}")
    sh = globals().get("_share", [])
    if sh:
        print(f"  how much of a commit's own lines really go: mean {100 * sum(sh) / len(sh):.0f}%, median {100 * statistics.median(sh):.0f}%, all of it in {sum(1 for x in sh if x == 1.0)} of {len(sh)}")
    print("  why it does not parse:", dict(sorted(globals().get("_kinds", {}).items(), key=lambda kv: -kv[1])))
    for ex in globals().get("_ex", []):
        print(ex)
    sys.exit(0)

build()

if mode == "validate":
    import shutil
    import tempfile
    exact = 0
    for k in range(N):
        files, _, _, _ = program(frozenset(range(k + 1)), keep=False)
        want = {p: t for p, t in ((p, show(VERS[k], p)) for p in tracked(VERS[k])) if t.strip()}
        exact += files == want
    print(f"history: {exact} of {N} recorded states rebuilt byte for byte from the switch settings")
    tmp = Path(tempfile.mkdtemp(prefix="weave-"))
    try:
        trials = [(NAMES[i], ALL - {i}) for i in range(1, N)]
        ev = frozenset(i for i in range(N) if "event" in NAMES[i])
        trials.append(("EVENT GROUP", ALL - ev))
        for strict in (False, True):
            print("strict (take out what others built on, too):" if strict else "default (never break what is on):")
            for label, on in trials:
                files, kept, kept_h, dormant = program(on, strict=strict)
                err = smoke(files, tmp / (label.replace(" ", "_") + str(strict)))
                k, d = describe(kept, kept_h, dormant)
                print(f"  off {label:17s} ties {len(k)}  dormant lines {sum(d.values()):3d}  "
                      f"app {'renders' if not err else 'BREAKS: ' + err}")
                if not strict:
                    for line in k:
                        print(f"        {line}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

elif mode == "all":
    work = Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    masks = range(1 << (N - 1))
    if os.environ.get("MASKS"):
        import random
        masks = random.Random(11).sample(list(masks), int(os.environ["MASKS"]))

    def one(mask):
        on = frozenset([0] + [i for i in range(1, N) if mask >> (i - 1) & 1])
        files, kept, kept_h, dormant = program(on)
        err = smoke(files, work / f"s{mask}")
        subprocess.run(["rm", "-rf", str(work / f"s{mask}")])
        return mask, err, len(kept) + len(kept_h), len(dormant)

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one, masks))
    ok = sum(1 for _, e, _, _ in res if not e)
    clean = sum(1 for _, e, k, d in res if not e and not k and not d)
    print(f"{PKG}: {ok} of {len(res)} on/off combinations render every page; "
          f"{clean} of them with nothing kept and nothing dormant")
    errs = {}
    for _, e, _, _ in res:
        if e:
            errs[e] = errs.get(e, 0) + 1
    for e, c in sorted(errs.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {c:5d}  {e}")
    json.dump({"pkg": PKG, "results": [[m, e] for m, e, _, _ in res]}, open(work / f"all-{PKG}.json", "w"))

elif mode == "export":
    out = {
        "pkg": PKG,
        "requests": [{"name": NAMES[i], "subject": SUBJ[i]} for i in range(N)],
        "files": {p: w for p, w in fileweave.items()},
        "units": {u.uid: {"key": u.keys[-1][1], "born": u.born, "died": u.died, "places": u.places,
                          "lines": [[ln.id, ln.born, ln.addh, ln.delh, ln.texts, sorted(ln.uses), [sorted(b) for b in ln.luses.values() if b]] for ln in u.lines]}
                  for u in units.values()},
        "hunks": {h.id: [h.req, h.unit, h.dels] for h in hunks.values()},
    }
    if os.environ.get("WHOLE"):
        _w = json.load(open(os.environ["WHOLE"]))
        out["whole"], out["unitWhole"] = _w["masks"], _w["units"]
    if len(sys.argv) > 4:
        allres = json.load(open(sys.argv[4]))["results"]
        msgs = sorted({e for _, e in allres if e})
        out["smokeMessages"] = msgs
        out["smoke"] = [0 if not e else msgs.index(e) + 1 for _, e in sorted(allres)]
    json.dump(out, open(sys.argv[3], "w"))
    print("exported", sys.argv[3], os.path.getsize(sys.argv[3]), "bytes")

if mode == "counts":  # a sample of settings, for checking another implementation against this one
    import random
    rnd = random.Random(7)
    rows = []
    for mask in [rnd.randrange(1 << (N - 1)) for _ in range(300)]:
        on = frozenset([0] + [i for i in range(1, N) if mask >> (i - 1) & 1])
        row = [mask]
        for strict in (False, True):
            files, kept, kept_h, dormant = program(on, strict=strict)
            row += [sum(len(t.splitlines()) for t in files.values()), len(kept)]
        rows.append(row)
    json.dump(rows, open(sys.argv[3], "w"))

if mode == "echo":  # what a person would read right after each request was saved
    def label(u):
        kind, _, name = u.keys[-1][1].partition(" ")
        return {"def": name + "()", "assign": name}.get(kind, u.keys[-1][1])

    for r in range(1, N):
        new = [u for u in units.values() if u.born == r]
        guest, uses = {}, {}
        for u in units.values():
            for ln in u.lines:
                if ln.born != r:
                    continue
                if u.born != r:
                    guest.setdefault(u.born, {}).setdefault(u.uid, 0)
                    guest[u.born][u.uid] += 1
                for t in ln.uses:
                    if units[t].born not in (r, 0):
                        uses.setdefault(units[t].born, set()).add(t)
        print(f"\n{NAMES[r]}: \"{SUBJ[r]}\"")
        if new:
            print("  new: " + ", ".join(sorted({f"{label(u)}" for u in new if u.keys[-1][1].startswith(('def', 'assign'))})) or "  new: (no definitions)")
        for owner, us in sorted(guest.items()):
            print(f"  edits inside {NAMES[owner]}: " + ", ".join(f"{label(units[x])} ({n} line{'s' if n > 1 else ''})" for x, n in us.items()))
        for owner, ts in sorted(uses.items()):
            print(f"  uses {NAMES[owner]}: " + ", ".join(sorted(label(units[t]) for t in ts)))


if mode == "repair":  # when the check fails for a setting, what is the least that has to stay?
    import random
    from itertools import combinations
    work = Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    failing = [m for m, e in json.load(open(sys.argv[4]))["results"] if e]
    sample = random.Random(3).sample(failing, min(int(os.environ.get("SAMPLE", "120")), len(failing)))

    def size(h):
        return sum(1 for ln in units[h.unit].lines if ln.addh == h.id)

    def run(on, forced, tag):
        files = program(on, forced=frozenset(forced))[0]
        err = smoke(files, work / tag)
        subprocess.run(["rm", "-rf", str(work / tag)])
        return not err

    def attempt(mask):
        on = frozenset([0] + [i for i in range(1, N) if mask >> (i - 1) & 1])
        _, kept, kept_h, _ = program(on)
        cands = sorted((h for h in hunks.values() if h.req not in on and h.id not in kept_h
                        and (units[h.unit].born in on or h.unit in kept)
                        and not (units[h.unit].died is not None and units[h.unit].died in on)), key=size)
        for h in cands:
            if run(on, [h.id], f"r{mask}_{h.id}"):
                return mask, [h.id]
        if len(cands) <= 14:
            for a, b in combinations(cands, 2):
                if run(on, [a.id, b.id], f"r{mask}_{a.id}_{b.id}"):
                    return mask, [a.id, b.id]
        return mask, None

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(attempt, sample))
    one = sum(1 for _, r in res if r and len(r) == 1)
    two = sum(1 for _, r in res if r and len(r) == 2)
    print(f"{PKG}: {len(failing)} settings fail the check; sampled {len(sample)}")
    print(f"  repaired by keeping one more block of a switched-off request: {one}; two blocks: {two}; not repaired: {len(sample) - one - two}")
    what = {}
    for _, r in res:
        for hid in r or []:
            h = hunks[hid]
            u = units[h.unit]
            key = f"{NAMES[h.req]}'s {size(h)}-line change to {u.keys[-1][1]} in {u.places[0][1]}"
            what[key] = what.get(key, 0) + 1
    for k_, n_ in sorted(what.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {n_:4d}  kept: {k_}")


if mode == "closure":
    import shutil
    import tempfile
    from itertools import combinations
    tmp = Path(tempfile.mkdtemp(prefix="closure-"))
    size = lambda F: sum(1 for ln in lines.values() if ln.addh in F and ln.delh is None)
    own = {r: frozenset(h.id for h in hunks.values() if h.req == r) for r in range(1, N)}
    cl = {r: closure(own[r]) for r in range(1, N)}
    ok2, notes, whole = 0, [], {}
    label_ = lambda a: f"{NAMES[hunks[a].req]}'s block in {units[hunks[a].unit].keys[-1][1].replace('def ', '') + ('()' if units[hunks[a].unit].keys[-1][1].startswith('def ') else '')}"
    groups = [[r] for r in range(1, N)] + [[r for r in range(1, N) if "event" in NAMES[r]]]
    for g_ in groups:
        seed_ = frozenset().union(*(own[r] for r in g_))
        F1 = closure(seed_)
        err1 = smoke(program(ALL, strict=True, off_hunks=F1)[0], tmp / "w")
        subprocess.run(["rm", "-rf", str(tmp / "w")])
        F2, good, added = checked_closure(seed_, tmp / "wk") if err1 else (F1, True, [])
        whole[str(sum(1 << (r - 1) for r in g_))] = {"set": sorted(F1), "ok": not err1, "err": err1, "adds": [label_(a) for a in added], "okc": good}
    for r in range(1, N):
        F2, good, added = checked_closure(own[r], tmp / f"k{r}")
        ok2 += good
        if added:
            notes.append(f"    {NAMES[r]}: the check adds " + "; ".join(
                f"{NAMES[hunks[a].req]}'s block in {units[hunks[a].unit].keys[-1][1]}" for a in added)
                + (" -> app renders" if good else " -> still breaks"))
    print(f"{PKG}: with the check as a source of needs, the set around each request leaves a running app for {ok2} of {N - 1}")
    for line_ in notes:
        print(line_)
    unit_whole = {}
    for u in units.values():
        if u.died is None and u.keys[-1][1].startswith(("def ", "assign ")):
            seed_ = frozenset(h.id for h in hunks.values() if h.unit == u.uid)
            F1 = closure(seed_)
            err1 = smoke(program(ALL, strict=True, off_hunks=F1)[0], tmp / "u")
            subprocess.run(["rm", "-rf", str(tmp / "u")])
            F2, good, added = checked_closure(seed_, tmp / "uk") if err1 else (F1, True, [])
            unit_whole[str(u.uid)] = {"set": sorted(F1), "ok": not err1, "err": err1, "adds": [label_(a) for a in added], "okc": good}
    print(f"{PKG}: pointing at each of {len(unit_whole)} definitions, the app runs after taking its feature out for "
          f"{sum(v['ok'] for v in unit_whole.values())}; with the check adding what fails, for {sum(v['okc'] for v in unit_whole.values())}")
    json.dump({"masks": whole, "units": unit_whole}, open(os.environ.get("CLOSURE_OUT", str(tmp / "cl.json")), "w"))
    ok = clean = 0
    print(f"{PKG}: the smallest self-contained set of blocks around each request")
    for r in range(1, N):
        F = cl[r]
        files, _, _, dormant = program(ALL, strict=True, off_hunks=F)
        err = smoke(files, tmp / f"c{r}")
        others = {}
        for hid in F - own[r]:
            n = sum(1 for ln in lines.values() if ln.addh == hid and ln.delh is None)
            others[NAMES[hunks[hid].req]] = others.get(NAMES[hunks[hid].req], 0) + n
        ok += not err
        clean += not others
        extra = ", ".join(f"{k} {v}" for k, v in sorted(others.items(), key=lambda kv: -kv[1])) or "nothing else"
        print(f"  {NAMES[r]:17s} own lines {size(own[r]):3d}; also takes: {extra}; "
              f"{'app renders' if not err else 'BREAKS: ' + err[:70]}; left dormant {len(dormant)}")
    print(f"  taking the whole set out leaves a running app for {ok} of {N - 1} requests; {clean} requests are self-contained as written")
    same = sum(1 for a, b in combinations(range(1, N), 2) if closure(own[a] | own[b]) == cl[a] | cl[b])
    inter = sum(1 for a, b in combinations(range(1, N), 2) if closure(cl[a] & cl[b]) == cl[a] & cl[b])
    pairs = (N - 1) * (N - 2) // 2
    print(f"  composing: the set around A and B together equals the union of the two sets in {same} of {pairs} pairs")
    print(f"  intersecting: the overlap of two sets is itself self-contained in {inter} of {pairs} pairs")
    ev = [r for r in range(1, N) if "event" in NAMES[r]]
    F = closure(frozenset().union(*(own[r] for r in ev)))
    print(f"  the three event requests together: own lines {size(frozenset().union(*(own[r] for r in ev)))}, the set adds {size(F) - size(frozenset().union(*(own[r] for r in ev)))} lines of other requests")
    shutil.rmtree(tmp, ignore_errors=True)


if mode in ("truth", "uncaptured"):
    import shutil
    import tempfile
    program(ALL, keep=False)
    pos = {lid: (p, i + 1) for p, ids in LAST_MAP.items() for i, lid in enumerate(ids)}
    path_of = lambda u: u.places[-1][1]
    if mode == "truth":  # from the fully captured history: which lines the event requests wrote, and which definitions came after the first state
        json.dump({"event": sorted(pos[l.id] for l in lines.values() if "event" in NAMES[l.born] and l.id in pos),
                   "defs": sorted([path_of(u), u.keys[-1][1]] for u in units.values()
                                  if u.born > 0 and u.died is None and u.keys[-1][1].startswith(("def ", "assign ")))},
                  open(sys.argv[3], "w"))
        sys.exit(0)
    truth = json.load(open(sys.argv[3]))
    T = {tuple(x) for x in truth["event"]}
    tmp = Path(tempfile.mkdtemp(prefix="unc-"))
    seed = frozenset(h.id for h in hunks.values() if path_of(units[h.unit]) == f"{PKG}/events.py")
    F, good, added = checked_closure(seed, tmp / "ev")
    got = {pos[l.id] for l in lines.values() if l.addh in F and l.id in pos}
    hit = len(got & T)
    runs = n = 0
    for p, key in truth["defs"]:
        u = next((u for u in units.values() if u.died is None and path_of(u) == p and u.keys[-1][1] == key), None)
        if u is None:
            continue
        n += 1
        Fd, ok, _ = checked_closure(frozenset(h.id for h in hunks.values() if h.unit == u.uid), tmp / "d")
        runs += ok
    total_lines = len(pos)
    print(f"{PKG} | states known: {N:2d} | point at events.py: takes {len(got):3d} lines, {hit} of {len(T)} event lines "
          f"({100 * hit // len(T)}%), {len(got - T)} lines that are not event work; app {'runs' if good else 'BREAKS'}"
          f" | point at each of {n} later definitions: app runs for {runs}")
    shutil.rmtree(tmp, ignore_errors=True)
