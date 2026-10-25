# -*- coding: utf-8 -*-
"""nwn_glossary.py - one glossary for the whole translation, and a checker that enforces it.

The glossary lives in <toolkit>/nwn_workspace/glossary.json:

    {"terms": {"Oakhurst": "Оакхёрст", "Meepo": "Мипо", ...},
     "rules": {"keep_tags": true, "keep_crlf": true, "no_latin": true, "no_gender_forms": true},
     "en_source": ""}

`check(en, ru)` is what the batch translator uses: for one English string and its Russian version it reports every
mechanical problem the earlier pipeline checked by hand -
  * an empty translation,
  * markup that does not match (<C=gold>…</C>, <i>…</i>, {placeholders}),
  * a different number of CR/LF (a dialogue node's line breaks matter),
  * Latin letters left outside the tags,
  * the "(-а)" gender forms that read badly in game,
  * a glossary term that is not translated with the agreed wording.

`audit_modules(en_path, ru_path)` does the same for two copies of the same module (the English original and the
translated build): every dialogue node and journal entry is paired by resref and checked. The dashboard's
Localisation page shows the result and lets the terms be edited; the assistant gets the terms in its prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "nwn_workspace", "glossary.json")

DEFAULT_RULES = {"keep_tags": True, "keep_crlf": True, "no_latin": True, "no_gender_forms": True}

TAG = re.compile(r"<[^>]{1,60}>")
BRACE = re.compile(r"\{[^}]{1,40}\}")
CYR = re.compile(r"[\u0400-\u04FF]")
LAT = re.compile(r"[A-Za-z]")
GENDER = re.compile(r"\(\s*-\s*[ая]\s*\)|\(а\)|\(ла\)|\(лся\)")
# Latin that is allowed to stay: game terms and unit-like tokens.
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
LATIN_OK = {"d", "nwn2", "nwn", "nw", "xp", "hp", "ac", "str", "dex", "con", "int", "wis", "cha", "dm", "pc",
            "npc", "xp2", "gp", "sp", "cp", "hh", "mm", "ss", "dd", "ee", "hp", "2da", "tlk", "mod", "erf",
            "nwsync", "e", "g", "i", "s", "c", "ok", "soz", "motb", "xp1", "ai", "dc", "mp", "sp",
            "llolthanchwi", "schlegels"}   # the sequel's title and the author's site


def load(path=None):
    path = path or CONFIG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"terms": {}, "rules": dict(DEFAULT_RULES), "en_source": ""}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh) or {}
            data["terms"] = dict(loaded.get("terms") or {})
            data["rules"].update(loaded.get("rules") or {})
            data["en_source"] = loaded.get("en_source") or ""
        except (OSError, ValueError):
            pass
    return data


def save(patch, path=None):
    path = path or CONFIG
    data = load(path)
    for k in ("terms", "rules", "en_source"):
        if k in (patch or {}):
            if k == "terms":
                data["terms"] = {str(a).strip(): str(b).strip() for a, b in (patch["terms"] or {}).items()
                                 if str(a).strip()}
            elif k == "rules":
                data["rules"].update({k2: bool(v) for k2, v in (patch["rules"] or {}).items()})
            else:
                data["en_source"] = str(patch[k] or "").strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return data


def terms_text(data=None):
    """The glossary as the "English = Русский" lines the page edits."""
    data = data or load()
    return "\n".join(f"{en} = {ru}" for en, ru in sorted(data["terms"].items()))


def parse_terms(text):
    """Read those lines back into a dict (a line without '=' is ignored; '#' starts a comment)."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        en, _, ru = line.partition("=")
        if en.strip() and ru.strip():
            out[en.strip()] = ru.strip()
    return out


def prompt_block(data=None, limit=400):
    """The part of the system prompt that keeps the assistant consistent with the glossary."""
    data = data or load()
    if not data["terms"]:
        return ""
    items = list(data["terms"].items())[:limit]
    return ("Глоссарий (соблюдай эти написания дословно):\n" +
            "; ".join(f"{en} → {ru}" for en, ru in items))


def check(en, ru, data=None, where=""):
    """Every mechanical problem with one translated string. Returns a list of sentences (empty = good)."""
    data = data or load()
    rules = data["rules"]
    problems = []
    en, ru = str(en or ""), str(ru or "")
    if not ru.strip():
        return [f"{where}: пустой перевод"]
    if rules.get("keep_tags"):
        a, b = sorted(TAG.findall(en)), sorted(TAG.findall(ru))
        if a != b:
            problems.append(f"{where}: разметка не совпадает: было {a[:4]}, стало {b[:4]}")
        a2, b2 = sorted(BRACE.findall(en)), sorted(BRACE.findall(ru))
        if a2 != b2:
            problems.append(f"{where}: плейсхолдеры не совпадают: было {a2[:4]}, стало {b2[:4]}")
    if rules.get("keep_crlf") and en.count("\r\n") != ru.count("\r\n"):
        problems.append(f"{where}: число \\r\\n не совпадает ({en.count(chr(13) + chr(10))} → "
                        f"{ru.count(chr(13) + chr(10))})")
    if rules.get("no_latin"):
        stripped = URL.sub("", TAG.sub("", BRACE.sub("", ru)))
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", stripped) if w.lower() not in LATIN_OK]
        if words:
            problems.append(f"{where}: латиница в переводе: {', '.join(sorted(set(words))[:6])}")
    if rules.get("no_gender_forms") and GENDER.search(ru):
        problems.append(f"{where}: форма рода вида «(-а)»: {ru[:60]!r}")
    groups = {}
    for term, want in data["terms"].items():
        groups.setdefault(term.lower(), []).append((term, want))
    for key, variants in groups.items():
        if not re.search(rf"(?<![A-Za-z]){re.escape(key)}(?![A-Za-z])", en, re.I):
            continue
        found = False
        for _term, want in variants:
            if not want:
                continue
            first = want.split()[0] if want.split() else want
            stem = first[:max(4, len(first) - 2)].lower()     # стебель первого слова: падежи совпадают
            if stem and stem in ru.lower():
                found = True
                break
        if not found:
            expected = ", ".join(f"«{w}»" for _t, w in variants if w)
            problems.append(f"{where}: термин «{variants[0][0]}» переведён не по глоссарию (ожидалось {expected})")
    return problems


def audit_pairs(pairs, data=None):
    """Check many (en, ru, where) triples; returns (checked, problems)."""
    data = data or load()
    problems = []
    for en, ru, where in pairs:
        problems.extend(check(en, ru, data, where=where))
    return len(list(pairs)) if not isinstance(pairs, list) else len(pairs), problems


# ------------------------------------------------------------------------------------------ module audit

def _loc_text(value):
    if isinstance(value, str):
        return value
    entries = getattr(value, "entries", None)
    if isinstance(entries, dict):
        return entries.get(0, "") or ""
    return ""


def _dialogue_pairs(en_root, ru_root):
    """(en, ru, where) for every node text of one dialogue, node by node."""
    out = []

    def nodes(root):
        rows = {}
        for key in ("StartingList", "EntryList", "ReplyList"):
            for i, node in enumerate(root.get(key) or []):
                for label in ("Text", "Text2"):
                    text = _loc_text(node.get(label))
                    if text:
                        rows[f"{key}[{i}]/{label}"] = text
        return rows

    a, b = nodes(en_root), nodes(ru_root)
    for key in sorted(set(a) | set(b)):
        out.append((a.get(key, ""), b.get(key, ""), key))
    return out


def audit_modules(en_path, ru_path):
    """Compare an English original (.mod) with a translated build (folder or .mod), node by node.

    Returns dict(dialogues, journal, problems, checked) - only texts present in both are compared; a node that exists
    only in one of them is reported as such."""
    import nwnlib as n

    def open_source(path):
        if os.path.isdir(path):
            return ("folder", path)
        return ("erf", n.Erf(path))

    def entries_of(src):
        kind, ref = src
        if kind == "erf":
            return {e.filename.lower(): (e.ext.lower(), e.resref) for e in ref.entries}
        out = {}
        for base, _dirs, files in os.walk(ref):
            for f in files:
                out[f.lower()] = (os.path.splitext(f)[1].lstrip(".").lower(), os.path.splitext(f)[0])
        return out

    def read(src, filename):
        kind, ref = src
        if kind == "erf":
            e = next(e for e in ref.entries if e.filename.lower() == filename.lower())
            return ref.read(e)
        for base, _dirs, files in os.walk(ref):
            for f in files:
                if f.lower() == filename.lower():
                    return open(os.path.join(base, f), "rb").read()
        raise FileNotFoundError(filename)

    en, ru = open_source(en_path), open_source(ru_path)
    try:
        en_files, ru_files = entries_of(en), entries_of(ru)
        data = load()
        problems, checked = [], 0
        for name, (ext, resref) in sorted(en_files.items()):
            if ext not in ("dlg", "jrl") or name not in ru_files:
                continue
            try:
                a = n.read_gff(read(en, name))
                b = n.read_gff(read(ru, name))
            except (n.GffError, OSError, StopIteration):
                continue
            if ext == "dlg":
                for en_text, ru_text, where in _dialogue_pairs(a, b):
                    if not en_text:
                        continue
                    checked += 1
                    problems.extend(check(en_text, ru_text, data, where=f"{resref}.dlg {where}"))
            else:
                for en_text, ru_text, where in _journal_pairs(a, b):
                    if not en_text:
                        continue
                    checked += 1
                    problems.extend(check(en_text, ru_text, data, where=f"journal {where}"))
        return dict(checked=checked, problems=problems,
                    dialogues=sum(1 for v in en_files.values() if v[0] == "dlg" and v[1] in
                                  {r[1] for r in ru_files.values()}),
                    journal_entries=sum(1 for v in en_files.values() if v[0] == "jrl"))
    finally:
        for src in (en, ru):
            if src[0] == "erf":
                src[1].close()


def _journal_pairs(en_root, ru_root):
    def cats(root):
        out = {}
        for cat in root.get("Categories") or []:
            tag = str(cat.get("Tag") or "")
            for e in cat.get("EntryList") or []:
                out[f"{tag}:{e.get('ID')}"] = _loc_text(e.get("Text"))
        return out

    a, b = cats(en_root), cats(ru_root)
    return [(a.get(k, ""), b.get(k, ""), k) for k in sorted(set(a) | set(b))]


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] in ("-h", "--help"):
        print("python nwn_glossary.py                     show the glossary file and its size")
        print("python nwn_glossary.py --check 'en' 'ru'   check one string")
        print("python nwn_glossary.py --audit <en.mod|folder> <ru.mod|folder>")
        return 0
    if argv[0] == "--check" and len(argv) >= 3:
        for p in check(argv[1], argv[2], where="строка"):
            print("!", p)
        print("ок" if not check(argv[1], argv[2]) else "")
        return 0
    if argv[0] == "--audit" and len(argv) >= 3:
        r = audit_modules(argv[1], argv[2])
        print(f"проверено текстов: {r['checked']}; проблем: {len(r['problems'])}")
        for p in r["problems"][:60]:
            print(" ", p)
        return 0
    data = load()
    print("файл:", CONFIG)
    print("терминов:", len(data["terms"]), "| правил:", len(data["rules"]))
    for en, ru in list(data["terms"].items())[:20]:
        print(f"  {en} = {ru}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
