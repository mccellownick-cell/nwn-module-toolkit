# -*- coding: utf-8 -*-
"""nwn_localize.py - how much of the module is translated, per kind of text.

Counts come from the analysis index (no module files are read again):
  journal   - quest categories and their entries (quests / quest_entries)
  dialogues - the .dlg node text stored in the index's fields table, plus the per-file node/word counts
  scripts   - the string literals in .nss sources (script_literals)

For every group it reports characters and the share of Cyrillic characters: a module translated to Russian has a high
share, an untouched one near zero. That makes "what is left to translate" visible before opening a single file.
"""
from __future__ import annotations

import os
import re
import sqlite3

CYR = re.compile(r"[\u0400-\u04FF]")
TAG = re.compile(r"<[^>]{1,40}>")          # <C=gold>…</C> markup does not count as text

# Functions whose string argument is shown to the player - the only literals that count as
# translation work (everything else is tags, variable names and 2DA arguments).
TEXTFUNCS = {"SpeakString", "ActionSpeakString", "SendMessageToPC", "SendMessageToAllDMs",
             "FloatingTextStringOnCreature", "SetCustomToken", "PrintString",
             "ActionSpeakStringByStrRef"}


def _text_stats(texts):
    """chars, cyrillic chars and share for an iterable of strings (markup stripped)."""
    total = cyr = 0
    for t in texts:
        s = TAG.sub("", str(t or ""))
        total += len(s)
        cyr += len(CYR.findall(s))
    return dict(chars=total, cyrillic=cyr, share=round(100.0 * cyr / total, 1) if total else 0.0)


def stats(analysis_dir):
    """Localisation numbers for one analysis folder (needs its index.sqlite)."""
    db_path = os.path.join(analysis_dir, "index.sqlite")
    if not os.path.isfile(db_path):
        raise ValueError("у анализа нет index.sqlite - сначала проанализируйте модуль")
    db = sqlite3.connect(db_path)
    try:
        # --- journal
        cats = db.execute("SELECT COUNT(*) FROM quests").fetchone()[0]
        jr = db.execute("SELECT text FROM quest_entries").fetchall()
        journal = _text_stats(r[0] for r in jr)
        journal.update(categories=cats, entries=len(jr))

        # --- dialogues: node text lives in fields (CEXOLOCSTRING values), keys look like EntryList[3]/Text
        rows = db.execute(
            "SELECT f.resref, fl.value FROM fields fl JOIN files f ON f.id = fl.file_id "
            "WHERE f.ext = 'dlg' AND (fl.label = 'Text' OR fl.label = 'Text2')").fetchall()
        per_dlg = {}
        for name, value in rows:
            per_dlg.setdefault(name, []).append(value)
        conv = {r[0]: dict(entries=r[1], replies=r[2], words=r[3])
                for r in db.execute("SELECT name, entries, replies, words FROM conversations")}
        dlgs = []
        all_dlg_text = []
        for name, texts in per_dlg.items():
            st = _text_stats(texts)
            all_dlg_text.extend(texts)
            c = conv.get(name, {})
            dlgs.append(dict(name=name, chars=st["chars"], share=st["share"],
                             entries=c.get("entries", 0), replies=c.get("replies", 0), words=c.get("words", 0)))
        dialogues = _text_stats(all_dlg_text)
        dialogues.update(files=len(per_dlg),
                         nodes=sum(d["entries"] + d["replies"] for d in dlgs),
                         words=sum(d["words"] for d in dlgs))
        dlgs.sort(key=lambda d: (-d["chars"]))

        # --- scripts: only literals passed to functions that show text to the player; tags and variable names are
        # not translation work, and counting them made the share look meaningless
        marks = ",".join("?" * len(TEXTFUNCS))
        lit = db.execute(f"SELECT script, literal FROM script_literals WHERE literal IS NOT NULL "
                         f"AND LENGTH(literal) > 2 AND func IN ({marks})", tuple(sorted(TEXTFUNCS))).fetchall()
        all_lit = db.execute("SELECT COUNT(*) FROM script_literals WHERE literal IS NOT NULL").fetchone()[0]
        per_scr = {}
        for script, value in lit:
            per_scr.setdefault(script, []).append(value)
        scripts = _text_stats(v for vs in per_scr.values() for v in vs)
        scripts.update(files=len(per_scr), literals=len(lit), all_literals=all_lit)
        scr_rows = sorted((dict(name=k, chars=_text_stats(v)["chars"], share=_text_stats(v)["share"])
                           for k, v in per_scr.items()), key=lambda d: -d["chars"])

        return dict(
            journal=journal, dialogues=dialogues, scripts=scripts,
            dialogues_top=dlgs[:80], scripts_top=scr_rows[:80],
            # the ten dialogues with the least Russian text that still have a lot of it to translate
            to_translate=sorted((d for d in dlgs if d["chars"] >= 400), key=lambda d: d["share"])[:20],
        )
    finally:
        db.close()


def summary_line(st):
    """One line for a page heading: the three shares at a glance."""
    return (f"журнал {st['journal']['share']}% · диалоги {st['dialogues']['share']}% · "
            f"скрипты {st['scripts']['share']}%")
