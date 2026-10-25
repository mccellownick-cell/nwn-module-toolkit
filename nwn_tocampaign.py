# -*- coding: utf-8 -*-
"""nwn_tocampaign.py - turn a stand-alone module into a campaign, the way the toolset's Campaign Editor does.

What the toolset does (and this reproduces):
  1. a folder Campaigns/<Name>/ holding Campaign.cam;
  2. Campaign.cam lists the module in ModNames and names it in StartModule, with DisplayName/Description and the
     party options (Cam_PartyCreate, Cam_PartySize, AllowUnrstrctLU, LvlCap, XPCap, JournalSynch);
  3. the campaign gets its own journal: the module's module.jrl is copied there and then emptied in the module;
  4. the module is pointed at the campaign: module.ifo's Campaign_ID = the campaign's GUID;
  5. Campaigns/<Name>/campaigndownloaderresources.xml (empty) and, when a sample is available, resources.dat.

Everything is written with .bak copies; `dry_run=True` returns the plan and touches nothing.
The .mod is rebuilt (same name, same entries, only module.ifo and module.jrl replaced) or, for an unpacked module
folder, its module.IFO and module.JRL are rewritten in place.
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid

import nwnlib as n

META = "Campaigns"

# The 33 fields of a toolset-made Campaign.cam, in its order (types as GFF stores them: 0 BYTE, 5 INT, 8 FLOAT,
# 10 CEXOSTRING, 12 CEXOLOCSTRING, 13 VOID, 15 LIST of struct{ModName}).
CAM_LAYOUT = [
    ("CompXPWt", 8), ("OL_MP_Lock", 5), ("ModNames", 15), ("GUID", 13), ("LoaderTipMax", 5),
    ("DLGPartySwap", 5), ("DisplayName", 12), ("GameStateVarName", 10), ("AutoXPAwd", 5),
    ("NoCharChanging", 5), ("JournalSynch", 5), ("Cam_PartySize", 0), ("StartModule", 10),
    ("UsePersonalRep", 5), ("Cam_PartyCreate", 5), ("PartyMemberDying", 5), ("Description", 12),
    ("NX2ConvoDist", 5), ("LvlCap", 0), ("HenchXPWt", 8), ("AllowUnrstrctLU", 5), ("XPCap", 5),
    ("SuppressRestMsgs", 5), ("AttackNeut", 0),
]


def _cam_root(display, description, start_module, guid, party_create, party_size, lvl_cap, xp_cap, allow_lu):
    root = n.GffRoot("CAM ", "V3.2")
    values = {
        "CompXPWt": 0.0, "OL_MP_Lock": 0, "GUID": guid, "LoaderTipMax": 0, "DLGPartySwap": 1,
        "DisplayName": n.LocString(0xFFFFFFFF, {0: display}), "GameStateVarName": "", "AutoXPAwd": 1,
        "NoCharChanging": 1, "JournalSynch": 1, "Cam_PartySize": int(party_size), "StartModule": start_module,
        "UsePersonalRep": 0, "Cam_PartyCreate": 1 if party_create else 0, "PartyMemberDying": 0,
        "Description": n.LocString(0xFFFFFFFF, {0: description or ""}), "NX2ConvoDist": 0,
        "LvlCap": int(lvl_cap), "HenchXPWt": 0.0, "AllowUnrstrctLU": 1 if allow_lu else 0,
        "XPCap": int(xp_cap), "SuppressRestMsgs": 0, "AttackNeut": 0,
    }
    for label, ftype in CAM_LAYOUT:
        if label == "ModNames":
            continue
        root.set(label, ftype, values[label])
    mods = []
    s = n.GffStruct(0)
    s.set("ModName", n.CEXOSTRING, start_module)
    mods.append(s)
    root.set("ModNames", n.LIST, mods)
    return root


def _ifo_text(root, label, default=""):
    v = root.get(label)
    if isinstance(v, n.LocString):
        return (v.entries.get(0) or "").strip() or default
    return str(v or "").strip() or default


def _read_module(path):
    """(ifo bytes, jrl bytes, ifo GFF, display name, description, journal file name) for a folder or a .mod."""
    if os.path.isdir(path):
        ifo_path = None
        for name in os.listdir(path):
            if name.lower() == "module.ifo":
                ifo_path = os.path.join(path, name)
                break
        if not ifo_path:
            raise ValueError(f"в папке нет module.ifo: {path}")
        jrl_path = os.path.join(path, "module.JRL")
        ifo_bytes = open(ifo_path, "rb").read()
        jrl_bytes = open(jrl_path, "rb").read() if os.path.isfile(jrl_path) else b""
        ifo = n.read_gff(ifo_bytes)
        return dict(kind="folder", ifo_bytes=ifo_bytes, jrl_bytes=jrl_bytes, ifo=ifo, ifo_path=ifo_path,
                    jrl_path=jrl_path)
    erf = n.Erf(path)
    try:
        ifo_e = next((e for e in erf.entries if e.resref.lower() == "module" and e.ext.lower() == "ifo"), None)
        jrl_e = next((e for e in erf.entries if e.resref.lower() == "module" and e.ext.lower() == "jrl"), None)
        if not ifo_e:
            raise ValueError(f"в .mod нет module.ifo: {path}")
        ifo_bytes = erf.read(ifo_e)
        jrl_bytes = erf.read(jrl_e) if jrl_e else b""
    finally:
        erf.close()
    return dict(kind="mod", ifo_bytes=ifo_bytes, jrl_bytes=jrl_bytes, ifo=n.read_gff(ifo_bytes),
                ifo_path=path, jrl_path=path)


def _jrl_categories(jrl_bytes):
    if not jrl_bytes:
        return 0
    try:
        return len(n.read_gff(jrl_bytes).get("Categories") or [])
    except n.GffError:
        return -1


def plan(module_path, campaign_root=None, name=None, party_create=False, party_size=6,
         allow_unrestricted=True, lvl_cap=20, xp_cap=253000):
    """Everything the conversion would do, without touching anything."""
    module_path = os.path.abspath(module_path)
    if not os.path.exists(module_path):
        raise ValueError(f"нет такого модуля: {module_path}")
    info = _read_module(module_path)
    ifo = info["ifo"]
    display = _ifo_text(ifo, "Mod_Name", os.path.splitext(os.path.basename(module_path))[0])
    description = _ifo_text(ifo, "Mod_Description")
    # the engine wants the module's file name (no extension) in StartModule / ModNames
    stem = os.path.splitext(os.path.basename(module_path))[0] if info["kind"] == "mod" else \
        os.path.basename(os.path.normpath(module_path))
    camp_name = (name or display or stem).strip()
    if campaign_root:
        root_dir = os.path.abspath(campaign_root)
    else:
        parent = os.path.dirname(module_path)
        root_dir = os.path.abspath(os.path.join(parent, "..", "Campaigns"))
    guid = uuid.uuid4().bytes
    return dict(module_path=module_path, kind=info["kind"], display=display, description=description,
                start_module=stem, campaign_name=camp_name,
                campaign_dir=os.path.join(root_dir, camp_name), guid=guid.hex(),
                journal_categories=_jrl_categories(info["jrl_bytes"]),
                party_create=party_create, party_size=party_size, allow_unrestricted=allow_unrestricted,
                lvl_cap=lvl_cap, xp_cap=xp_cap, _ifo=ifo, _info=info, _guid=guid)


def public(p):
    """The plan without the pieces JSON cannot carry (the GFF tree, the GUID bytes)."""
    return {k: v for k, v in p.items() if not k.startswith("_")}


def convert(module_path, dry_run=False, **kw):
    """Do the conversion. Returns the same plan plus what was written (or would be, with dry_run)."""
    p = plan(module_path, **kw)
    info, ifo, guid = p["_info"], p["_ifo"], p["_guid"]
    written = []
    if dry_run:
        p["written"] = ["(пробный прогон) " + p["campaign_dir"]]
        p["dry_run"] = True
        return public(p)

    os.makedirs(p["campaign_dir"], exist_ok=True)
    cam = _cam_root(p["display"], p["description"], p["start_module"], guid, p["party_create"],
                    p["party_size"], p["lvl_cap"], p["xp_cap"], p["allow_unrestricted"])
    cam_path = os.path.join(p["campaign_dir"], "Campaign.cam")
    _write(cam_path, n.write_gff(cam))
    written.append(cam_path)

    # the campaign journal: the module's own journal, moved
    jrl_path = os.path.join(p["campaign_dir"], "module.jrl")
    if info["jrl_bytes"]:
        _write(jrl_path, info["jrl_bytes"])
        written.append(jrl_path)
        empty = n.GffRoot("JRL ", "V3.2")
        empty.set("Categories", n.LIST, [])
        empty_bytes = n.write_gff(empty)
    else:
        empty_bytes = b""

    xml_path = os.path.join(p["campaign_dir"], "campaigndownloaderresources.xml")
    _write(xml_path, b'<?xml version="1.0" encoding="utf-8"?>\r\n<content xmlns:xsi="http://www.w3.org/2001/'
                     b'XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" />')
    written.append(xml_path)

    dat_src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "The Sunless Citadel", "Campaigns", "The Sunless Citadel", "resources.dat")
    if os.path.isfile(dat_src):
        dat_dst = os.path.join(p["campaign_dir"], "resources.dat")
        shutil.copy2(dat_src, dat_dst)
        written.append(dat_dst)

    # point the module at the campaign, and empty its journal
    ifo.set("Campaign_ID", n.VOID, guid)
    ifo_bytes = n.write_gff(ifo)
    if p["kind"] == "folder":
        _write(info["ifo_path"], ifo_bytes)
        written.append(info["ifo_path"])
        if empty_bytes:
            _write(info["jrl_path"], empty_bytes)
            written.append(info["jrl_path"])
    else:
        written.append(_rebuild_mod(p["module_path"], ifo_bytes, empty_bytes))
    p["written"] = written
    p["dry_run"] = False
    return public(p)


def _write(path, data):
    """Write bytes, keeping one .bak copy of whatever was there."""
    if os.path.isfile(path):
        bak = path + ".bak"
        if not os.path.isfile(bak):
            shutil.copy2(path, bak)
    with open(path, "wb") as fh:
        fh.write(data)


def _rebuild_mod(mod_path, ifo_bytes, jrl_bytes):
    """A .mod with the same entries, the new module.ifo and an empty module.jrl (written to a temp name first)."""
    erf = n.Erf(mod_path)
    entries = []
    try:
        for e in erf.entries:
            low = (e.resref.lower(), e.ext.lower())
            if low == ("module", "ifo"):
                data = ifo_bytes
            elif low == ("module", "jrl"):
                data = jrl_bytes or erf.read(e)
            else:
                data = erf.read(e)
            entries.append((e.resref, e.ext, len(data), (lambda d=data: d)))
        version = erf.version
    finally:
        erf.close()
    tmp = mod_path + ".new"
    n.write_erf_stream(tmp, entries, "MOD ", version=version)
    bak = mod_path + ".bak"
    if not os.path.isfile(bak):
        shutil.copy2(mod_path, bak)
    os.replace(tmp, mod_path)
    return mod_path


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser(description="convert a module into a campaign")
    ap.add_argument("module")
    ap.add_argument("--root", default=None, help="Campaigns folder (default: next to the module)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--party-create", action="store_true")
    ap.add_argument("--party-size", type=int, default=6)
    ap.add_argument("--no-unrestricted", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    p = convert(a.module, dry_run=not a.apply, campaign_root=a.root, name=a.name,
                party_create=a.party_create, party_size=a.party_size,
                allow_unrestricted=not a.no_unrestricted)
    print(f"module: {p['module_path']} ({p['kind']})")
    print(f"campaign: {p['campaign_name']} -> {p['campaign_dir']}")
    print(f"StartModule: {p['start_module']} | journal categories: {p['journal_categories']}")
    print("written:" if a.apply else "would write:")
    for w in p["written"]:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
