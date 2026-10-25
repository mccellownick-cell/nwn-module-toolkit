# -*- coding: utf-8 -*-
"""nwn_assistant.py - an AI assistant for the toolkit dashboard.

What it is
----------
A small agent behind the dashboard's own local server, so the API key never reaches the browser or the exported
(static) report: only the Python process reads it, from

    <toolkit>/nwn_workspace/harness.json

The model answers with one JSON object per turn - either

    {"say": "текст ответа"}                              the answer, done
    {"tool": "read_file", "args": {"path": "..."}}       do something; the result comes back as the next message

Tools: list_dir, read_file, search_text, propose_write, finish. `propose_write` does NOT touch the disk: it records a
pending change that the page shows as a diff with an "Apply" button - that click is the confirmation.

Paths are confined to the configured workspace (the folder that holds the module packs). Everything outside it is
refused, and an applied write keeps a .bak copy next to the original.

The DeepSeek Harness bridge lives here too: the desktop harness (on its own local port) can be probed and started
(`npx @deepseek-ai/dsh web`). It cannot be embedded in the report page - it wants its own access token and refuses
framing - so the page shows its status and opens it in a tab.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.join(HERE, "nwn_workspace")
CONFIG = os.path.join(WORKSPACE_DIR, "harness.json")

DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "models": ["deepseek-flash", "deepseek-v4-pro"],
    # the folder that usually holds the module packs: the one above the toolkit folder
    "workspace": os.path.dirname(os.path.dirname(HERE)),
    "max_rounds": 16,
    "max_file_kb": 60,
    "read_only_roots": [],                      # extra folders it may read (never write)
    "harness_url": "http://127.0.0.1:19387",
    "harness_command": "",                       # the user's own launcher command, kept in harness.json
    "system_extra": "",
}

PENDING = {}                                     # id -> dict(path, before, after, created)

SYSTEM = """Ты ассистент по переводу и починке модулей Neverwinter Nights 2. Модуль уже разобран инструментом:
описание модуля, замечания (Issues), диалоги, скрипты и журнал лежат в базе анализа; файлы модуля - на диске.

Отвечай ОДНИМ JSON-объектом и больше ничем. Два варианта:
  {"tool": "<имя>", "args": { ... }}     - выполнить инструмент, результат придёт следующим сообщением
  {"say": "<ответ>"}                    - закончить и ответить пользователю

Инструменты:
  list_dir(path)                    - список файлов/папок (относительно рабочей папки)
  read_file(path, max_kb)           - прочитать текстовый файл
  search_text(pattern, path, ext)   - регулярный поиск по файлам (ext - например "nss", "dlg", "2da")
  propose_write(path, content)      - ПРЕДЛОЖИТЬ запись файла (не пишет на диск: пользователь увидит diff и нажмёт
                                      «применить»). Всегда пользуйся им вместо прямой записи.
  finish(text)                      - то же, что say

Правила:
* Правь только то, о чём просили; не переписывай файлы целиком, если нужна мелкая правка.
* Для перевода диалогов сохраняй разметку, теги <C=...>, переводы строк \\r\\n и служебные поля; переводи только
  текст, который видит игрок.
* Если для работы нужен файл - сначала read_file, не выдумывай содержимое.
* Не исследуй всю папку: не больше 4 инструментов до первого ответа. Если чего-то нет - так и скажи.
* Отвечать можно на любом шаге: если данных достаточно, сразу say.
* В say пиши по-русски, коротко: что сделал и что осталось. Список предложенных правок перечисляй файлами.
"""


# ---------------------------------------------------------------------------------------------- config

def load():
    """The config, creating it (with empty api_key) on first use. Unknown keys are kept."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                cfg.update(json.load(fh) or {})
        except (OSError, ValueError):
            pass
    else:
        save({})
    return cfg


def save(patch):
    """Merge patch into the config file. The API key is stored as given (it is the one secret here)."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    cfg = dict(DEFAULTS)
    if os.path.isfile(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                cfg.update(json.load(fh) or {})
        except (OSError, ValueError):
            pass
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "max_rounds":
            v = max(1, min(24, int(v or 8)))
        if k == "max_file_kb":
            v = max(4, min(512, int(v or 60)))
        if k in ("api_key", "model", "base_url", "workspace", "harness_url", "harness_command", "system_extra"):
            v = str(v or "").strip()
        cfg[k] = v
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG)
    return cfg


def masked(cfg=None):
    """Config for the page: no key, only whether it is set and its last four characters."""
    cfg = cfg or load()
    key = cfg.get("api_key") or ""
    return dict(api_key_set=bool(key), api_key_hint=(("…" + key[-4:]) if len(key) >= 8 else ""),
                model=cfg.get("model"), models=cfg.get("models") or DEFAULTS["models"],
                base_url=cfg.get("base_url"), workspace=cfg.get("workspace"),
                max_rounds=cfg.get("max_rounds"), harness_url=cfg.get("harness_url"),
                harness_command=cfg.get("harness_command"), config_path=CONFIG)


# ---------------------------------------------------------------------------------------------- http

def _post_json(url, payload, key, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")[:600]
        raise ValueError(f"API {ex.code}: {body}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as ex:
        raise ValueError(f"нет связи с {url}: {ex}") from None


def _get_json(url, key, timeout=30):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")[:400]
        raise ValueError(f"API {ex.code}: {body}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as ex:
        raise ValueError(f"нет связи с {url}: {ex}") from None


def test_key(cfg=None):
    """Ask the API for the model list: proves the key and the network path without spending tokens."""
    cfg = cfg or load()
    key = cfg.get("api_key") or ""
    if not key:
        return dict(ok=False, error="ключ не задан")
    base = (cfg.get("base_url") or DEFAULTS["base_url"]).rstrip("/")
    try:
        r = _get_json(base + "/models", key)
    except ValueError as ex:
        return dict(ok=False, error=str(ex))
    names = [m.get("id") for m in (r.get("data") or []) if isinstance(m, dict)]
    return dict(ok=True, models=names[:40])


# ---------------------------------------------------------------------------------------------- paths

def _root(cfg):
    return os.path.abspath(cfg.get("workspace") or DEFAULTS["workspace"])


def _resolve(cfg, path, write=False):
    """Absolute path inside the workspace, or ValueError. Refuses traversal and anything outside.

    read_only_roots (the game's own modules folder, say) may be read but never written."""
    root = _root(cfg)
    p = str(path or "").strip().strip('"')
    if not p:
        raise ValueError("пустой путь")
    full = os.path.abspath(p if os.path.isabs(p) else os.path.join(root, p))
    try:
        common = os.path.commonpath([os.path.normcase(root), os.path.normcase(full)])
    except ValueError:
        common = ""
    if common == os.path.normcase(root):
        return full
    if not write:
        for extra in (cfg.get("read_only_roots") or []):
            if not extra:
                continue
            try:
                if os.path.normcase(os.path.commonpath(
                        [os.path.normcase(os.path.abspath(extra)), os.path.normcase(full)])) == \
                        os.path.normcase(os.path.abspath(extra)):
                    return full
            except ValueError:
                continue
    raise ValueError(f"вне рабочей папки: {full}" + (" (только чтение)" if not write else ""))


def _rel(cfg, full):
    try:
        return os.path.relpath(full, _root(cfg))
    except ValueError:
        return full


# ---------------------------------------------------------------------------------------------- tools

def _tool_list_dir(cfg, args):
    full = _resolve(cfg, args.get("path") or ".")
    if not os.path.isdir(full):
        return f"не папка: {full}"
    rows = []
    for name in sorted(os.listdir(full))[:400]:
        p = os.path.join(full, name)
        rows.append(("d " if os.path.isdir(p) else "  ") + name + ("" if os.path.isdir(p) else f"  ({os.path.getsize(p)} B)"))
    return "\n".join(rows) or "(пусто)"


def _tool_read_file(cfg, args):
    full = _resolve(cfg, args.get("path"))
    if not os.path.isfile(full):
        return f"нет файла: {full}"
    kb = int(args.get("max_kb") or cfg.get("max_file_kb") or 60)
    data = open(full, "rb").read(kb * 1024 + 1)
    text = data[: kb * 1024].decode("utf-8", "replace")
    if len(data) > kb * 1024:
        text += f"\n… (обрезано на {kb} КБ; файл {_rel(cfg, full)})"
    return text


def _tool_search(cfg, args):
    pat = str(args.get("pattern") or "")
    if not pat:
        return "нужен pattern"
    try:
        rx = re.compile(pat)
    except re.error as ex:
        return f"плохое выражение: {ex}"
    base = _resolve(cfg, args.get("path") or ".")
    ext = str(args.get("ext") or "").lower().lstrip(".")
    limit = int(args.get("limit") or 60)
    hits = []
    for root, _dirs, files in os.walk(base):
        for name in files:
            if ext and not name.lower().endswith("." + ext):
                continue
            p = os.path.join(root, name)
            if os.path.getsize(p) > 2_000_000:
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append(f"{_rel(cfg, p)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= limit:
                                return "\n".join(hits) + f"\n… (обрезано, лимит {limit})"
            except OSError:
                continue
    return "\n".join(hits) or "не найдено"


def _tool_propose_write(cfg, args):
    full = _resolve(cfg, args.get("path"), write=True)
    new = args.get("content")
    if not isinstance(new, str):
        return "нужен content (строка)"
    old = ""
    if os.path.isfile(full):
        old = open(full, "rb").read().decode("utf-8", "replace")
    elif os.path.exists(full):
        return f"это не файл: {full}"
    pid = uuid.uuid4().hex[:12]
    PENDING[pid] = dict(id=pid, path=full, rel=_rel(cfg, full), before=old, after=new, created=time.time())
    return (f"запись предложена (id {pid}) для {_rel(cfg, full)}: было {len(old)} байт, станет {len(new)}. "
            f"Пользователь увидит diff и подтвердит.")


TOOLS = {
    "list_dir": _tool_list_dir,
    "read_file": _tool_read_file,
    "search_text": _tool_search,
    "propose_write": _tool_propose_write,
    "finish": None,
    "say": None,
}


def _clean(text):
    """Tool output for the model: no control characters, no runaway length, no console noise."""
    s = str(text or "")
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return s[:12000]


def _parse_action(text):
    """The first JSON object in the model's reply (it may fence it in ```json)."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    blob = m.group(1) if m else None
    if blob is None:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            blob = text[start:end + 1]
    if not blob:
        return None
    try:
        obj = json.loads(blob)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def chat(messages, context="", cfg=None):
    """Run the tool loop. Returns dict(say, rounds, trace, pending) - pending are ids the page shows."""
    cfg = cfg or load()
    key = cfg.get("api_key") or ""
    if not key:
        raise ValueError("ключ DeepSeek не задан: откройте «Ассистент» и сохраните ключ (nwn_workspace/harness.json)")
    base = (cfg.get("base_url") or DEFAULTS["base_url"]).rstrip("/")
    url = base + "/chat/completions"
    system = SYSTEM + (("\n\n" + cfg["system_extra"]) if cfg.get("system_extra") else "")
    try:                                     # keep every translation consistent with the shared glossary
        import nwn_glossary as _G
        _block = _G.prompt_block()
        if _block:
            system += "\n\n" + _block
    except Exception:                        # noqa - a broken glossary must never stop the assistant
        pass
    if context:
        system += "\n\nКонтекст, который прислала страница:\n" + str(context)[:20000]
    convo = [dict(role="system", content=system)]
    for msg in (messages or [])[-24:]:
        role = msg.get("role")
        if role in ("user", "assistant") and msg.get("content"):
            convo.append(dict(role=role, content=str(msg["content"])[:20000]))
    if not convo[1:]:
        raise ValueError("пустой запрос")

    trace, pending, said = [], [], None
    rounds = max(1, int(cfg.get("max_rounds") or 8))
    for n in range(rounds):
        if n == rounds - 1:
            # last step: no more tools, answer with what is known (a task must never end with "no answer")
            convo.append(dict(role="user", content="Это последний шаг: инструменты недоступны. Ответь сейчас "
                                                   "объектом {\"say\": \"...\"}: что удалось выяснить и что "
                                                   "нужно сделать дальше."))
        reply = _post_json(url, dict(model=cfg.get("model") or DEFAULTS["model"], messages=convo,
                                     temperature=0.2, stream=False), key)
        try:
            text = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ValueError(f"неожиданный ответ API: {str(reply)[:300]}") from None
        action = _parse_action(text)
        if not action:
            said = text.strip()
            break
        if "say" in action or "text" in action or action.get("tool") in (None, "say", "finish"):
            said = str(action.get("say") or action.get("text") or text).strip()
            break
        name = action.get("tool")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        fn = TOOLS.get(name)
        if fn is None:
            result = f"неизвестный инструмент {name!r}; доступны: {', '.join(k for k, v in TOOLS.items() if v)}"
        else:
            try:
                result = fn(cfg, args)
            except ValueError as ex:
                result = f"отказ: {ex}"
            except OSError as ex:
                result = f"ошибка файла: {ex}"
        result = _clean(result)
        trace.append(dict(tool=name, args=args, result=result[:400]))
        convo.append(dict(role="assistant", content=json.dumps(action, ensure_ascii=False)))
        convo.append(dict(role="user", content="результат " + str(name) + ":\n" + str(result)[:12000]))
        if name == "propose_write" and PENDING:
            last = next(reversed(list(PENDING.values())))
            if last["id"] not in [p["id"] for p in pending]:
                pending.append(dict(id=last["id"], path=last["rel"]))
    else:
        done = "; ".join(f"{t['tool']}({', '.join(f'{k}={v!r}' for k, v in list(t['args'].items())[:2])})"
                         for t in trace[:6])
        said = ("Задача не поместилась в отведённые шаги - модель так и не дала ответ. Что успело выполниться: "
                + (done or "ничего") + ". Уточните задачу (одна цель за раз) или увеличьте max_rounds в конфиге.")
    return dict(say=said or "(пустой ответ)", rounds=len(trace), trace=trace, pending=pending)


# ---------------------------------------------------------------------------------------------- pending changes

def pending_list():
    """Every pending change, with a compact unified diff for the page."""
    out = []
    for p in sorted(PENDING.values(), key=lambda x: x["created"], reverse=True):
        out.append(dict(id=p["id"], path=p["rel"], added=max(0, len(p["after"]) - len(p["before"])),
                        diff=diff(p["before"], p["after"])))
    return out


def diff(before, after, limit=400):
    """A small line diff (only the changed blocks, capped) - enough to review a patch."""
    import difflib
    rows = list(difflib.unified_diff(before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=2))
    return rows[:limit]


def apply_pending(pid, discard=False, cfg=None):
    """Write a pending change (with a .bak copy) or drop it. Returns what happened."""
    p = PENDING.get(str(pid))
    if not p:
        raise ValueError("такой правки нет (возможно, страница устарела)")
    if discard:
        PENDING.pop(p["id"], None)
        return dict(ok=True, discarded=True, path=p["rel"])
    cfg = cfg or load()
    full = _resolve(cfg, p["path"], write=True)        # re-check confinement at write time
    os.makedirs(os.path.dirname(full), exist_ok=True)
    backup = ""
    if os.path.isfile(full):
        backup = full + ".bak"
        with open(backup, "wb") as fh:
            fh.write(open(full, "rb").read())
    with open(full, "wb") as fh:
        fh.write(p["after"].encode("utf-8"))
    PENDING.pop(p["id"], None)
    return dict(ok=True, path=p["rel"], bytes=len(p["after"].encode("utf-8")), backup=backup)


# ---------------------------------------------------------------------------------------------- harness bridge

def harness_status(cfg=None):
    """Is the DeepSeek Harness answering on its local port?"""
    cfg = cfg or load()
    url = (cfg.get("harness_url") or DEFAULTS["harness_url"]).rstrip("/")
    u = urllib.parse.urlparse(url)
    host, port = u.hostname or "127.0.0.1", u.port or 80
    alive = False
    try:
        with socket.create_connection((host, port), timeout=2):
            alive = True
    except OSError:
        alive = False
    return dict(url=url, alive=alive, command=cfg.get("harness_command") or DEFAULTS["harness_command"])


def harness_start(cfg=None):
    """Start the harness detached (first run downloads the package with npx). Returns the status after a moment."""
    cfg = cfg or load()
    st = harness_status(cfg)
    if st["alive"]:
        return dict(started=False, note="харнес уже отвечает", **st)
    cmd = cfg.get("harness_command") or DEFAULTS["harness_command"]
    env = dict(os.environ)
    if cfg.get("api_key"):
        env["DEEPSEEK_API_KEY"] = cfg["api_key"]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(cmd, shell=True, env=env, cwd=_root(cfg), creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError as ex:
        return dict(started=False, error=str(ex), **st)
    for _ in range(10):
        time.sleep(1)
        st = harness_status(cfg)
        if st["alive"]:
            break
    return dict(started=True, **st)


# ---------------------------------------------------------------------------------------------- selftest

def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load()
    print("config:", CONFIG)
    print("key set:", bool(cfg.get("api_key")), "| model:", cfg.get("model"), "| workspace:", cfg.get("workspace"))
    if "--test-key" in argv:
        print(json.dumps(test_key(cfg), ensure_ascii=False, indent=1))
    print("harness:", json.dumps(harness_status(cfg), ensure_ascii=False))
    if "--write-config" in argv:
        save({"workspace": cfg.get("workspace") or DEFAULTS["workspace"]})
        print("config written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
