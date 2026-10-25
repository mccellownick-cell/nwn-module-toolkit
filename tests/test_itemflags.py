"""
Tests for item flags (Stolen, Plot, Undroppable = the Cursed field): the Blueprints page data (nwn_palette), the placed
copies and how each differs from its blueprint (nwn_itemflags), and setting the flags with Change fields…
(nwn_bpchanges), which the next build applies - with "all placed copies", also to copies that hold their own value -
and the audit proves.
    python tests/test_itemflags.py          (last line "N/M checks passed"; exit code 1 on a failure)
Works only in temporary folders and the test workspace (see tests/_harness.py).
"""
import _harness as h  # noqa: F401 - first: isolates the workspace and the compiler before toolkit modules load
import csv
import os
import shutil
import sys

import nwnlib as n  # noqa: E402
import nwn_bpchanges as BC  # noqa: E402
import nwn_build  # noqa: E402
import nwn_dashboard as D  # noqa: E402
import nwn_itemflags as IF  # noqa: E402
import nwn_palette  # noqa: E402
from _harness import check  # noqa: E402
from _gff import st, root as gff, loc, item_fields, w, R, X, B, LST  # noqa: E402


def item(resref, tag, name, **flags):
    """An item's fields with flags given as stolen=1, plot=1, cursed=1 (Stolen / Plot / Cursed bytes)."""
    f = item_fields(resref, tag, name)
    for k, v in flags.items():
        f[k.capitalize()] = (B, v)
    return f


def fixture(tmp):
    """A module: sword (Stolen), key (Plot + Undroppable), plain (no flag) blueprints; one area holding a chest with
    copies that match and differ, a creature wearing a plain copy, a creature listing the sword by name only, and a
    gem on the ground whose blueprint is the base game's (not Stolen) while the copy is."""
    mod = os.path.join(tmp, "mod"); os.makedirs(mod)
    game = os.path.join(tmp, "game")
    n.write_key_bif(game, {("nw_it_gem001", "uti"): n.write_gff(gff("UTI ", **item("nw_it_gem001", "NW_GEM", "Gem")))})
    g = lambda fn, r: w(mod, fn, n.write_gff(r))  # noqa: E731
    g("module.ifo", gff("IFO ", Mod_Name=loc("Flags"), Mod_Entry_Area=(R, "yard"),
                        Mod_Area_list=(LST, [st(6, Area_Name=(R, "yard")), st(6, Area_Name=(R, "cave"))])))
    # bare: a hand-made item blueprint with no Stolen field at all (item_fields writes Plot and Cursed, not Stolen)
    g("bare.uti", gff("UTI ", **item_fields("bare", "BARE", "Bare ring")))
    g("cave.are", gff("ARE ", Name=loc("Cave"), Tag=(X, "CAVE"), ResRef=(R, "cave"), Tileset=(R, "tdc01")))
    # the module's copy of the cave; the hak's copy (below) is the one the game uses
    g("cave.git", gff("GIT ", **{"Placeable List": (LST, [st(9, TemplateResRef=(R, "chest"), Tag=(X, "CHEST"),
        LocName=loc("Module chest"), HasInventory=(B, 1), ItemList=(LST, [st(0, **item("plain", "PLAIN", "Plain cloak"))]))])}))
    hk = os.path.join(tmp, "hk"); os.makedirs(hk)
    w(hk, "cave.git", n.write_gff(gff("GIT ", **{"Placeable List": (LST, [st(9, TemplateResRef=(R, "chest"), Tag=(X, "CHEST"),
        LocName=loc("Hak chest"), HasInventory=(B, 1), ItemList=(LST, [st(0, **item("key", "KEY", "Gate key", plot=1, cursed=1))]))])})))
    hak = os.path.join(tmp, "cavehak.hak")
    h.pack_hak(hk, hak)
    g("yard.are", gff("ARE ", Name=loc("Old Yard"), Tag=(X, "YARD"), ResRef=(R, "yard"), Tileset=(R, "tcn01")))
    g("sword.uti", gff("UTI ", **item("sword", "SWORD", "Sword", stolen=1)))
    g("key.uti", gff("UTI ", **item("key", "KEY", "Gate key", plot=1, cursed=1)))
    g("plain.uti", gff("UTI ", **item("plain", "PLAIN", "Plain cloak")))
    g("chest.utp", gff("UTP ", TemplateResRef=(R, "chest"), Tag=(X, "CHEST"), LocName=loc("Chest")))
    g("guard.utc", gff("UTC ", TemplateResRef=(R, "guard"), Tag=(X, "GUARD"), FirstName=loc("Guard")))
    g("itempalcus.itp", gff("ITP ", MAIN=(LST, [st(0, NAME=(X, "Custom"), ID=(B, 1))])))
    g("yard.git", gff("GIT ", **{
        "Placeable List": (LST, [st(9, TemplateResRef=(R, "chest"), Tag=(X, "CHEST"), LocName=loc("Chest"),
                                    HasInventory=(B, 1), XPosition=(n.FLOAT, 3.0), YPosition=(n.FLOAT, 4.0),
                                    ItemList=(LST, [st(0, **item("sword", "SWORD", "Sword", stolen=1)),   # same
                                                    st(0, **item("sword", "SWORD", "Sword", stolen=0)),   # differs
                                                    st(0, **item("plain", "PLAIN", "Plain cloak", stolen=1)),  # differs
                                                    st(0, **item("key", "KEY", "Gate key", plot=1, cursed=1)),
                                                    st(0, **item("bare", "BARE", "Bare ring", stolen=0)),  # Stolen 0
                                                    st(0, **item_fields("bare", "BARE", "Bare ring"))]))]),  # no field
        "Creature List": (LST, [st(4, TemplateResRef=(R, "guard"), Tag=(X, "GUARD"), FirstName=loc("Guard"),
                                   XPosition=(n.FLOAT, 1.0), YPosition=(n.FLOAT, 2.0),
                                   Equip_ItemList=(LST, [st(2, **item("plain", "PLAIN", "Plain cloak"))]),
                                   ItemList=(LST, [st(0, InventoryRes=(R, "sword"))]))]),
        "List": (LST, [st(0, **item("nw_it_gem001", "NW_GEM", "Gem", stolen=1), XPosition=(n.FLOAT, 7.0),
                          YPosition=(n.FLOAT, 8.0))])}))
    out = os.path.join(tmp, "analysis")
    h.analyse(mod, out, haks=[hak], nwn_root=game)
    return out


def blueprints(out):
    """The Blueprints page data: each item blueprint's flags, and the CSV columns."""
    pal = nwn_palette.build_palette(out, write=False)
    fl = {b["resref"]: b.get("flags") for b in pal["types"]["uti"]["blueprints"]}
    check("palette: each item blueprint carries the flags it has set (Cursed shown as undroppable); none = no key",
          fl == {"sword": ["stolen"], "key": ["plot", "undroppable"], "plain": None, "bare": None}, fl)
    check("palette: version 3 (older palette.json files are rebuilt)", pal["version"] == 3 == nwn_palette.VERSION)
    rows = {r["resref"]: r for r in nwn_palette.csv_rows(pal) if r["type"] == "Items"}
    check("palette CSV: stolen / plot / undroppable columns", (rows["key"]["plot"], rows["key"]["undroppable"],
          rows["key"]["stolen"], rows["sword"]["stolen"]) == ("yes", "yes", "", "yes") and
          {"stolen", "plot", "undroppable"} <= set(nwn_palette.CSV_COLUMNS), rows["key"])


def placed(out):
    """nwn_itemflags: the placed copies with a flag or differing from their blueprint, where they are and who holds them."""
    data = IF.placed_item_flags(out)
    cs = data["copies"]
    got = sorted((c["resref"], tuple(c["flags"]), c["differs"]) for c in cs)
    check("placed: copies with a flag, or that differ from their blueprint, are listed (a matching plain copy is not)",
          got == sorted([("sword", ("stolen",), False), ("sword", (), True), ("plain", ("stolen",), True),
                         ("key", ("plot", "undroppable"), False), ("key", ("plot", "undroppable"), False),
                         ("nw_it_gem001", ("stolen",), True)]), got)
    hk = [c for c in cs if c["area"] == "cave"]
    check("placed: in an area whose hak copy wins, the hak's items are listed (marked with the hak), not the module's",
          len(hk) == 1 and hk[0]["area_src"] == "cavehak" and hk[0]["resref"] == "key" and
          hk[0]["held_by"].startswith("placeable 'Hak chest'") and all(c["area_src"] == "module" for c in cs if c["area"] != "cave"), hk)
    gem = next(c for c in cs if c["resref"] == "nw_it_gem001")
    check("placed: an item whose blueprint is the base game's is compared with the game's own blueprint",
          gem["bp_source"] == "base game" and gem["bp_flags"] == [] and gem["held_by"] == "on the ground", gem)
    sw = next(c for c in cs if c["resref"] == "sword" and c["differs"])
    check("placed: each copy says its area (name and resref), who holds it and where in the area file",
          sw["area_name"] == "Old Yard" and sw["area"] == "yard" and sw["held_by"].startswith("placeable 'Chest'") and
          sw["where"] == "Placeable List[0]/ItemList[1]" and sw["bp_flags"] == ["stolen"], sw)
    check("placed: copies that differ come first", [c["differs"] for c in cs] == sorted([c["differs"] for c in cs], reverse=True))
    s = data["summary"]
    check("placed: summary counts", (s["placed"], s["listed"], s["stolen"], s["plot"], s["undroppable"], s["differs"],
                                     s["no_blueprint"], s["in_hak"]) == (9, 6, 3, 2, 2, 3, 0, 1), s)
    check("placed: all_copies also lists copies that match with no flag",
          len(IF.placed_item_flags(out, all_copies=True)["copies"]) == 9)
    check("placed: an item listed by name only (InventoryRes) is not a copy of its own",
          not any(c["held_by"].startswith("creature") and c["resref"] == "sword" for c in cs))
    p = os.path.join(out, "flags.csv")
    rc = IF.main([out, "--csv", p])
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    check("command line: --csv writes one row per listed copy with the blueprint's flags",
          rc == 0 and len(rows) == 6 and rows[0]["differs_from_blueprint"] == "yes" and
          any(r["blueprint_flags"] == "none (base game)" for r in rows), rows[:2])


def change_and_build(out):
    """Change fields… for flags: yes/no, refused values, "all placed copies", then the build and the audit."""
    ctx = BC.load_context(out)
    check("context: flags read as yes / no", ctx["values"][("uti", "key")]["plot"] == "yes" and
          ctx["values"][("uti", "plain")].get("stolen", "no") == "no", ctx["values"][("uti", "key")])
    p = BC.plan_values(ctx, "uti", ["sword"], {"stolen": "No"})
    check("plan: a flag set to no", [(c["field"], c["from"], c["to"]) for c in p["changes"]] == [("stolen", "yes", "no")], p)
    p = BC.plan_values(ctx, "uti", ["sword"], {"plot": "maybe"})
    check("plan: a flag value other than yes / no is refused", not p["changes"] and "yes or no" in p["refused"][0]["why"], p)
    p = BC.plan_values(ctx, "uti", ["plain"], {"stolen": "no"})
    check("plan: no on a blueprint that already has no is unchanged", not p["changes"] and p["unchanged"] == 1, p)
    p = BC.plan_values(ctx, "uti", ["plain"], {"stolen": "no"}, all_copies=True)
    check("plan: with all placed copies it is a change (copies may differ), marked copies all",
          [(c["field"], c["to"], c.get("copies")) for c in p["changes"]] == [("stolen", "no", "all")], p)
    p2 = BC.plan_values(ctx, "uti", ["plain"], {"name": "Cloak"}, all_copies=True)
    check("plan: all placed copies is only for item flags", "copies" not in p2["changes"][0], p2)
    BC.undo_changes(out)
    BC.save_plan(out, BC.plan_values(ctx, "uti", ["sword"], {"stolen": "no"}))
    BC.save_plan(out, p)
    BC.save_plan(out, BC.plan_values(ctx, "uti", ["key"], {"undroppable": "no"}))
    BC.save_plan(out, BC.plan_values(ctx, "uti", ["bare"], {"stolen": "yes"}))      # blueprint has no Stolen field
    ch = BC.load_changes(out)["uti"]
    check("store: the all-copies choice is kept with the change",
          ch["plain"]["stolen"] == {"to": "no", "from": "no", "copies": "all"} and "copies" not in ch["sword"]["stolen"], ch)
    log = nwn_build.build(out, dict(delete=[], merge=[]), name="flags", verbose=False,
                          audit_kwargs=dict(compiler=h.NO_COMPILER))
    clean = os.path.join(out, "build", "flags")
    rd = lambda fn: n.read_gff_file(os.path.join(clean, fn))  # noqa: E731
    check("build: the blueprints get the new flags", rd("sword.uti").get("Stolen") == 0 and rd("key.uti").get("Cursed") == 0
          and rd("key.uti").get("Plot") == 1, (rd("sword.uti").get("Stolen"), rd("key.uti").get("Cursed")))
    check("build: a blueprint without the field gets it added (a missing flag is not set)",
          rd("plain.uti").type_of("Stolen") == n.BYTE and rd("plain.uti").get("Stolen") == 0)
    inv = rd("yard.git").get("Placeable List")[0].get("ItemList")
    check("build: a copy still holding the old value follows; one with its own value keeps it",
          inv[0].get("Stolen") == 0 and inv[1].get("Stolen") == 0, [i.get("Stolen") for i in inv])
    check("build: with all placed copies, a copy holding its own value is set too", inv[2].get("Stolen") == 0, inv[2].get("Stolen"))
    check("build: the key copy loses Undroppable, keeps Plot", (inv[3].get("Cursed"), inv[3].get("Plot")) == (0, 1))
    check("build: a blueprint without the flag counts as not set, so copies holding 0 follow - also a copy with no field",
          rd("bare.uti").get("Stolen") == 1 and inv[4].get("Stolen") == 1 and inv[5].type_of("Stolen") == n.BYTE and
          inv[5].get("Stolen") == 1, (inv[4].get("Stolen"), inv[5].get("Stolen")))
    bc = {(c["resref"], c["field"]): c for c in log["blueprint_changes"]}
    check("build: the change log counts the copies changed and marks all copies",
          bc[("sword", "stolen")]["placed"] == 1 and bc[("plain", "stolen")]["placed"] == 1 and
          bc[("bare", "stolen")]["placed"] == 2 and
          bc[("plain", "stolen")].get("copies") == "all", bc)
    checks = {c["id"]: c for c in log["audit"]["checks"]}
    for cid in ("modified", "blueprint_changes", "placements"):
        check(f"audit: {cid} passes", checks.get(cid, {}).get("status") == "PASS", checks.get(cid))
    md = open(os.path.join(out, "build", "changes.md"), encoding="utf-8").read()
    check("changes.md marks all copies", "(all copies)" in md)
    try:
        BC.new_value("stolen", "maybe")
        bad = False
    except ValueError:
        bad = True
    check("a flag value that isn't yes / no is never guessed (a hand-edited change is skipped, not cleared)", bad)
    BC.undo_changes(out)
    BC._save(out, {"uti": {"sword": {"stolen": {"to": "maybe", "from": "yes"}}}})
    log = nwn_build.build(out, dict(delete=[], merge=[]), name="flags2", verbose=False, audit_kwargs=dict(compiler=h.NO_COMPILER))
    e = log["blueprint_changes"][0]
    check("build: such a change is skipped with a note, the blueprint keeps its flag",
          e["status"] == "skipped" and "not a valid value" in e["note"] and
          n.read_gff_file(os.path.join(out, "build", "flags2", "sword.uti")).get("Stolen") == 1, e)
    csvb = ("type,resref,new_stolen,all_copies\nItems,sword,No,\nItems,sword,0,\nItems,plain,no,yes\nItems,plain,no,\n"
            "Items,key,Yes,\n").encode()
    pc = BC.plan_csv(BC.load_context(out), csvb)
    why = {r["item"]: r["why"] for r in pc["refused"]}
    check("CSV: No and 0 are the same value; rows that disagree about all_copies are refused",
          [(c["resref"], c["to"]) for c in pc["changes"]] == [("sword", "no"), ("key", "yes")] and
          "all_copies" in why.get("uti:plain", ""), pc)
    BC.undo_changes(out)


def api_and_page(out):
    """/api/itemflags (Blueprints page, Placed item flags), the API's all_copies, and the page's parts."""
    a = "iftest"
    d = os.path.join(D.WORKSPACE, a)
    shutil.copytree(out, d)
    try:
        r = D.GET_ROUTES["/api/itemflags"]({"a": a})
        check("api: the placed copies and the summary", r["summary"]["listed"] == 6 and len(r["copies"]) == 6, r["summary"])
        r2 = D.GET_ROUTES["/api/itemflags"]({"a": a, "all": "1"})
        check("api: all=1 also lists matching copies with no flag", len(r2["copies"]) == 9)
        r = D.POST_ROUTES["/api/bpchange/plan"]({"a": a, "ext": "uti", "resrefs": ["plain"], "values": {"stolen": "no"},
                                                 "all_copies": True})
        check("api: Change fields passes all placed copies on", r["changes"] and r["changes"][0].get("copies") == "all", r)
        r = D.POST_ROUTES["/api/bpchange/plan"]({"a": a, "ext": "uti", "resrefs": ["plain"], "values": {"stolen": "no"},
                                                 "all_copies": "false"})
        check("api: all_copies \"false\" is not true", not r["changes"] and r["unchanged"] == 1, r)
    finally:
        D.release_analysis(a)
    html = open(os.path.join(h.ROOT, "dashboard.html"), encoding="utf-8").read()
    check("page: Items have a flag filter and a Flags column, and Change fields offers the flags",
          'id: "flag"' in html and 'key: "flags"' in html and '"stolen", "plot", "undroppable"' in html)
    check("page: a Placed item flags view reads /api/itemflags, with Export CSV",
          '/api/itemflags?a=' in html and '["flags", "Placed item flags"]' in html and "placed_item_flags" in html)
    check("page: Change fields has the all placed copies choice", 'id="cfall"' in html and "all_copies" in html)
    check("page: Export CSV has the flag columns and new_stolen", '"new_stolen", "new_plot", "new_undroppable"' in html)


def main():
    with h.tempdir("nwn_if_") as tmp:
        out = h.run(fixture, tmp)
        if out:
            for fn in (blueprints, placed, change_and_build, api_and_page):
                h.run(fn, out)
    return h.summary()


if __name__ == "__main__":
    sys.exit(main())
