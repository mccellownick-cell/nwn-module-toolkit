"""
nwn_compile.py - validate scripts with the OFFICIAL NWN:EE script compiler
(nwn_script_comp from neverwinter.nim). Nothing is written into the module:
the compiler runs in simulate mode (-s).

Why this exists
---------------
The toolkit's own checks (nwn_index.validate) find structural problems, but only the real compiler can say whether
a script compiles against the game's nwscript.nss and includes. This module runs that compiler and turns its log
into a per-script list of errors.

Finding the compiler: --compiler (or the Settings path) when given - then only that file; otherwise env
NWN_SCRIPT_COMP, ./tools/, PATH. The file must be executable (nwn_script_comp.exe on Windows).
Finding the game (for nwscript.nss and base-game includes): --nwn-root, env NWN_ROOT,
or the compiler's own auto-detection of your Steam/Beamdog install.

    python nwn_compile.py <module folder or .mod> [--nwn-root PATH] [--compiler PATH]

Entry points
------------
    find_compiler(explicit)            -> path of nwn_script_comp, or None
    compile_module(module_path, ...)   -> check every script of a module (simulate: no .ncs written anywhere)
    compile_files(folder, names, out_dir, ...)  -> really compile some scripts, writing .ncs into out_dir only
                                          (used by the build for edited / new scripts)

Reads and writes
----------------
compile_module reads the module's .nss files: they are copied into a new temporary folder (a .mod is first unpacked
into another temporary folder), the compiler is pointed at those copies, and both folders are deleted afterwards.
It writes nothing else. compile_files compiles the scripts in the folder it is given (the build's own folder) and
writes .ncs files only into its out_dir. The compiler is started directly with an argument list (no shell), so
file names can't be read as shell commands.

Limits
------
Results come from parsing the compiler's text log: an error line whose format the patterns below don't recognise
is not attributed to a script (the log is returned so it can be read).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

import nwnlib as n

HERE = os.path.dirname(os.path.abspath(__file__))
# One error line of the compiler log: "<anything>] <path>.nss: <message>", with an optional timing suffix such
# as " [12.3ms]" or " [<1ms]" that is not part of the message. Only lines that start with "E " (error level) are
# used. Shape: "E [...] foo.nss: <message> [0.4ms]" -> path "foo.nss", msg "<message>". The path may start with a
# Windows drive letter ("C:\...") but otherwise contains no ":" - the first ":" after it ends the path.
LINE_RE = re.compile(r"\]\s+(?P<path>(?:[A-Za-z]:)?[^:]+?\.nss):\s*(?P<msg>.*?)(?:\s+\[[<\d.]+ms\])?$")


def _candidates(explicit):
    """Where the compiler may be, in search order (see find_compiler). An explicit path is the only candidate:
    the caller asked for that program, so another one found elsewhere must not be used in its place."""
    if explicit:
        return [explicit]
    exe = "nwn_script_comp.exe" if os.name == "nt" else "nwn_script_comp"
    return [os.environ.get("NWN_SCRIPT_COMP"), os.path.join(HERE, "tools", exe), shutil.which("nwn_script_comp")]


# --------------------------------------------------------------------------
# NWN2: Skywing's NWNScriptCompiler
# --------------------------------------------------------------------------
# NWN2 has no nwn_script_comp. Its compiler is NWNScriptCompiler.exe with a different command line and log:
#     NWNScriptCompiler -i <includes> [-i ...] -n <installdir> -h <homedir> -x E <in.nss> <out.ncs>
#     log: "script.nss(12): E: NSC1020: message"
# and, unlike nwn_script_comp, batch mode (-b) ABORTS at the first script that fails, so a "check everything" run
# compiles one script per process. NWN2 keeps nwscript.nss and the base-game includes inside Data\Scripts*.zip,
# not in loose files, so they are extracted once into a cache folder and passed with -i.
NWN2_EXE_NAMES = ("NWNScriptCompiler.exe", "nwn2scriptcompiler.exe")
NWN2_LINE_RE = re.compile(r"^(?P<path>.+?\.nss?)\((?P<line>\d+)\):\s*(?P<kind>[EW]):\s*(?P<msg>.*)$", re.I)
BASE_SCRIPT_ZIPS = ("Scripts.zip", "scripts_X1.zip", "scripts_X2.zip")


def _mkdtemp(prefix):
    """A temporary folder for one compile run, created inside the toolkit's own workspace.

    Uses os.makedirs with a unique name rather than tempfile.mkdtemp: mkdtemp creates the folder mode 0700, and on a
    locked-down Windows machine such a folder can end up unwritable even for its owner, which would break every
    compile run. Keeping the copies next to the analysis also makes a failed run easy to inspect."""
    import uuid
    try:
        import nwn_update
        workspace = nwn_update.workspace_dir(HERE)
    except Exception:  # noqa
        workspace = os.path.join(HERE, "nwn_workspace")
    for root in (os.path.join(workspace, "_tmp"), tempfile.gettempdir()):
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            continue
        for _ in range(50):
            d = os.path.join(root, prefix + uuid.uuid4().hex[:12])
            try:
                os.makedirs(d)
                return d
            except FileExistsError:
                continue
            except OSError:
                break
    return tempfile.mkdtemp(prefix=prefix)


def is_nwn2_compiler(comp):
    """True for a NWN2 compiler (detected by its file name), False for nwn_script_comp / nwnsc."""
    name = os.path.basename(comp or "").lower()
    return name.startswith(("nwnscriptcompiler", "nwn2scriptcompiler"))


def _nwn2_candidates(explicit):
    """Where the NWN2 compiler may be: an explicit path, env NWN2_SCRIPT_COMP, the toolkit's tools/ folder, PATH,
    and next to the game itself (the install ships nwn2scriptcompiler.exe)."""
    if explicit:
        return [explicit]
    out = [os.environ.get("NWN2_SCRIPT_COMP")]
    for nm in NWN2_EXE_NAMES:
        out.append(os.path.join(HERE, "tools", nm))
        out.append(shutil.which(os.path.splitext(nm)[0]))
    for root in (os.environ.get("NWN_ROOT"), os.environ.get("NWN2_ROOT")):
        if root:
            out += [os.path.join(root, nm) for nm in NWN2_EXE_NAMES]
    return out


def module_is_nwn2(module_path):
    """True when the module is an NWN2 archive (ERF V1.1). A folder module can't be told apart by its archive, so it
    counts as NWN1 unless an NWN2 compiler was chosen explicitly - pass --compiler then."""
    try:
        if os.path.isdir(module_path):
            return False
        with open(module_path, "rb") as fh:
            return fh.read(8)[4:8] == b"V1.1"
    except OSError:
        return False


def nwn2_base_includes(nwn_root):
    """Folder holding nwscript.nss and every base-game include, extracted from the install's Data\\Scripts*.zip.

    Cached in the toolkit's workspace (_compiler_includes) and re-extracted only when a source zip's size or mtime
    changes. Returns None when no install (or no such archive) is given. Read-only outside the cache folder."""
    # No install given: reuse whatever an earlier run extracted (the caller may rely on the saved settings only)
    cache_probe = None
    try:
        import nwn_update
        cache_probe = os.path.join(nwn_update.workspace_dir(HERE), "_compiler_includes")
    except Exception:  # noqa
        cache_probe = os.path.join(HERE, "nwn_workspace", "_compiler_includes")
    if not nwn_root:
        return cache_probe if os.path.isfile(os.path.join(cache_probe, "nwscript.nss")) else None
    data = None
    for sub in ("Data", "data"):
        cand = os.path.join(nwn_root, sub)
        if os.path.isdir(cand):
            data = cand
            break
    if not data:
        return None
    zips = [os.path.join(data, z) for z in BASE_SCRIPT_ZIPS if os.path.isfile(os.path.join(data, z))]
    if not zips:
        # any scripts archive the install happens to use
        zips = [os.path.join(data, fn) for fn in sorted(os.listdir(data)) if fn.lower().startswith("script")]
    if not zips:
        return None
    try:
        import nwn_update
        workspace = nwn_update.workspace_dir(HERE)
    except Exception:  # noqa
        workspace = os.path.join(HERE, "nwn_workspace")
    cache = os.path.join(workspace, "_compiler_includes")
    stamp = os.path.join(cache, ".stamp")
    try:
        sig = "|".join(f"{os.path.basename(z)}:{os.path.getmtime(z)}:{os.path.getsize(z)}" for z in zips)
    except OSError:
        return None
    try:
        with open(stamp, encoding="utf-8") as fh:
            if fh.read() == sig and os.path.isfile(os.path.join(cache, "nwscript.nss")):
                return cache
    except OSError:
        pass
    import zipfile
    try:
        os.makedirs(cache, exist_ok=True)
        for z in zips:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".nss"):
                        continue
                    name = info.filename.rsplit("/", 1)[-1]
                    if not name:
                        continue
                    with zf.open(info) as src, open(os.path.join(cache, name.lower()), "wb") as dst:
                        dst.write(src.read())
        with open(stamp, "w", encoding="utf-8") as fh:
            fh.write(sig)
    except (OSError, zipfile.BadZipFile):
        return cache if os.path.isfile(os.path.join(cache, "nwscript.nss")) else None
    return cache


def _nwn2_run(comp, src_file, out_file, nwn_root, nwn_user, extra_dirs, timeout):
    """One compiler process for one script. Returns (returncode, log)."""
    cmd = [comp]
    inc = nwn2_base_includes(nwn_root)
    dirs = ([inc] if inc else []) + [os.path.dirname(src_file)] + [d for d in extra_dirs if d]
    if dirs:
        cmd += ["-i", ";".join(dirs)]
    root = nwn_root or os.environ.get("NWN_ROOT")
    if root:
        cmd += ["-n", root]
    user = nwn_user or os.environ.get("NWN_HOME") or os.environ.get("NWN_USER_DIRECTORY")
    if user:
        cmd += ["-h", user]
    cmd += ["-x", "E", src_file]
    if out_file:
        cmd.append(out_file)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -9, f"{os.path.basename(src_file)}: compiler timed out after {timeout}s"
    except OSError as ex:
        return -1, f"{os.path.basename(src_file)}: {ex}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _rc_signed(rc):
    """A Windows process exit code as a signed number (4294967295 -> -1)."""
    return rc - 2 ** 32 if rc and rc > 2 ** 31 - 1 else rc


def _nwn2_errors(log, script):
    """Error messages for `script` from one NWN2 compiler log (warnings are not failures).

    A script can also fail because of an #include it pulls in, and the compiler names that include, not the script.
    Those errors are reported too, prefixed with the include's name - otherwise the script would look like it failed
    for no reason."""
    own, others = [], {}
    for line in log.splitlines():
        m = NWN2_LINE_RE.match(line.strip())
        if not m or m.group("kind").upper() != "E":
            continue
        name = os.path.basename(m.group("path")).lower()[:-4]
        msg = f"line {m.group('line')}: {m.group('msg').strip()}"
        if name == script:
            own.append(msg)
        else:
            others.setdefault(name, []).append(msg)
    if own:
        return own
    if others:
        out = []
        for name, msgs in list(others.items())[:3]:
            out.append(f"fails inside include {name}.nss - {msgs[0]}")
            out.extend(msgs[1:3])
        if len(others) > 3:
            out.append(f"… and {len(others) - 3} more include file(s) with errors")
        return out
    if "Failed to initialize compiler" in log:
        return ["compiler could not start (nwscript.nss / install folder not found)"]
    if "Unable to load" in log:
        return [log.strip().splitlines()[-1][:200] if log.strip() else "compiler could not load a required file"]
    return []


def find_compiler(explicit=None, nwn2=None):
    """Path of the script compiler, or None. explicit: a path from the settings or the command line - when given,
    only that file is accepted (None if it is missing). nwn2: which family to look for - True (NWN2, Skywing's
    NWNScriptCompiler / the game's nwn2scriptcompiler.exe), False (nwn_script_comp) or None (auto: an explicit path
    decides by its name, otherwise nwn_script_comp is preferred). The file must exist and be executable; it is never
    run here."""
    if nwn2 is None:
        nwn2 = is_nwn2_compiler(explicit) if explicit else False
    for c in (_nwn2_candidates(explicit) if nwn2 else _candidates(explicit)):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _run_hint(comp):
    """How to let a compiler file run: the execute bit can be lost when a zip is unpacked, and macOS blocks a
    downloaded program (the com.apple.quarantine attribute) until it is cleared."""
    return f'chmod +x "{comp}" (on macOS also: xattr -d com.apple.quarantine "{comp}")'


def not_found_reason(explicit=None, nwn2=False):
    """One plain sentence on why find_compiler(explicit) found nothing: the file named is missing, a file was found
    but cannot be run (no execute permission), or none was found at all."""
    for c in (_nwn2_candidates(explicit) if (nwn2 or is_nwn2_compiler(explicit)) else _candidates(explicit)):
        if c and os.path.isfile(c):
            return f"{c} is not executable - run: {_run_hint(c)}"
    if explicit:
        return f"the compiler set in Settings does not exist: {explicit}"
    if nwn2:
        return ("NWN2 script compiler not found. Put NWNScriptCompiler.exe (Skywing's standalone compiler) in the "
                "toolkit's tools/ folder, set NWN2_SCRIPT_COMP, or pass --compiler. The game's own "
                "nwn2scriptcompiler.exe in the NWN2 install folder is used when NWN_ROOT is set.")
    return ("Official compiler (nwn_script_comp) not found. Download neverwinter.nim from GitHub releases and put "
            "nwn_script_comp (nwn_script_comp.exe on Windows) in the toolkit's tools/ folder.")


def _died_reason(comp, returncode, log):
    """The reason text for a compiler run that ended with a non-zero exit code and no error line the parser
    recognised: a crash, a missing library, or (macOS) a program blocked because it was downloaded."""
    last = (log.strip().splitlines() or [""])[-1][:300]
    how = f"was killed by signal {-returncode}" if returncode < 0 else f"exited with code {returncode}"
    return (f"the compiler {how} without reporting a script error" + (f" ({last})" if last else ", printing nothing")
            + f". If it never runs, allow it to: {_run_hint(comp)}")


def compile_module(module_path, compiler=None, nwn_root=None, extra_dirs=(), timeout=1800, nwn_user=None):
    """Returns dict(status, compiler, results{script: [errors]}, ok, failed, log).

    module_path: module folder or .mod. compiler / nwn_root / nwn_user: as for find_compiler and the module
    docstring (env NWN_ROOT, NWN_HOME / NWN_USER_DIRECTORY when not given). extra_dirs: more include folders
    (e.g. extracted haks). timeout: seconds for the whole compiler run.
    status: "ran", "skipped" (no compiler; see reason) or "error" (the module could not be opened, the compiler
    could not run, or it exited with a failure code without naming a script error; see reason). results maps every
    script name (lower case) to its error messages, [] when it compiled. ok / failed: counts. log: the last
    20,000 characters of the compiler output. Never writes into the module and never writes .ncs files (-s).
    In a folder module, links and hidden folders are skipped (nwnlib.walk_folder), as the index skips them."""
    # A pack folder (modules/<name>.mod plus hak/ and tlk/) is accepted here too, and resolving it first matters:
    # otherwise the folder itself would be walked and its own loose .nss files (and anything else lying around)
    # compiled instead of the module's scripts.
    module_path = n.resolve_module_path(module_path)
    comp = find_compiler(compiler, nwn2=True if module_is_nwn2(module_path) else None)
    if not comp:
        return dict(status="skipped", reason=not_found_reason(compiler, module_is_nwn2(module_path)), results={},
                    ok=0, failed=0, log="")
    if is_nwn2_compiler(comp):
        return _compile_module_nwn2(module_path, comp, nwn_root, extra_dirs, timeout, nwn_user)
    tmp = work = None
    src = module_path
    # one try/finally around everything that uses the temporary folders: they are removed whatever fails
    # (a module that can't be opened, a copy error, a timeout or a crash of the compiler)
    try:
        if not os.path.isdir(module_path):  # .mod/.erf: extract scripts to a temp folder
            tmp = _mkdtemp("nwn_comp_")
            try:
                erf = n.Erf(module_path)
            except (OSError, ValueError) as ex:
                return dict(status="error", reason=f"module could not be opened: {ex}", results={}, ok=0, failed=0, log="")
            try:
                for e in erf.entries:
                    if e.ext == "nss":
                        # e.filename is safe as a file name: nwnlib.Erf renames entries with path characters or
                        # reserved device names, so nothing can be written outside tmp
                        with open(os.path.join(tmp, e.filename), "wb") as fh:
                            fh.write(erf.read(e))
            finally:
                erf.close()
            src = tmp
        # the compiler gets its own copy of the scripts, so even a misbehaving compiler can't touch the module
        # folder. File names are lower-cased in the copy (the toolkit uses lower-case resrefs throughout).
        # walk_folder skips links and hidden folders, as the index does: a linked file is not part of the module.
        work = _mkdtemp("nwn_comp_src_")
        names = []
        for full, _rel in n.walk_folder(src):
            fn = os.path.basename(full)
            # nwscript.nss is the engine's list of functions and constants, not a script: it is not compiled
            # as one, and the compiler takes it from the game install
            if fn.lower().endswith(".nss") and fn.lower() != "nwscript.nss":
                shutil.copy2(full, os.path.join(work, fn.lower()))
                names.append(fn.lower()[:-4])
        # -c compile, -y keep going after a script fails (so every error is reported), -s simulate: write no .ncs.
        # Argument list, no shell: paths with spaces or odd characters are passed as they are.
        cmd = [comp, "-c", "-y", "-s", "--quiet"]
        root = nwn_root or os.environ.get("NWN_ROOT")
        if root:
            cmd += ["--root", root]
        user = nwn_user or os.environ.get("NWN_HOME") or os.environ.get("NWN_USER_DIRECTORY")
        if user:
            cmd += ["--userdirectory", user]
        # --dirs: the folders searched for #include files (the copies and extra_dirs); the last argument is the
        # folder of scripts to compile
        cmd += ["--dirs", ",".join([work] + list(extra_dirs)), work]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            log = (p.stdout or "") + (p.stderr or "")
        except PermissionError as ex:
            return dict(status="error", reason=f"{ex} - allow the compiler to run: {_run_hint(comp)}", results={},
                        ok=0, failed=0, log="")
        except Exception as ex:  # noqa
            return dict(status="error", reason=str(ex), results={}, ok=0, failed=0, log="")
    finally:
        for d in (work, tmp):
            if d:
                shutil.rmtree(d, ignore_errors=True)
    results = {nm: [] for nm in names}     # every script starts as "compiled OK"; error lines add messages
    fatal = None
    for line in log.splitlines():
        m = LINE_RE.search(line)
        if m and line.startswith("E "):
            script = os.path.basename(m.group("path"))[:-4].lower()
            msg = m.group("msg").strip() or "could not be compiled (invalid name?)"
            results.setdefault(script, []).append(msg)
        # these mean the compiler itself stopped (typically: game install not found), not that a script failed
        if "unhandled exception" in line or "not found [AssertionDefect]" in line:
            fatal = line.strip()
    if fatal:
        return dict(status="error", reason="Compiler could not start - usually the NWN install was not found. "
                    "Pass --nwn-root (the folder containing data/ and lang/). Detail: " + fatal,
                    results={}, ok=0, failed=0, log=log)
    failed = sum(1 for v in results.values() if v)
    # The compiler exits 1 when a script failed, and also when it could not run at all. Without an error line to
    # explain a non-zero exit, "every script compiled" would be a false all-clear.
    if p.returncode != 0 and not failed:
        return dict(status="error", reason=_died_reason(comp, p.returncode, log), results={}, ok=0, failed=0,
                    log=log[-20000:])
    return dict(status="ran", compiler=comp, results=results, ok=len(results) - failed, failed=failed, log=log[-20000:])


def _compile_module_nwn2(module_path, comp, nwn_root, extra_dirs, timeout, nwn_user):
    """compile_module for NWN2: one compiler process per script.

    NWNScriptCompiler's batch mode stops at the first script that fails ("Processing aborted"), which would leave
    every later script unexamined, so each script is compiled on its own into a temporary folder that is discarded -
    the module is still never written to."""
    tmp = work = outdir = None
    src = module_path
    try:
        if not os.path.isdir(module_path):
            tmp = _mkdtemp("nwn2_comp_")
            try:
                erf = n.Erf(module_path)
            except (OSError, ValueError) as ex:
                return dict(status="error", reason=f"module could not be opened: {ex}", results={}, ok=0, failed=0,
                            log="")
            try:
                for e in erf.entries:
                    if e.ext == "nss":
                        with open(os.path.join(tmp, e.filename), "wb") as fh:
                            fh.write(erf.read(e))
            finally:
                erf.close()
            src = tmp
        work = _mkdtemp("nwn2_src_")
        outdir = _mkdtemp("nwn2_ncs_")
        names = []
        for full, _rel in n.walk_folder(src):
            fn = os.path.basename(full)
            if fn.lower().endswith(".nss") and fn.lower() != "nwscript.nss":
                shutil.copy2(full, os.path.join(work, fn.lower()))
                names.append(fn.lower()[:-4])
        per_script = max(20, min(180, int(timeout // max(1, len(names)))))
        results, log_tail = {}, []
        for nm in names:
            rc, log = _nwn2_run(comp, os.path.join(work, nm + ".nss"), os.path.join(outdir, nm + ".ncs"),
                                nwn_root, nwn_user, list(extra_dirs), per_script)
            errs = _nwn2_errors(log, nm)
            if rc != 0 and not errs:
                errs = [f"the compiler exited with code {_rc_signed(rc)} without a recognised error line"]
            results[nm] = errs
            if errs:
                log_tail.extend(log.strip().splitlines()[:40])
        failed = sum(1 for v in results.values() if v)
        return dict(status="ran", compiler=comp, results=results, ok=len(results) - failed, failed=failed,
                    log="\n".join(log_tail)[-20000:])
    finally:
        for d in (work, outdir, tmp):
            if d:
                shutil.rmtree(d, ignore_errors=True)


def _compile_files_nwn2(folder, names, out_dir, comp, nwn_root, nwn_user, extra_dirs, timeout):
    """compile_files for NWN2: compile one script at a time, writing <name>.ncs into out_dir."""
    on_disk = {f.lower(): f for f in os.listdir(folder) if f.lower().endswith(".nss")}
    files = [(nm, os.path.join(folder, on_disk[f"{nm.lower()}.nss"])) for nm in names
             if f"{nm.lower()}.nss" in on_disk]
    missing = [nm for nm in names if f"{nm.lower()}.nss" not in on_disk]
    if not files:
        return dict(status="ran", results={}, written=[], missing=missing)
    per_script = max(20, min(180, int(timeout // max(1, len(files)))))
    results, written, log_tail = {}, [], []
    for nm, path in files:
        target = os.path.join(out_dir, f"{nm.lower()}.ncs")
        before = os.path.getmtime(target) if os.path.isfile(target) else None
        rc, log = _nwn2_run(comp, path, target, nwn_root, nwn_user, list(extra_dirs), per_script)
        errs = _nwn2_errors(log, nm.lower())
        if rc != 0 and not errs:
            errs = [f"the compiler exited with code {_rc_signed(rc)} without a recognised error line"]
        results[nm.lower()] = errs
        if os.path.isfile(target) and os.path.getmtime(target) != before and not errs:
            written.append(nm.lower())
        if errs:
            log_tail.extend(log.strip().splitlines()[:40])
    return dict(status="ran", results=results, written=written, missing=missing,
                log="\n".join(log_tail)[-5000:])


def compile_files(folder, names, out_dir, compiler=None, nwn_root=None, nwn_user=None, extra_dirs=(), timeout=600):
    """Really compile the named scripts (found in folder, includes resolved from folder + extra_dirs),
    writing .ncs files into out_dir. Returns dict(status, results{name: [errors]}, written[list], missing[list]).

    names: script names without .nss, matched to the files in folder without regard to case (a folder module may
    hold "MyScript.nss" on a case-sensitive file system; resrefs are lower case). missing: names with no file in
    folder - not compiled. written: names whose out_dir/<name>.ncs this run wrote (new, or changed since before the
    run) and that reported no error - a .ncs left there by an earlier run never counts. status: "ran", "skipped"
    (no compiler) or "error" (see reason). Writes only into out_dir (the compiler's -d; a .ncs the compiler named
    in another case is renamed to <name>.ncs there); folder is read, never changed. Scripts are passed in batches
    so the command line stays under the Windows length limit."""
    comp = find_compiler(compiler, nwn2=True if is_nwn2_compiler(compiler or "") else None)
    if not comp:
        return dict(status="skipped", reason=not_found_reason(compiler, is_nwn2_compiler(compiler or "")),
                    results={}, written=[], missing=list(names))
    if is_nwn2_compiler(comp):
        return _compile_files_nwn2(folder, names, out_dir, comp, nwn_root, nwn_user, extra_dirs, timeout)
    on_disk = {f.lower(): f for f in os.listdir(folder) if f.lower().endswith(".nss")}
    files = [os.path.join(folder, on_disk[f"{nm.lower()}.nss"]) for nm in names if f"{nm.lower()}.nss" in on_disk]
    missing = [nm for nm in names if f"{nm.lower()}.nss" not in on_disk]
    if not files:
        return dict(status="ran", results={}, written=[], missing=missing)
    cmd = [comp, "-c", "-y", "--quiet", "-d", out_dir]      # no -s: real compile, .ncs go to out_dir (-d)
    root = nwn_root or os.environ.get("NWN_ROOT")
    if root:
        cmd += ["--root", root]
    user = nwn_user or os.environ.get("NWN_HOME") or os.environ.get("NWN_USER_DIRECTORY")
    if user:
        cmd += ["--userdirectory", user]
    cmd += ["--dirs", ",".join([folder] + list(extra_dirs))]
    # batches: Windows refuses command lines over ~32,000 characters (WinError 206)
    batches, cur, size = [], [], sum(len(c) + 3 for c in cmd)
    for f in files:
        if cur and size + len(f) + 3 > 24000:
            batches.append(cur); cur, size = [], sum(len(c) + 3 for c in cmd)
        cur.append(f); size += len(f) + 3
    batches.append(cur)

    def ncs_files():
        """out_dir's .ncs files by lower-case name -> (actual file name, (mtime_ns, size))."""
        out = {}
        for f in os.listdir(out_dir):
            if f.lower().endswith(".ncs"):
                st_ = os.stat(os.path.join(out_dir, f))
                out[f.lower()[:-4]] = (f, (st_.st_mtime_ns, st_.st_size))
        return out
    # what out_dir held before the run: a .ncs that is still the same file afterwards was not written by this run
    before = {k: v[1] for k, v in ncs_files().items()}
    log, codes = "", []
    try:
        for b in batches:
            p = subprocess.run(cmd + b, capture_output=True, text=True, timeout=timeout)
            log += (p.stdout or "") + (p.stderr or "")
            codes.append(p.returncode)
    except PermissionError as ex:
        return dict(status="error", reason=f"{ex} - allow the compiler to run: {_run_hint(comp)}", results={},
                    written=[], missing=missing)
    except Exception as ex:  # noqa
        return dict(status="error", reason=str(ex), results={}, written=[], missing=missing)
    results = {nm.lower(): [] for nm in names if nm not in missing}
    for line in log.splitlines():
        m = LINE_RE.search(line)
        if m and line.startswith("E "):
            results.setdefault(os.path.basename(m.group("path"))[:-4].lower(), []).append(m.group("msg").strip())
    if "unhandled exception" in log:
        return dict(status="error", reason=log.strip().splitlines()[-1][:300], results={}, written=[], missing=missing)
    bad_exit = next((c for c in codes if c != 0), 0)
    if bad_exit and not any(results.values()):
        # same rule as compile_module: a failed run with no error line is not "everything compiled"
        return dict(status="error", reason=_died_reason(comp, bad_exit, log), results={}, written=[],
                    missing=missing, log=log[-5000:])
    written = []
    after = ncs_files()
    for nm in results:
        if results[nm] or nm not in after:
            continue
        actual, stamp = after[nm]
        if stamp != before.get(nm):
            if actual != f"{nm}.ncs":
                os.replace(os.path.join(out_dir, actual), os.path.join(out_dir, f"{nm}.ncs"))
            written.append(nm)
    return dict(status="ran", results=results, written=written, missing=missing, log=log[-5000:])


def main(argv=None):
    """Command line: check every script of a module. Exit code 0 = all compiled, 1 = some failed, 2 = could not
    run (no compiler, or the compiler could not start)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", help="module folder or .mod (only read)")
    ap.add_argument("--compiler", help="path of nwn_script_comp (default: env NWN_SCRIPT_COMP, the tools/ folder, PATH)")
    ap.add_argument("--nwn-root", help="NWN install folder (contains data/ and lang/)")
    ap.add_argument("--nwn-user", help="NWN user folder (Documents/Neverwinter Nights)")
    ap.add_argument("--dirs", action="append", default=[], help="extra include folders (e.g. extracted haks)")
    a = ap.parse_args(argv)
    if not (a.nwn_root and a.nwn_user):
        # the folders saved in the dashboard's Settings fill in whatever the command line leaves out
        try:
            import nwn_dashboard
            st = nwn_dashboard.settings()
        except Exception:  # noqa
            st = {}
        a.nwn_root = a.nwn_root or st.get("nwn_root")
        a.nwn_user = a.nwn_user or st.get("nwn_user")
    r = compile_module(a.module, a.compiler, a.nwn_root, a.dirs, nwn_user=a.nwn_user)
    if r["status"] != "ran":
        print(r["status"].upper() + ": " + r.get("reason", ""))
        return 2
    for s, errs in sorted(r["results"].items()):
        for e in errs:
            print(f"FAIL {s}: {e}")
    print(f"{r['ok']} compiled OK, {r['failed']} failed")
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
