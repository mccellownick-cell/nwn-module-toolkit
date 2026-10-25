# NWN2 Module Toolkit

**English** | [Русский](README.md)
A local, offline toolkit for **Neverwinter Nights 2** modules: it indexes a module,
analyses it, and gives you a browser dashboard for everything that is tedious by hand -
references, haks, 2DA tables, localisation, campaigns and world maps.

Everything runs on your machine. No module data is uploaded anywhere.

---

## What it does

* **Analysis** - indexes every resource in a module (or a whole campaign) and reports real
  problems: missing scripts and conversations, broken strrefs, blueprints that are
  referenced but absent, stale 2DA copies, unsafe names, duplicate assets.
* **Dashboard** - one local web page (`http://127.0.0.1:8765`) with a tab per task area,
  built from the analysis of the module you pick.
* **Haks** - list, extract, slim and rebuild `.hak` files; compare copies; find duplicates.
* **2DA editor** - read, edit and validate 2DA tables, compare them with the installed game.
* **Localisation** - measure how much text a module carries and how much of it is already
  translated (journal, dialogues, script strings), plus a glossary checker for terms,
  markup, placeholders and gender forms.
* **Module to campaign** - performs the Campaign Editor steps: creates `Campaigns/<name>/`,
  moves the journal there, points `module.ifo` at the campaign, with `.bak` copies.
* **World map** - preview and edit `.wmp` files: points, icons, sizes, coordinates.
* **Assistant** (optional) - a chat panel that can read a local analysis and propose edits.

---

## Requirements

* Windows (the paths and the dashboard starter assume it), Python 3.10+
* No third-party Python packages: the toolkit ships pure standard library code
* Optional: an NWN2 installation, for talk tables and base-game comparison
* Optional: a DeepSeek API key, only if you want the in-page assistant

---

## Quick start

```
git clone <this repository>
cd nwn-module-toolkit-1.5.3-nwn2

python nwn_analyse.py "D:\Neverwinter Nights 2\modules\MyModule.mod"
python run_dashboard.py
```

Then open `http://127.0.0.1:8765`.

A whole campaign works too - point the analyser at the campaign folder:

```
python nwn_analyse.py "D:\Neverwinter Nights 2\Campaigns\MyCampaign"
```

Results land in `nwn_workspace/<name>/` (index, report.json, CSV reports, conversations).
The dashboard reads exactly those files.

---

## The dashboard tabs

| Tab | What you get |
|---|---|
| Analysis | summary, issues by severity, files, areas, creatures |
| Details | per-object reports and CSV exports |
| Haks | hak inventory, extraction, slimming |
| 2DA | table editor and game comparison |
| Localisation | translation coverage and the glossary checker |
| Map | world map preview and point editing |
| Blueprints | palette browser and duplicate finder |
| Build | rebuild a module from a folder |
| Assistant | optional chat, see below |
| Settings | paths, external tools, report options |

---

## Assistant (bring your own key)

The assistant is optional and **off by default**. If you want it, put your key in
`nwn_workspace/harness.json`:

```json
{ "api_key": "<your key>" }
```

Notes:

* the key is read only by the local server; it is never sent to the browser and never
  written into an exported report;
* `harness.json` is in `.gitignore` - do not commit it;
* without a key the rest of the toolkit works normally.

To use the regular DeepSeek chat instead, the Assistant tab links to
<https://chat.deepseek.com/> and opens it in a new tab - the site cannot be embedded in a
frame, so a link is the honest way to offer it.

---

## Data and privacy

* `nwn_workspace/` holds analysis output and is **ignored by git** - it can contain file
  names and text from modules you do not own.
* Nothing leaves your machine except the optional assistant request, which contains only
  what you type plus a short summary of the open analysis.
* No API keys, no tokens, no credentials are stored in this repository.

---

## Testing

```
python tests/run_all.py
```

The suite covers the library, the analyser, the 2DA and hak tooling, the map reader and the
dashboard: 20 suites, 1300+ checks.

---

## Contributing

* keep the standard library only;
* run `python tests/run_all.py` before a pull request;
* never commit keys - check with
  `git diff --cached --name-only` and search the staged files for `sk-`, `api_key`, `token`.

---

## License

See the license file shipped with the toolkit.