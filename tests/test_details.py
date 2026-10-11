"""
Tests for the details shown for areas and every other game object (the owner: "in Areas, capture all associated
metadata, include variables on area, settings, and scripts ... same for creatures ... and any other object; when
exploring an area or blueprint, make sure scripts are listed"), and for the side panel closing when the page changes.
  - report.json areas[]: props (every .are / .git AreaProperties value, decoded), variables (the area's own Variables:
    the VarTable at the top of its .git), summary (interior/exterior, natural/urban, PvP, no rest, always night);
  - reports/areas.csv and area_variables.csv;
  - /api/node details (nwn_analysis.object_details) for a placed creature, a creature blueprint, a placeable, an area
    and the module: every event script (creature Script* fields named as the toolset names them, missing ones flagged),
    the object's own Variables and its other settings.
    python tests/test_details.py          (last line "N/M checks passed"; exit code 1 on a failure)
Works only in temporary folders and the test workspace (see tests/_harness.py).
"""
import _harness as h  # noqa: F401 - first: isolates the workspace and the compiler before toolkit modules load
import csv
import os
import shutil
import sys

import nwnlib as n  # noqa: E402
import nwn_dashboard as D  # noqa: E402
from _harness import check  # noqa: E402
from _gff import st, root, loc, w, item_fields, R, X, B, I, DW, F, LST, stand_in_ncs  # noqa: E402

WORD = n.WORD


def var(name, typ, field_type, value):
    """One Variables entry (VarTable struct): Type 1 int, 2 float, 3 string."""
    return st(0, Name=(X, name), Type=(DW, typ), Value=(field_type, value))


def fixture(tmp):
    """A module with one area that has settings, music and three Variables; a placed guard (creature) with its own
    Variables and event scripts (one of them missing); a static placeable; a creature blueprint; module Variables."""
    mod = os.path.join(tmp, "mod")
    os.makedirs(mod)
    g = lambda fn, r: w(mod, fn, n.write_gff(r))  # noqa: E731
    g("module.ifo", root("IFO ", Mod_Name=loc("Details"), Mod_Entry_Area=(R, "keep"),
                         Mod_Area_list=(LST, [st(6, Area_Name=(R, "keep"))]), Mod_OnModLoad=(R, "mod_load"),
                         Mod_OnActvtItem=(R, "item_act"),
                         VarTable=(LST, [var("SERVER_NAME", 3, X, "Test PW")])))
    g("keep.are", root("ARE ", Name=loc("Old Keep"), Tag=(X, "KEEP"), ResRef=(R, "keep"), Tileset=(R, "tcn01"),
                       Width=(I, 4), Height=(I, 3), Flags=(DW, 1 | 4), PlayerVsPlayer=(B, 0), NoRest=(B, 1),
                       DayNightCycle=(B, 0), IsNight=(B, 1), SunFogColor=(DW, 0x00FF8000), ChanceRain=(I, 25),
                       WindPower=(I, 2), OnEnter=(R, "keep_enter"), OnHeartbeat=(R, ""), Comments=(X, "Built for the test")))
    guard = st(4, TemplateResRef=(R, "guard"), Tag=(X, "GUARD1"), FirstName=loc("Guard"),
               ScriptHeartbeat=(R, "guard_hb"), ScriptOnNotice=(R, "guard_gone"), ScriptSpawn=(R, ""),
               VarTable=(LST, [var("POST", 3, X, "gate"), var("ALERT", 1, I, 2)]),
               ItemList=(LST, [st(0, InventoryRes=(R, "torch")), st(1, InventoryRes=(R, "rope"))]))
    statue = st(9, TemplateResRef=(R, "statue"), Tag=(X, "STATUE"), LocName=loc("Statue"), Static=(B, 1),
                OnUsed=(R, "statue_use"))
    g("keep.git", root("GIT ", **{
        "AreaProperties": (n.STRUCT, st(100, MusicDay=(I, 12), MusicNight=(I, 13), AmbientSndDay=(I, 40),
                                        AmbientSndDayVol=(I, 32), EnvAudio=(I, 5))),
        "VarTable": (LST, [var("explored", 1, I, 1), var("BOSS_TAG", 3, X, "OGRE_KING"), var("LOOT_MULT", 2, F, 1.5)]),
        "Creature List": (LST, [guard]), "Placeable List": (LST, [statue])}))
    g("guard.utc", root("UTC ", TemplateResRef=(R, "guard"), Tag=(X, "GUARD"), FirstName=loc("Guard"),
                        ScriptHeartbeat=(R, "guard_hb"), ScriptDeath=(R, "guard_death"),
                        VarTable=(LST, [var("POST", 3, X, "wall")])))
    for nm in ("keep_enter", "guard_hb", "guard_death", "statue_use", "item_act"):
        w(mod, nm + ".nss", "void main()\n{\n}\n")
        w(mod, nm + ".ncs", stand_in_ncs(nm))
    # an item with a tag-based script that handles activation, and a script that looks the item up by its tag
    g("rune_stone.uti", root("UTI ", **item_fields("rune_stone", "rune_stone", "Rune Stone")))
    w(mod, "rune_stone.nss", '#include "x2_inc_switches"\nvoid main()\n{\n    if (GetUserDefinedItemEventNumber() == '
                             'X2_ITEM_EVENT_ACTIVATE)\n        SendMessageToPC(GetItemActivator(), "glows");\n}\n')
    w(mod, "rune_stone.ncs", stand_in_ncs("rune_stone"))
    w(mod, "mod_load.nss", 'void main()\n{\n    object o = GetItemPossessedBy(GetFirstPC(), "rune_stone");\n}\n')
    w(mod, "mod_load.ncs", stand_in_ncs("mod_load"))
    return mod


def checks(tmp):
    mod = fixture(tmp)
    a = "details_t"
    out = os.path.join(D.WORKSPACE, a)
    try:
        rep = h.analyse(mod, out)
        ar = next((x for x in rep["areas"] if x["resref"] == "keep"), {})
        props = {lab: v for _g, lab, v in ar.get("props", [])}
        check("area: type decoded from Flags (interior, above ground, natural)",
              props.get("Type") == "Interior, Above ground, Natural", props.get("Type"))
        check("area: PvP, no rest, day/night, weather, wind spelled out",
              props.get("Player vs player") == "None" and props.get("No rest") == "yes" and
              props.get("Day/night cycle") == "no" and props.get("Always night") == "yes" and
              props.get("Chance of rain (%)") == "25" and props.get("Wind") == "Strong", props)
        check("area: a colour shown as #RRGGBB (the game stores BGR)", props.get("Day fog colour") == "#0080FF",
              props.get("Day fog colour"))
        check("area: size, tileset, comments", props.get("Width (tiles)") == "4" and props.get("Tileset") == "tcn01"
              and props.get("Comments") == "Built for the test", props)
        check("area: music and ambient sound from the .git AreaProperties",
              props.get("Music, day (ambientmusic.2da row)") == "12" and
              props.get("Ambient sound, day (ambientsound.2da row)") == "40", props)
        check("area: its own Variables with type and value (the VarTable at the top of the .git)",
              [(v["name"], v["type"], str(v["value"])) for v in ar.get("variables", [])] ==
              [("explored", "int", "1"), ("BOSS_TAG", "string", "OGRE_KING"), ("LOOT_MULT", "float", "1.5")],
              ar.get("variables"))
        check("area: the placed guard's variables are not the area's", "POST" not in {v["name"] for v in ar.get("variables", [])})
        check("area: summary for the Areas table", ar.get("summary") == ["Interior", "Natural", "PvP none", "no rest",
                                                                       "always night"], ar.get("summary"))
        rows = list(csv.DictReader(open(os.path.join(out, "reports", "areas.csv"), encoding="utf-8-sig")))
        check("reports/areas.csv: one row per area, every property its own column",
              rows and rows[0]["resref"] == "keep" and rows[0]["Wind"] == "Strong" and "BOSS_TAG=OGRE_KING" in rows[0]["variables"],
              rows[:1])
        vrows = list(csv.DictReader(open(os.path.join(out, "reports", "area_variables.csv"), encoding="utf-8-sig")))
        check("reports/area_variables.csv: one row per area variable", len(vrows) == 3 and vrows[1]["name"] == "BOSS_TAG", vrows)

        # the side panel (/api/node) for each kind of object
        d = D.node_details(a, "area:keep")["details"]
        check("panel, area: event scripts listed (empty slots left out)",
              [(x["event"], x["script"], x["found"]) for x in d["scripts"]] == [("OnEnter", "keep_enter", True)], d["scripts"])
        check("panel, area: variables and properties", len(d["variables"]) == 3 and
              any(p_[1] == "Wind" for p_ in d["props"]), d)
        d = D.node_details(a, "inst:keep:Creature List[0]")["details"]
        ev = {x["event"]: x for x in d["scripts"]}
        check("panel, placed creature: Script* fields named as the toolset does (OnHeartbeat, OnPerception)",
              set(ev) == {"OnHeartbeat", "OnPerception"} and ev["OnPerception"]["field"] == "ScriptOnNotice", d["scripts"])
        check("panel, placed creature: a script that doesn't exist is flagged", ev["OnPerception"]["found"] is False and
              ev["OnHeartbeat"]["found"] is True, ev)
        check("panel, placed creature: its own Variables (not the blueprint's)",
              [(v["name"], str(v["value"])) for v in d["variables"]] == [("POST", "gate"), ("ALERT", "2")], d["variables"])
        check("panel, placed creature: lists shown as counts, plain settings as properties",
              ["Lists", "Inventory items", "2"] in d["props"] and ["Properties", "Tag", "GUARD1"] in d["props"], d["props"])
        d = D.node_details(a, "bp:guard.utc")["details"]
        check("panel, creature blueprint: its scripts and its Variables",
              {x["event"] for x in d["scripts"]} == {"OnHeartbeat", "OnDeath"} and d["variables"][0]["value"] == "wall", d)
        d = D.node_details(a, "inst:keep:Placeable List[0]")["details"]
        check("panel, placeable: OnUsed and the Static setting",
              d["scripts"][0]["event"] == "OnUsed" and ["Properties", "Static", "1"] in d["props"], d)
        d = D.node_details(a, "module")["details"]
        check("panel, module: module events (Mod_ dropped from the name) and module Variables",
              d["scripts"][0]["event"] == "OnModLoad" and d["variables"][0]["name"] == "SERVER_NAME", d)

        it = D.node_details(a, "bp:rune_stone.uti")["details"].get("item_scripts") or {}
        check("panel, item: its tag-based script, with the item events it handles",
              [(x["script"], x["events"]) for x in it.get("tag_based", [])] == [("rune_stone", ["activated (Unique Power)"])], it)
        check("panel, item: the module item event that runs it", {"event": "OnActivateItem", "script": "item_act"} in
              it.get("module_events", []), it.get("module_events"))
        check("panel, item: scripts that name the item's tag, with the line",
              any(l_["node"] == "script:mod_load" and "line 3" in l_["via"] for l_ in it.get("lookups", [])), it.get("lookups"))
        check("panel: no item section for things that aren't items",
              "item_scripts" not in D.node_details(a, "bp:guard.utc")["details"])
        page = open(os.path.join(h.ROOT, "dashboard.html"), encoding="utf-8").read()
        check("page: the item section (tag-based scripts, module events, tag lookups)",
              "Item scripts${it.tag ?" in page and "name this tag</summary>" in page)
        check("page: the side panel shows Scripts, Variables and Properties (objectDetails)",
              "function objectDetails(det)" in page and "H += objectDetails(d.details);" in page)
        check("page: Areas table has Settings and Variables columns and searches variables/settings",
              'label:"Settings"' in page and 'label:"Variables"' in page and "Search areas, variables, scripts, settings" in page)
        check("page: changing page closes the side panel (asking first when an editor holds unsaved text)",
              "if (dr && !dr.hidden && S.view !== v){" in page and "leaveDrawer().then(ok =>" in page)
    finally:
        D.release_analysis(a)              # close the cached index before its folder goes
        shutil.rmtree(out, ignore_errors=True)


def main():
    """Run every group of checks; returns the exit code (tests/_harness.summary)."""
    with h.tempdir() as t:
        h.run(checks, t)
    return h.summary()


if __name__ == "__main__":
    sys.exit(main())
