"""Refuse to publish secrets: scans files for tokens, keys and credentials.

Run before a commit:

    python tools/check_secrets.py            # scans the whole toolkit
    python tools/check_secrets.py --staged   # scans only what git has staged

Exit code 1 means something that looks like a secret was found; nothing is printed
that could leak the value itself, only the file, the line and the kind of match.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

SKIP_DIRS = {"nwn_workspace", "wn_workspace", "__pycache__", ".git", ".venv", "venv", "backup_placeholder"}
SKIP_EXT = {".pyc", ".pyo", ".sqlite", ".sqlite-journal", ".log", ".tga", ".dds", ".mdb", ".gr2", ".hak", ".mod", ".erf"}
PATTERNS = [
    ("api-key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}")),
    ("assignment", re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"']{12,}[\"']")),
    ("private-key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
SECRET_NAMES = {"harness.json", ".env", "credentials.json", "secrets.json"}


def walk(root: str):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = os.path.join(base, name)
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            yield path


def staged_files(root: str):
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             cwd=root, capture_output=True, text=True, check=True).stdout
    except Exception:
        return []
    return [os.path.join(root, line.strip()) for line in out.splitlines() if line.strip()]


def scan(paths, root: str):
    findings = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path).lower()
        if name in SECRET_NAMES:
            findings.append((os.path.relpath(path, root), 0, "secret file name"))
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for number, line in enumerate(fh, 1):
                    for kind, pattern in PATTERNS:
                        if pattern.search(line):
                            findings.append((os.path.relpath(path, root), number, kind))
                            break
        except OSError:
            continue
    return findings


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = staged_files(root) if "--staged" in argv else list(walk(root))
    findings = scan(paths, root)
    if not findings:
        print(f"no secrets found ({len(paths)} files scanned)")
        return 0
    print(f"{len(findings)} possible secret(s):")
    for path, line, kind in findings:
        print(f"  {path}:{line}  {kind}")
    print("\nremove the value (or the file) before committing; keys belong in nwn_workspace/harness.json")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
