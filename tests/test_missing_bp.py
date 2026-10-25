"""
Tests for "missing blueprint" findings a tester called false positives (1.5.4): items placed in areas, or shown on the
toolset palette, were reported as blueprints that don't exist. The finding stands (the game loads only the saved
module, the haks module.ifo lists and the base game), but the report must say what is going on and where the file is:
  - placed copies of an item are not its blueprint: the detail says copies exist and the fix says Create Blueprint;
  - a blueprint saved only in the toolset's working folder (temp0, module not saved) is named there, with a
    toolset_unsaved warning;
  - one only in a hak module.ifo does not list, or only in the development folder, is named there;
  - one whose name is cut at 16 characters is linked to the almost-identical loaded name.
    python tests/test_missing_bp.py      (last line "N/M checks passed"; exit code 1 on a failure)
Works only in temporary folders and the test workspace (see tests/_harness.py).
"""
import _harness as h  # noqa: F401 - first: isolates the workspace and the compiler before toolkit modules load
import os
import sys
import time

import nwnlib as n  # noqa: E402
from _harness import check  # noqa: E402
from _gff import st, root, loc, item_fields, inst_item, R, X, LST, stand_in_ncs  # noqa: E402


def fixture(tmp):
    """A user folder (modules/, hak/, development/) and a packed module 'Runes' whose script creates six items:
    a_rune (only placed copies in the area), pot_bloodberry (only in the toolset's temp0), huntitem9 (only in an
    unlisted hak), keyofchaos (only in development/), a 17-character name whose 16-character file is in the module,
    and really_gone (nowhere). Returns (module path, user folder)."""
    user = os.path.join(tmp, "user")
    mods, hak, dev = (os.path.join(user, d) for d in ("modules", "hak", "development"))
    temp0 = os.path.join(mods, "temp0")
    for d in (mods, hak, dev, temp0):
        os.makedirs(d)
    gff = lambda r: n.write_gff(r)  # noqa: E731
    ifo = gff(root("IFO ", Mod_Name=loc("Runes"), Mod_Entry_Area=(R, "area001"),
                   Mod_Area_list=(LST, [st(6, Area_Name=(R, "area001"))]), Mod_OnModLoad=(R, "dhd_use"),
                   Mod_HakList=(LST, [])))
    are = gff(root("ARE ", Name=loc("Ruins"), Tag=(X, "RUINS"), ResRef=(R, "area001"), Tileset=(R, "tcn01")))
    git = gff(root("GIT ", **{"List": (LST, [inst_item("a_rune", "A_RUNE", "Arnath Rune"),
                                             inst_item("a_rune", "A_RUNE", "Arnath Rune")])}))
    src = ("void main()\n{\n    object oPC = GetFirstPC();\n" +
           "".join(f'    CreateItemOnObject("{r}", oPC);\n' for r in
                   ("a_rune", "pot_bloodberry", "huntitem9", "keyofchaos", "averylongitemnam", "really_gone")) + "}\n")
    long_item = gff(root("UTI ", **item_fields("averylongitemnam", "LONG", "Long name")))
    files = [("module", "ifo", ifo), ("area001", "are", are), ("area001", "git", git),
             ("dhd_use", "nss", src.encode()), ("dhd_use", "ncs", stand_in_ncs("dhd_use"))]
    # the script names a 17-character resref; the module's file has the 16 characters the game keeps
    files[3] = ("dhd_use", "nss", src.replace('"averylongitemnam"', '"averylongitemname"').encode())
    files.append(("averylongitemnam", "uti", long_item))
    mod = os.path.join(mods, "runes.mod")
    n.write_erf(mod, files, file_type="MOD ")
    # the toolset's working copy: the same module plus a potion made since the last save, newer than the .mod
    with open(os.path.join(temp0, "module.ifo"), "wb") as fh:
        fh.write(ifo)
    with open(os.path.join(temp0, "pot_bloodberry.uti"), "wb") as fh:
        fh.write(gff(root("UTI ", **item_fields("pot_bloodberry", "pot_bloodberry", "Magical Bloodberry"))))
    later = time.time() + 3600
    for fn in os.listdir(temp0):
        os.utime(os.path.join(temp0, fn), (later, later))
    extra = os.path.join(tmp, "extra")
    os.makedirs(extra)
    h.pack_hak(extra, os.path.join(hak, "extra_items.hak"),
               extra=[("huntitem9", "uti", gff(root("UTI ", **item_fields("huntitem9", "HUNT9", "Hunt item"))))])
    with open(os.path.join(dev, "keyofchaos.uti"), "wb") as fh:
        fh.write(gff(root("UTI ", **item_fields("keyofchaos", "KEYOFCHAOS", "Key of Chaos"))))
    return mod, user


def checks(tmp):
    mod, user = fixture(tmp)
    rep = h.analyse(mod, os.path.join(tmp, "an"), nwn_user=user)
    mb = {i["node"]: i for i in rep["issues"] if i["category"] == "missing_blueprint"}
    rune = mb.get("bp:a_rune.uti", {})
    check("placed copies: a_rune is still an error (CreateItemOnObject needs the blueprint)", rune.get("severity") == "error", rune)
    check("placed copies: the detail says 2 placed copies exist but there is no blueprint file",
          "2 placed copies of it exist" in rune.get("detail", "") and "no blueprint file" in rune.get("detail", ""), rune)
    check("placed copies: the fix says Create Blueprint with the same ResRef",
          "Create Blueprint" in rune.get("fix", "") and "'a_rune'" in rune.get("fix", ""), rune.get("fix"))
    pot = mb.get("bp:pot_bloodberry.uti", {})
    check("temp0: a blueprint only in the toolset's working folder is named there",
          "temp0" in pot.get("detail", "") and "File > Save" in pot.get("fix", ""), pot)
    uns = [i for i in rep["issues"] if i["category"] == "toolset_unsaved"]
    check("temp0: newer than the .mod -> one toolset_unsaved warning with a fix",
          len(uns) == 1 and uns[0]["severity"] == "warning" and "File > Save" in uns[0].get("fix", ""), uns)
    hunt = mb.get("bp:huntitem9.uti", {})
    check("unlisted hak: named, with the fix to list it in Module Properties",
          "extra_items.hak" in hunt.get("detail", "") and "Custom Content" in hunt.get("fix", ""), hunt)
    key = mb.get("bp:keyofchaos.uti", {})
    check("development folder: named, players don't have it",
          "development folder" in key.get("detail", "") and "players don't" in key.get("fix", ""), key)
    lng = mb.get("bp:averylongitemname.uti", {})
    check("16 characters: the almost-identical loaded name is pointed out",
          "averylongitemnam.uti" in lng.get("detail", "") and "almost the same name" in lng.get("detail", ""), lng)
    gone = mb.get("bp:really_gone.uti", {})
    check("nowhere: no 'Found elsewhere' and no placed-copies note",
          gone and "Found elsewhere" not in gone["detail"] and "placed cop" not in gone["detail"], gone)
    # temp0 holding ANOTHER module (a different Mod_Name) is not this module's working copy
    with open(os.path.join(user, "modules", "temp0", "module.ifo"), "wb") as fh:
        fh.write(n.write_gff(root("IFO ", Mod_Name=loc("Something else"), Mod_HakList=(LST, []))))
    rep2 = h.analyse(mod, os.path.join(tmp, "an2"), nwn_user=user)
    pot2 = next((i for i in rep2["issues"] if i["node"] == "bp:pot_bloodberry.uti"), {})
    check("temp0 of another module: not used, no unsaved warning",
          "temp0" not in pot2.get("detail", "x temp0") and
          not any(i["category"] == "toolset_unsaved" for i in rep2["issues"]), pot2)


def main():
    """Run every group of checks; returns the exit code (tests/_harness.summary)."""
    with h.tempdir() as t:
        h.run(checks, t)
    return h.summary()


if __name__ == "__main__":
    sys.exit(main())
