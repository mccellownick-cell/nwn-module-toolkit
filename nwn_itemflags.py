"""
nwn_itemflags.py - item flags (Stolen, Plot, Undroppable) on the items placed in areas, compared with their blueprints.

    python nwn_itemflags.py <analysis folder> [--csv FILE] [--all]

Why this exists
---------------
A builder auditing which items are Stolen, Plot or Undroppable needs two lists: the item blueprints (the Blueprints
page shows their flags, from palette.json) and the items already placed in areas - in creature inventories and
equipment, containers, stores and on the ground. A placed item is a full copy that keeps its own flags, so fixing a
blueprint does not fix copies placed before; this module lists the copies and says where each one differs from its
blueprint. "Undroppable" is the item's Cursed field (NWScript SetItemCursedFlag: a cursed item can't be dropped).

What it reads and writes
------------------------
Reads the analysis index (index.sqlite, read-only): the placed item objects (objects, class 'item', not blueprints),
their Stolen / Plot / Cursed fields (fields; a placed item's fields sit under its own path in the area's .git), the
item blueprints' flags (palette data, the copy the game uses) and, for items whose blueprint is the base game's, the
game's own blueprint (nwnlib.BaseGame, when the analysis knows the install folder). Only the copy of each area the
game uses counts (haks in module.ifo order, then the module; nwnlib.source_rank). Writes nothing, except the CSV file
the command line is asked for.

Limits
------
Items created by scripts at run time are not in the module, so they are not listed (their blueprint's flags apply).
An item placed by name only (a store or creature blueprint that lists it as InventoryRes) is made from its blueprint
when the game creates it: it has no flags of its own and is not listed here.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import nwn_csvio
import nwn_palette
import nwnlib as n

FLAGS = (("Stolen", "stolen"), ("Plot", "plot"), ("Cursed", "undroppable"))   # GFF label, name on the page
FLAG_NAMES = [nm for _lab, nm in FLAGS]
CSV_COLUMNS = ["name", "resref", "tag", "area", "area_resref", "area_from", "held_by", "where", "stolen", "plot",
               "undroppable", "blueprint_flags", "differs_from_blueprint"]


def flag_text(flags):
    """A flag list as people read it: "stolen + undroppable", or "none"."""
    return " + ".join(flags) if flags else "none"


def placed_item_flags(analysis, pal=None, all_copies=False):
    """The placed item copies of an analysis with their flags.

    analysis: the analysis folder. pal: its palette data (nwn_palette.build_palette / the dashboard's palette_data), for
    the item blueprints' flags; built (not saved) when not given. all_copies: also list copies with no flag that match
    their blueprint (normally only copies with a flag, or that differ from their blueprint, are listed).
    Returns dict(copies [{node, resref, name, tag, area, area_name, area_src, held_by, held_by_node, where, flags,
    bp_flags, bp_source, differs}], summary {placed, listed, stolen, plot, undroppable, differs, no_blueprint, in_hak}).
    area_src: "module", or the hak whose copy of the area the game uses - a build changes module files only, so it can't
    change copies in such an area (change them in the hak).
    bp_flags is None when no blueprint of that name exists (module, haks or base game); bp_source says whose blueprint
    it is ("module", a hak's name, "base game"). Read-only; raises sqlite3.OperationalError when the index can't be
    opened."""
    pal = pal or nwn_palette.build_palette(analysis, write=False)
    t = pal["types"]["uti"]
    bp = {b["resref"]: (list(b.get("flags") or []), pal["sources"][b["src"]]) for b in t["blueprints"]}
    db = n.sqlite_ro(os.path.join(analysis, "index.sqlite"))
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        # the copy of each area file the game uses: module and haks only (the builder's override is not the module's)
        best = {}
        for fid, resref, kind, prio, spath in db.execute(
                "SELECT f.id, f.resref, s.kind, s.priority, s.path FROM files f JOIN sources s ON s.id=f.source_id "
                "WHERE f.ext='git' AND s.kind IN ('module', 'hak')"):
            rk = (n.source_rank(kind, prio), fid)
            key = (resref or "").lower()
            if key not in best or rk < best[key][0]:
                best[key] = (rk, fid, nwn_palette._source_name(kind, spath))
        gits = {fid: src for _rk, fid, src in best.values()}     # winning .git file id -> "module" or the hak's name
        # Stolen / Plot / Cursed set on placed items: (file id, the item's path) -> {flag name}; a field that is 0 or
        # missing means not set (as in the game)
        set_flags = {}
        for fid, path, label, value in db.execute(
                "SELECT file_id, path, label, value FROM fields WHERE label IN ('Stolen', 'Plot', 'Cursed')"):
            if fid not in gits or "/" not in path:
                continue
            owner = path.rsplit("/", 1)[0]
            if _int(value):
                set_flags.setdefault((fid, owner), set()).add(dict(FLAGS)[label])
        objs = db.execute("SELECT file_id, node, template, tag, name, area, container, path FROM objects "
                          "WHERE class='item' AND is_blueprint=0").fetchall()
        holders = {}
        # node ids (inst:<area>:<path>) repeat in every copy of an area: take the holder from the copy the game uses
        for fid, node, cls, name, tag in db.execute("SELECT file_id, node, class, name, tag FROM objects WHERE "
                                                    "is_blueprint=0 AND class IN ('creature', 'placeable', 'store', 'item')"):
            if fid in gits:
                holders[node] = f"{cls} '{name or tag}'" + (f" ({tag})" if tag and name and tag != name else "")
        area_names = {}
        for node, label in db.execute("SELECT node, label FROM nodes WHERE type='area'"):
            m = re.match(r"(.*) \[[^\]]*\]$", label or "")
            area_names[node[5:]] = m.group(1) if m else ""
    finally:
        db.close()

    base_game = None
    base_cache = {}

    def blueprint_flags(resref):
        """(flags, source) of the blueprint the game would use for resref; (None, "") when there is none."""
        nonlocal base_game
        if resref in bp:
            return bp[resref]
        if resref not in base_cache:
            base_cache[resref] = (None, "")
            if meta.get("nwn_root"):
                if base_game is None:
                    base_game = n.BaseGame(meta["nwn_root"])
                data = base_game.get(resref + ".uti")
                if data:
                    try:
                        root = n.read_gff(data)
                        base_cache[resref] = ([nm for lab, nm in FLAGS if _int(root.get(lab))], "base game")
                    except n.GffError:
                        pass
        return base_cache[resref]

    copies, summary = [], dict(placed=0, listed=0, differs=0, no_blueprint=0, in_hak=0, **{f: 0 for f in FLAG_NAMES})
    for fid, node, tpl, tag, name, area, container, path in objs:
        if fid not in gits:
            continue                     # another copy of this area (a hak's, or the override's) is the one the game uses
        summary["placed"] += 1
        flags = [f for f in FLAG_NAMES if f in set_flags.get((fid, path), ())]
        resref = (tpl or "").lower()
        bpf, bsrc = blueprint_flags(resref)
        differs = bpf is not None and sorted(bpf) != sorted(flags)
        if not (flags or differs or all_copies):
            continue
        for f in flags:
            summary[f] += 1
        summary["differs"] += differs
        summary["no_blueprint"] += bpf is None
        summary["in_hak"] += gits[fid] != "module"
        copies.append(dict(node=node, resref=resref, name=name or "", tag=tag or "", area=area or "",
                           area_name=area_names.get(area or "", ""), held_by=holders.get(container, "on the ground")
                           if container and not container.startswith("area:") else "on the ground",
                           held_by_node=container if container and not container.startswith("area:") else None,
                           where=path, flags=flags, bp_flags=bpf, bp_source=bsrc, differs=differs,
                           area_src=gits[fid]))
    summary["listed"] = len(copies)
    copies.sort(key=lambda c: (not c["differs"], c["area_name"].lower() or c["area"], c["where"]))
    return dict(copies=copies, summary=summary)


def _int(v):
    """A stored number as int; 0 for empty or unreadable values (a missing flag is not set)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def csv_rows(data):
    """One CSV_COLUMNS dict per listed copy of placed_item_flags()."""
    return [dict(name=c["name"], resref=c["resref"], tag=c["tag"], area=c["area_name"], area_resref=c["area"],
                 area_from=c["area_src"],
                 held_by=c["held_by"], where=c["where"], **{f: "yes" if f in c["flags"] else "" for f in FLAG_NAMES},
                 blueprint_flags="no blueprint" if c["bp_flags"] is None else
                 flag_text(c["bp_flags"]) + (" (base game)" if c["bp_source"] == "base game" else ""),
                 differs_from_blueprint="yes" if c["differs"] else "") for c in data["copies"]]


def main(argv=None):
    """Command line: print the counts; --csv writes the list (spreadsheet-safe cells). Returns the exit code."""
    ap = argparse.ArgumentParser(description="Item flags (Stolen, Plot, Undroppable) of the items placed in areas.")
    ap.add_argument("analysis", help="the analysis folder (holds index.sqlite)")
    ap.add_argument("--csv", help="write the list to this CSV file")
    ap.add_argument("--all", action="store_true", help="also list copies with no flag that match their blueprint")
    a = ap.parse_args(argv)
    data = placed_item_flags(a.analysis, all_copies=a.all)
    s = data["summary"]
    print(f"{s['placed']} placed item copies; {s['listed']} listed: stolen {s['stolen']}, plot {s['plot']}, "
          f"undroppable {s['undroppable']}; {s['differs']} differ from their blueprint; {s['no_blueprint']} have no "
          "blueprint")
    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            wr.writeheader()
            wr.writerows({k: nwn_csvio.cell(v) for k, v in r.items()} for r in csv_rows(data))
        print(f"written: {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
