# -*- coding: utf-8 -*-
"""Analyse the Vordan's Hero Creator pack with this NWN2 fork, then write the HTML report.

Nothing to type: the pack folder is found relative to this file, the .mod inside modules/ is
picked up, and the pack's own hak/ is used automatically.

    python analyse_vhc.py                 analyse the default pack, then export the report
    python analyse_vhc.py "<other pack>"  analyse something else (also accepts a .mod)

Set VHC_PACK to override the default pack folder.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nwn_dashboard   # noqa: E402  (first, so it fixes WORKSPACE to this folder's nwn_workspace)
import nwn_analyse     # noqa: E402
import nwnlib          # noqa: E402

DEFAULT_PACK = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "Vordan's Hero Creator"))


def main(argv):
    pack = argv[0] if argv else os.environ.get("VHC_PACK") or DEFAULT_PACK
    module = nwnlib.resolve_module_path(pack)

    print(f"NWN Module Toolkit {nwn_dashboard.VERSION}")
    print(f"pack   : {pack}")
    print(f"module : {module}")
    if module == pack and not os.path.isfile(os.path.join(pack, "module.ifo")):
        print("\nНе найден .mod внутри папки. Укажите путь к папке-паку или к .mod файлу:")
        print('    python analyse_vhc.py "D:\\путь\\модуль.mod"')
        return 2
    hakdir = nwnlib.guess_user_folder(module)
    print(f"hak/tlk: {hakdir or '(не найдены - хаки будут помечены MISSING)'}")
    print()

    rc = nwn_analyse.main([module])
    if rc not in (0, None):
        print(f"\nАнализ остановился с кодом {rc}.")
        return rc

    name = nwn_dashboard.analysis_name_for(module)
    try:
        nwn_dashboard.main(["--export", name])
    except SystemExit:
        pass
    report = os.path.join(nwn_dashboard.WORKSPACE, f"{name}_report.html")
    print()
    print(f"Готово.")
    print(f"  отчёты : {os.path.join(nwn_dashboard.WORKSPACE, name, 'reports')}")
    print(f"  диалоги: {os.path.join(nwn_dashboard.WORKSPACE, name, 'conversations')}")
    if os.path.isfile(report):
        print(f"  HTML   : {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
