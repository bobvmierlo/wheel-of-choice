"""Collect every translatable text in the app — the English keys a
language file should cover. Used by `python3 -m wheelapp.i18n_keys check xx`
and by the test that keeps locales/nl.json complete.

Sources:
  - JavaScript: t('…'), tn(n, '…', '…') and N_('…') with literal text;
  - Python: i18n.msg('…'), i18n.tr('…'), abort(description='…'), the
    texts given to push notifications, sign-on errors and user-facing
    ValueErrors, and the weekday/month names;
  - index.html: the text, placeholders, titles and labels i18n.js
    translates (same rules, see translateDom there).
"""
import ast
import re
from html import escape
from html.parser import HTMLParser

from .core import ROOT

JS_FILES = ("app.js", "admin.js", "account.js", "constants.js", "utils.js", "ui.js")
STR = r"""(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)"|`((?:\\.|[^`\\$])*)`)"""
JS_ONE = re.compile(r"(?<![\w.])(?:t|N_)\(\s*" + STR)
JS_TN = re.compile(r"(?<![\w.])tn\([^,]+,\s*" + STR + r"\s*,\s*" + STR)
RAISE_FILES = {"oidc.py", "calendars.py", "validation.py"}


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def js_keys():
    keys = set()
    for name in JS_FILES:
        src = (ROOT / name).read_text(encoding="utf-8")
        for m in JS_ONE.finditer(src):
            keys.add(_unescape(next(g for g in m.groups() if g is not None)))
        for m in JS_TN.finditer(src):
            groups = [g for g in m.groups() if g is not None]
            keys.update(_unescape(g) for g in groups)
    return keys


def _consts(node):
    """String constants a node can evaluate to (through if/else)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _consts(node.body) + _consts(node.orelse)
    if isinstance(node, ast.BoolOp):
        return [v for x in node.values for v in _consts(x)]
    return []


def py_keys():
    keys = set()
    for path in sorted((ROOT / "wheelapp").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in ("WEEKDAYS", "MONTHS") for t in node.targets):
                keys.update(e.value for e in node.value.elts)
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if fname in ("msg", "tr") and node.args:
                keys.update(_consts(node.args[0]))
            elif fname == "abort":
                for kw in node.keywords:
                    if kw.arg == "description":
                        keys.update(_consts(kw.value))
            elif fname == "_sso_bounce":
                for kw in node.keywords:
                    if kw.arg == "sso_error":
                        keys.update(_consts(kw.value))
            elif fname == "push_to_users":
                for arg in node.args[1:3]:
                    keys.update(_consts(arg))
            elif fname == "ValueError" and path.name in RAISE_FILES and node.args:
                keys.update(_consts(node.args[0]))
    return keys


# ── index.html, mirroring i18n.js translateDom ──────────────────────
INLINE = {"strong", "em", "b", "i", "code", "a", "br", "small", "kbd"}
SKIP = {"script", "style", "canvas", "svg", "template"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}
ATTRS = ("placeholder", "title", "aria-label", "alt")


def normalize(s):
    return re.sub(r"\s+", " ", s).strip()


class _El:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, attrs, []


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _El("#root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        el = _El(tag, attrs)
        self.stack[-1].children.append(el)
        if tag not in VOID:
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_El(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _inner_html(el):
    out = []
    for c in el.children:
        if isinstance(c, str):
            out.append(escape(c, quote=False).replace("\xa0", "&nbsp;"))
        else:
            attrs = "".join(
                f' {k}="{escape(v or "", quote=False).replace(chr(34), "&quot;")}"'
                for k, v in c.attrs)
            out.append(f"<{c.tag}{attrs}>")
            if c.tag not in VOID:
                out.append(_inner_html(c) + f"</{c.tag}>")
    return "".join(out)


def _descendants(el):
    for c in el.children:
        if not isinstance(c, str):
            yield c
            yield from _descendants(c)


def _walk(el, keys, no_translate=False):
    if el.tag in SKIP:
        return
    attrs = dict(el.attrs)
    if attrs.get("translate") == "no" or no_translate:
        return
    for a in ATTRS:
        if attrs.get(a) and normalize(attrs[a]):
            keys.add(normalize(attrs[a]))
    kids = [c for c in el.children if not isinstance(c, str)]
    is_unit = (kids and all(d.tag in INLINE for d in _descendants(el))
               and any(isinstance(c, str) and c.strip() for c in el.children))
    if is_unit and el.tag not in INLINE:
        keys.add(normalize(_inner_html(el)))
        return
    for c in el.children:
        if isinstance(c, str):
            if normalize(c):
                keys.add(normalize(c))
        else:
            _walk(c, keys)


def html_keys():
    tree = _Tree()
    tree.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    keys = set()
    _walk(tree.root, keys)
    # punctuation/emoji-only bits ("—", "✕", ":00") need no translation
    return {k for k in keys if re.search(r"[A-Za-z]{2}", k)}


def all_keys():
    return js_keys() | py_keys() | html_keys()


if __name__ == "__main__":  # python3 -m wheelapp.i18n_keys check nl
    import sys
    from . import i18n
    if len(sys.argv) != 3 or sys.argv[1] != "check":
        sys.exit("usage: python3 -m wheelapp.i18n_keys check <language-code>")
    missing, broken = i18n.check(sys.argv[2], all_keys())
    for k in missing:
        print("missing:", k)
    for k in broken:
        print("placeholders differ:", k)
    print(f"{len(missing)} missing, {len(broken)} with mismatched placeholders")
    sys.exit(1 if missing or broken else 0)
