# -*- coding: utf-8 -*-
"""Campaign support for the NWN2 fork.

An NWN2 campaign is a folder like

    <Neverwinter Nights 2>/Campaigns/The Sunless Citadel/
        Campaign.cam      GFF "CAM ": DisplayName, Description, StartModule, ModNames[],
                          GUID, LvlCap, XPCap, Cam_PartySize, ...
        module.jrl        the campaign journal shared by every module of the campaign
        resources.dat, campaigndownloaderresources.xml, ...

so it is neither a module (no module.ifo) nor a hak. Pointing an analysis at it - or at the user/install folder that
contains Campaigns/ - now means: read the campaign, resolve the modules it names, analyse each of them with its own
talk table and haks, and write a campaign report that ties them together (including the campaign journal, which is
translation material in its own right).

Public entry points
-------------------
    find_campaigns(path)                      -> [campaign folder, ...] (a campaign folder, or one holding Campaigns/)
    read_campaign(folder)                     -> dict(display_name, description, start_module, modules, settings, journal)
    resolve_module(name, dirs)                -> path of the .mod for that name, or None
    analyse(path, nwn_root, nwn_user, ...)    -> dict with the campaign summary and each module's analysis
    main(argv)                                -> command line: show the campaign, and analyse its modules
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

import nwnlib as n

CAMPAIGN_FILE = "campaign.cam"
JOURNAL_FILE = "module.jrl"


def _find_file(folder: str, name: str) -> Optional[str]:
    """A file in `folder` whose name matches `name` without regard to case (Windows is case-insensitive, Linux
    is not, and the game ships "Campaign.cam" while the docs say "campaign.cam")."""
    try:
        for fn in os.listdir(folder):
            if fn.lower() == name.lower() and os.path.isfile(os.path.join(folder, fn)):
                return os.path.join(folder, fn)
    except OSError:
        pass
    return None


def is_campaign_folder(folder: str) -> bool:
    """True when `folder` holds a Campaign.cam of its own."""
    return os.path.isdir(folder) and _find_file(folder, CAMPAIGN_FILE) is not None


def find_campaigns(path: str) -> List[str]:
    """Every campaign folder that `path` names.

    path may be the campaign folder itself, or a folder holding the game's Campaigns/ directory (a user or install
    folder), in which case every campaign inside it is returned. [] when the path is neither."""
    if not path or not os.path.isdir(path):
        return []
    if is_campaign_folder(path):
        return [path]
    out = []
    candidates = [os.path.join(path, "Campaigns")]
    try:
        for fn in os.listdir(path):
            if fn.lower() == "campaigns" and os.path.isdir(os.path.join(path, fn)):
                candidates.append(os.path.join(path, fn))
    except OSError:
        pass
    seen = set()
    for base in candidates:
        if not os.path.isdir(base):
            continue
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for fn in names:
            full = os.path.join(base, fn)
            if full in seen:
                continue
            if is_campaign_folder(full):
                seen.add(full)
                out.append(full)
    return out


def _loc(v):
    return v.entries.get(0, "") if isinstance(v, n.LocString) else v


def read_campaign(folder: str) -> dict:
    """The campaign's own data: Campaign.cam settings, the module list, and the campaign journal.

    Returns dict(name, folder, display_name, description, start_module, modules, journal, journal_entries,
    settings, files). Never raises for a missing or damaged piece: the field is simply empty/None."""
    info = dict(name=os.path.basename(os.path.normpath(folder)), folder=folder, display_name="", description="",
                start_module="", modules=[], journal=[], journal_entries=0, settings={}, files=[])
    try:
        info["files"] = sorted(fn for fn in os.listdir(folder) if os.path.isfile(os.path.join(folder, fn)))
    except OSError:
        pass
    cam_path = _find_file(folder, CAMPAIGN_FILE)
    if cam_path:
        try:
            root = n.read_gff(open(cam_path, "rb").read())
        except (OSError, n.GffError):
            root = None
        if root is not None:
            info["display_name"] = str(_loc(root.get("DisplayName", "")) or "")
            info["description"] = str(_loc(root.get("Description", "")) or "")
            info["start_module"] = str(_loc(root.get("StartModule", "")) or "")
            for struct in (root.get("ModNames") or []):
                name = struct.get("ModuleName")
                if name:
                    info["modules"].append(str(name))
            for key in ("GUID", "LvlCap", "XPCap", "Cam_PartySize", "Cam_PartyCreate", "JournalSynch",
                        "NoCharChanging", "AllowUnrstrctLU", "UsePersonalRep", "ModNames"):
                if key in root.fields:
                    v = root.get(key)
                    info["settings"][key] = v if isinstance(v, (int, float, str)) else type(v).__name__
    j_path = _find_file(folder, JOURNAL_FILE)
    if j_path:
        try:
            jroot = n.read_gff(open(j_path, "rb").read())
        except (OSError, n.GffError):
            jroot = None
        if jroot is not None:
            for cat in (jroot.get("Categories") or []):
                entries = []
                for e in (cat.get("EntryList") or []):
                    entries.append(dict(
                        text=str(_loc(e.get("Text", "")) or ""),
                        end=str(_loc(e.get("End", "")) or ""),
                        xp=e.get("XP") or 0,
                        strref=getattr(e.get("Text"), "strref", None) if isinstance(e.get("Text"), n.LocString)
                        else None,
                    ))
                info["journal"].append(dict(name=str(_loc(cat.get("Name", "")) or ""), entries=entries))
                info["journal_entries"] += len(entries)
    return info


def _search_dirs(campaign_dir: str, extra=()) -> List[str]:
    """Folders a campaign's modules may sit in: the campaign itself, its neighbours' modules/ folders, and any extra
    folder given (the user folder, the install folder)."""
    out = []
    bases = [campaign_dir, os.path.dirname(campaign_dir), os.path.dirname(os.path.dirname(campaign_dir))]
    for b in bases:
        for sub in (b, os.path.join(b, "modules"), os.path.join(b, "Modules")):
            if os.path.isdir(sub) and sub not in out:
                out.append(sub)
    for b in extra:
        if not b:
            continue
        # the folder itself, its modules/ folder, and the folders around it (a .mod often sits beside the campaign)
        for base in (b, os.path.dirname(os.path.normpath(b)), os.path.dirname(os.path.dirname(os.path.normpath(b)))):
            if not base:
                continue
            for sub in (base, os.path.join(base, "modules"), os.path.join(base, "Modules")):
                if os.path.isdir(sub) and sub not in out:
                    out.append(sub)
    return out


def _module_display_name(path: str) -> str:
    """Mod_Name / DisplayName from a .mod's module.ifo, or "" (used to match a renamed file)."""
    try:
        erf = n.Erf(path)
    except (OSError, ValueError):
        return ""
    try:
        entry = next((e for e in erf.entries if e.ext == "ifo"), None)
        if not entry:
            return ""
        root = n.read_gff(erf.read(entry))
        for key in ("Mod_Name", "DisplayName", "Mod_Tag"):
            v = root.get(key)
            if v:
                return str(_loc(v))
        return ""
    except (OSError, KeyError, ValueError, n.GffError):
        return ""
    finally:
        erf.close()


def _is_module_folder(path: str) -> bool:
    """True for an unpacked module folder (module.ifo inside)."""
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "module.ifo"))


def _module_name(path: str) -> str:
    """The name a candidate is matched by: the file stem for a .mod, the folder name for an unpacked module."""
    return os.path.basename(os.path.normpath(path)).lower()


def _walk_mods(base: str, depth: int = 2, limit: int = 40000) -> List[str]:
    """Every .mod file, and every unpacked module folder, under `base` down to `depth` levels (bounded)."""
    out = []
    base = os.path.normpath(base)
    if _is_module_folder(base):
        return [base]
    base_depth = base.rstrip("\\").count(os.sep)
    for root, subdirs, files in os.walk(base):
        if _is_module_folder(root) and root != base:
            out.append(root)
            subdirs[:] = []                      # the module's own files are not candidate folders
            continue
        if root.rstrip("\\").count(os.sep) - base_depth >= depth:
            subdirs[:] = []
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        for fn in files:
            if fn.lower().endswith(".mod"):
                out.append(os.path.join(root, fn))
                if len(out) >= limit:
                    return out
    return out


def _top_level(dirs) -> List[str]:
    """Candidates sitting directly in the search folders: .mod files and unpacked module folders."""
    out = []
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            full = os.path.join(d, fn)
            if fn.lower().endswith(".mod") and os.path.isfile(full):
                if full not in out:
                    out.append(full)
            elif _is_module_folder(full):
                if full not in out:
                    out.append(full)
    return out


def _rank(cands, low: str) -> List[str]:
    """(rank, path) sorted: same name, name prefix, substring, then the module.ifo's own name."""
    ranked = []
    for p in cands:
        stem = _module_name(p)
        if stem == low:
            ranked.append((0, p))
        elif stem.startswith(low):
            ranked.append((1, p))
        elif low in stem:
            ranked.append((2, p))
        elif _module_display_name(p).lower() == low:
            ranked.append((3, p))
    return [p for _r, p in sorted(ranked)]


def module_candidates(name: str, dirs) -> List[str]:
    """Every module that could be campaign module `name`: .mod files and unpacked module folders, best match
    first (same name, prefix, substring, module.ifo), then anything up to two folders deeper."""
    if not name:
        return []
    low = name.lower().strip()
    ranked = _rank(_top_level(dirs), low)
    if ranked:
        return ranked
    deep = []
    for d in dirs:
        for p in _walk_mods(d):
            if p not in deep:
                deep.append(p)
    return _rank(deep, low)


def resolve_module(name: str, dirs) -> Optional[str]:
    """The .mod that is campaign module `name`, searched in `dirs` (see _search_dirs).

    Tried in order: the same file name, a file whose name starts with it (a translated copy often gets a suffix such
    as " (RU)"), a file whose name contains it, and finally a module whose own module.ifo names it."""
    cands = module_candidates(name, dirs)
    return cands[0] if cands else None


def quick_stats(module_path: str) -> dict:
    """Size and file counts of a module: a .mod (tables only) or an unpacked module folder (file names and sizes)."""
    stats = dict(path=module_path, name=os.path.basename(os.path.normpath(module_path)), bytes=0, files=0, by_ext={})
    if os.path.isdir(module_path):
        counts, total = {}, 0
        try:
            for full, _rel in n.walk_folder(module_path):
                ext = os.path.splitext(full)[1][1:].lower()
                counts[ext] = counts.get(ext, 0) + 1
                total += os.path.getsize(full)
        except OSError:
            pass
        stats.update(files=sum(counts.values()), by_ext=counts, bytes=total, version="folder")
        return stats
    try:
        stats["bytes"] = os.path.getsize(module_path)
    except OSError:
        pass
    try:
        erf = n.Erf(module_path)
    except (OSError, ValueError):
        return stats
    try:
        counts = {}
        for e in erf.entries:
            counts[e.ext] = counts.get(e.ext, 0) + 1
        stats["files"] = len(erf.entries)
        stats["by_ext"] = counts
        stats["version"] = erf.version
    finally:
        erf.close()
    return stats


def write_journal_tsv(campaign: dict, out_path: str) -> str:
    """The campaign journal as a tab-separated file (category, entry, text, end text) - translation material."""
    lines = ["category\tentry\tstrref\ttext\tend"]
    for cat in campaign["journal"]:
        for i, e in enumerate(cat["entries"], 1):
            text = (e["text"] or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
            end = (e["end"] or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
            lines.append(f"{cat['name']}\t{i}\t{e.get('strref') or ''}\t{text}\t{end}")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return out_path


# ----------------------------------------------------------------------------------------------- analysis
def _export_html(name, workspace):
    """Write <workspace>/<name>_report.html through nwn_dashboard --export (a separate process: the dashboard fixes
    its workspace at import time and exits after exporting)."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, NWN_WORKSPACE=workspace, PYTHONIOENCODING="utf-8")
    try:
        subprocess.run([sys.executable, os.path.join(here, "nwn_dashboard.py"), "--export", name],
                       cwd=here, env=env, capture_output=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return None
    path = os.path.join(workspace, f"{name}_report.html")
    return path if os.path.isfile(path) else None


def _journal_tags(journal_path):
    """The quest tags a .jrl defines (lower case)."""
    try:
        root = n.read_gff(open(journal_path, "rb").read())
    except (OSError, n.GffError):
        return set()
    return {str(c.get("Tag") or "").strip().lower() for c in (root.get("Categories") or []) if c.get("Tag")}


def explain_campaign_quests(out_dir, campaign_dir, verbose=True):
    """Drop the "missing quest" issues that the campaign's journal defines, from report.json and issues.csv.

    A module inside a campaign resolves journal quests against the campaign journal too (that is what joining the
    campaign does), so a quest the module's own module.JRL does not list is not missing. Returns the tags removed."""
    import csv
    import json
    jp = _find_file(campaign_dir, JOURNAL_FILE)
    tags = _journal_tags(jp) if jp else set()
    if not tags:
        return set()
    removed = set()

    def explained(label):
        low = str(label or "").strip().lower()
        tag = low[6:] if low.startswith("quest:") else low
        if tag in tags:
            removed.add(tag)
            return True
        return False

    rep_p = os.path.join(out_dir, "report.json")
    if os.path.isfile(rep_p):
        rep = json.load(open(rep_p, encoding="utf-8"))
        issues = rep.get("issues") or []
        keep = [i for i in issues if not (i.get("category") == "missing_quest" and explained(i.get("label")))]
        if len(keep) != len(issues):
            rep["issues"] = keep
            tmp = rep_p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, default=str)
            os.replace(tmp, rep_p)
    csv_p = os.path.join(out_dir, "reports", "issues.csv")
    if os.path.isfile(csv_p):
        with open(csv_p, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
            fields = list(rows[0].keys()) if rows else []
        if fields:
            keep = [r for r in rows if not (r.get("category") == "missing_quest" and explained(r.get("label")))]
            if len(keep) != len(rows):
                with open(csv_p, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=fields)
                    w.writeheader()
                    w.writerows(keep)
    if removed and verbose:
        print(f"  campaign journal explains {len(removed)} quest(s): {', '.join(sorted(removed))}")
    return removed


def merge_campaign_journal(out_dir, campaign_dir, verbose=True):
    """Write the campaign journal's categories into <out_dir>/index.sqlite before the analysis pass.

    quests(tag, name, file_id, entries) and quest_entries(tag, entry_id, text, is_end) are what Quest health reads
    (`SELECT * FROM quests`, no join on files, so file_id may stay NULL), and the quest:<tag> node is raised to
    in_module=1 with the category's display name. Returns the number of categories merged."""
    import sqlite3
    jp = _find_file(campaign_dir, JOURNAL_FILE)
    if not jp:
        return 0
    try:
        root = n.read_gff(open(jp, "rb").read())
    except (OSError, n.GffError):
        return 0
    db_path = os.path.join(out_dir, "index.sqlite")
    if not os.path.isfile(db_path):
        return 0
    db = sqlite3.connect(db_path)
    merged = 0
    try:
        for cat in (root.get("Categories") or []):
            tag = str(cat.get("Tag") or "").strip()
            if not tag:
                continue
            name = str(_loc(cat.get("Name")) or tag)
            ents = cat.get("EntryList") or []
            db.execute("DELETE FROM quests WHERE tag=?", (tag,))
            db.execute("INSERT INTO quests VALUES (?,?,?,?)", (tag, name, None, len(ents)))
            db.execute("DELETE FROM quest_entries WHERE tag=?", (tag,))
            for e in ents:
                db.execute("INSERT INTO quest_entries VALUES (?,?,?,?)",
                           (tag, e.get("ID"), str(_loc(e.get("Text")) or ""), e.get("End", 0)))
            db.execute("INSERT INTO nodes(node, type, name, label, file_id, in_module) VALUES (?,?,?,?,NULL,1) "
                       "ON CONFLICT(node) DO UPDATE SET label=excluded.label, in_module=1",
                       (f"quest:{tag}", "quest", tag, f"{name} [{tag}]"))
            merged += 1
        # dialogue script parameters: the tag is passed to a helper script instead of sitting in a Quest field
        if merged:
            tag_set = {r[0] for r in db.execute("SELECT tag FROM quests")}
            added = 0
            for file_id, path, value in db.execute(
                    "SELECT file_id, path, value FROM fields WHERE value LIKE 'z%'").fetchall():
                if value not in tag_set or "Parameters[" not in path:
                    continue
                row = db.execute("SELECT node FROM files WHERE id=?", (file_id,)).fetchone()
                if not row or not row[0]:
                    continue
                kind = "quest_ref" if "ScriptList[" in path else "quest_check"
                db.execute("INSERT INTO edges(src, dst, kind, via) VALUES (?,?,?,?)",
                           (row[0], f"quest:{value}", kind, "dialogue script parameter"))
                added += 1
            if added and verbose:
                print(f"  dialogue script parameters: {added} quest link(s) added to the graph")
        db.commit()
    finally:
        db.close()
    if merged and verbose:
        print(f"  campaign journal merged into the index: {merged} quest(s) with their stages")
    return merged


def analyse(path, nwn_root=None, nwn_user=None, workspace=None, deep=True, export=True, verbose=True):
    """Analyse every campaign `path` names (see find_campaigns).

    deep: also index and analyse each module the campaign lists (a big module takes minutes and a large index).
    export: write one HTML report per module into `workspace`.
    Returns dict(campaigns=[summary, ...]) where each summary holds the campaign data, the modules it resolved, their
    stats and the paths of everything written. Writes only inside `workspace`."""
    import json
    import nwn_analysis
    import nwn_index
    import nwn_update

    ws = workspace or nwn_update.workspace_dir(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(ws, exist_ok=True)
    found = find_campaigns(path)
    out = dict(campaigns=[])
    for folder in found:
        info = read_campaign(folder)
        # The campaign sits in a user folder (hak/, tlk/ beside Campaigns/). Without one the module's haks - and so
        # most of its blueprints and text - look missing, so fall back on that folder.
        user_for_haks = nwn_user
        if not user_for_haks:
            for cand in (os.path.dirname(os.path.dirname(os.path.normpath(folder))),
                         os.path.dirname(os.path.normpath(folder))):
                if any(os.path.isdir(os.path.join(cand, k)) for k in ("hak", "tlk", "override")):
                    user_for_haks = cand
                    break
            if user_for_haks and verbose:
                print(f"  user folder (for haks and talk tables): {user_for_haks}")
        safe = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in info["name"]).strip() or "campaign"
        cdir = os.path.join(ws, f"{safe} (campaign)")
        os.makedirs(cdir, exist_ok=True)
        if info["journal"]:
            info["journal_tsv"] = write_journal_tsv(info, os.path.join(cdir, "journal.tsv"))

        dirs = _search_dirs(folder, extra=[user_for_haks, nwn_root, path])
        modules = []
        for name in info["modules"]:
            cands = module_candidates(name, dirs)
            mod = cands[0] if cands else None
            entry = dict(name=name, path=mod, found=bool(mod),
                         candidates=[dict(path=c, bytes=os.path.getsize(c)) for c in cands[:8]])
            if len(cands) > 1 and verbose:
                print(f"  {len(cands)} candidates for {name!r}; using {os.path.basename(mod)}:")
                for c in cands[:8]:
                    print(f"      {c}")
            if mod:
                entry.update(quick_stats(mod))
                if deep:
                    mod_name = os.path.splitext(os.path.basename(mod))[0]
                    analysis_name = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in mod_name).strip()
                    analysis_name = analysis_name.replace(" ", "_") or "module"
                    out_dir = os.path.join(ws, analysis_name)
                    if verbose:
                        print(f"  analysing module {name!r} from {os.path.basename(mod)} -> {analysis_name}")
                    try:
                        nwn_index.run_index(mod, [], None, None, out_dir, nwn_root=nwn_root,
                                            nwn_user=user_for_haks)
                        merge_campaign_journal(out_dir, folder, verbose=verbose)
                        rep = nwn_analysis.run_analysis(out_dir, verbose=False)
                        explain_campaign_quests(out_dir, folder, verbose=verbose)
                        entry["analysis"] = dict(name=analysis_name, dir=out_dir,
                                                 issues=len(rep.get("issues") or []),
                                                 files=len(rep.get("files") or []))
                        if export:
                            entry["report_html"] = _export_html(analysis_name, ws)
                    except Exception as ex:  # noqa - one bad module must not stop the campaign
                        entry["analysis_error"] = f"{type(ex).__name__}: {ex}"
            else:
                if verbose:
                    print(f"  module {name!r}: no .mod found in {len(dirs)} folder(s) - skipped")
            modules.append(entry)
        info["modules_resolved"] = modules
        info.pop("journal_full", None)

        with open(os.path.join(cdir, "campaign.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1)
        md = [f"# Кампания: {info['display_name'] or info['name']}", "",
              f"* папка: `{folder}`",
              f"* стартовый модуль: `{info['start_module']}`",
              f"* модулей в Campaign.cam: {len(info['modules'])}",
              f"* записей журнала кампании: {info['journal_entries']} в {len(info['journal'])} категориях", ""]
        if info["settings"]:
            md += ["## Настройки кампании", ""]
            md += [f"* {k}: {v}" for k, v in info["settings"].items() if k != "ModNames"]
            md += [""]
        md += ["## Модули", "", "| модуль | найден | файлов | размер | анализ |", "|---|---|---|---|---|"]
        for m in modules:
            size = f"{m['bytes'] / 1048576:.1f} МБ" if m.get("bytes") else "-"
            an = m.get("analysis", {}).get("name") or m.get("analysis_error") or "-"
            md.append(f"| {m['name']} | {'да' if m['found'] else 'нет'} | {m.get('files') or '-'} | {size} | {an} |")
        md += [""]
        if info["journal"]:
            md += ["## Журнал кампании", ""]
            for cat in info["journal"]:
                md.append(f"* {cat['name']}: {len(cat['entries'])} записей")
            md += ["", f"Полный текст: `{info.get('journal_tsv') or ''}`", ""]
        with open(os.path.join(cdir, "campaign.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(md))
        info["campaign_dir"] = cdir
        out["campaigns"].append(info)
    return out


def main(argv=None):
    """Command line: show a campaign and analyse its modules.

        python nwn_campaign.py "<campaign folder or user folder>" [--list] [--nwn-root P] [--nwn-user P]
    """
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--list", action="store_true", help="only show the campaign and its modules; analyse nothing")
    ap.add_argument("--nwn-root")
    ap.add_argument("--nwn-user")
    ap.add_argument("--no-export", action="store_true")
    a = ap.parse_args(argv)
    if not (a.nwn_root and a.nwn_user):
        try:
            import nwn_dashboard
            st = nwn_dashboard.settings()
        except Exception:  # noqa
            st = {}
        a.nwn_root = a.nwn_root or st.get("nwn_root")
        a.nwn_user = a.nwn_user or st.get("nwn_user")
    res = analyse(a.path, a.nwn_root, a.nwn_user, deep=not a.list, export=not a.no_export)
    if not res["campaigns"]:
        print(f"No campaign found at {a.path} (looked for {CAMPAIGN_FILE})")
        return 2
    for c in res["campaigns"]:
        print(f"Campaign: {c['display_name'] or c['name']}  ({c['folder']})")
        print(f"  start module: {c['start_module']}   journal: {c['journal_entries']} entries "
              f"in {len(c['journal'])} categories")
        for m in c["modules_resolved"]:
            state = "found" if m["found"] else "MISSING"
            extra = ""
            if m.get("analysis"):
                extra = f"  -> analysis '{m['analysis']['name']}' ({m['analysis']['issues']} issues)"
            elif m.get("analysis_error"):
                extra = f"  -> {m['analysis_error']}"
            print(f"  module {m['name']!r}: {state} {m.get('path') or ''}{extra}")
        print(f"  report: {os.path.join(c['campaign_dir'], 'campaign.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
