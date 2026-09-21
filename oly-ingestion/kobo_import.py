# kobo_import.py
"""
Kobo purchase → clean EPUB in sources/ (docs/KOBO-IMPORT.md).

Kobo Desktop no longer keeps a decryptable local copy (it is a web-reader
shell), Adobe Digital Editions is end-of-life, and Kobo's "Download" button
hands out Adobe-DRM `URLLink*.acsm` licence files. The only working route
(2026-09) is Calibre with two plugins:

  * DeACSM  (Leseratte10/acsm-calibre-plugin) — fulfils the .acsm: talks to the
    Adobe licence server with an emulated ADE identity and downloads the EPUB.
  * DeDRM   (noDRM/DeDRM_tools) — strips the Adobe DRM on import, using the key
    DeACSM created.

This script drives every step that can be scripted, through Calibre's own
CLIs (`calibre-customize`, `calibre-debug`, `calibredb`); it never touches the
DRM code itself. The DeDRM plugin install is left to the user (see `check`).

    uv run python kobo_import.py check                 # Calibre + plugins present? authorized?
    uv run python kobo_import.py authorize             # one-time anonymous ADE identity (+ backup zip)
    uv run python kobo_import.py fulfil ~/Downloads/URLLink*.acsm
    uv run python kobo_import.py export --ids 7-11     # library → sources/<Author - Title (Year, Publisher)>.epub
    uv run python kobo_import.py verify sources/*.epub # real text? encryption.xml gone?

`fulfil` refuses to run while the Calibre GUI is open (it holds the library
lock; calibredb would fail). Kobo's .acsm links expire within hours — fulfil
them the day you download them, and re-download a title from kobo.com → My
Books if fulfilment reports an E_ADEPT error.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

CALIBRE_DIR = Path(os.environ.get("CALIBRE_DIR", r"C:\Program Files\Calibre2"))
SOURCES_DIR = Path(__file__).parent / "sources"
DEDRM_URL = "https://github.com/noDRM/DeDRM_tools/releases/download/v10.0.9/DeDRM_tools_10.0.9.zip"
DEACSM_URL = "https://github.com/Leseratte10/acsm-calibre-plugin/releases"


def _exe(name: str) -> str:
    p = CALIBRE_DIR / f"{name}.exe"
    if not p.exists():
        sys.exit(f"{p} not found — install Calibre (64-bit) or set CALIBRE_DIR")
    return str(p)


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def _calibre_gui_running() -> bool:
    out = _run(["tasklist", "/FI", "IMAGENAME eq calibre.exe"]).stdout
    return "calibre.exe" in out


def _plugins() -> dict[str, str]:
    out = _run([_exe("calibre-customize"), "-l"]).stdout
    found = {}
    for line in out.splitlines():
        m = re.match(r"\S.*?\s{2,}(\S.*?)\s{2,}\((.*?)\)", line)
        if m:
            found[m.group(1).strip()] = m.group(2)
    return found


def _config_dir() -> Path:
    out = _run([_exe("calibre-debug"), "-c", "from calibre.utils.config import config_dir; print(config_dir)"]).stdout
    return Path(out.strip().splitlines()[-1])


# ── check ──────────────────────────────────────────────────────

def cmd_check(_args) -> int:
    ok = True
    print(f"Calibre: {CALIBRE_DIR}  ({_run([_exe('calibre-debug'), '--version']).stdout.strip()})")
    plugins = _plugins()
    for name, url in (("DeACSM", DEACSM_URL), ("DeDRM", DEDRM_URL)):
        if name in plugins:
            print(f"  [ok] {name} {plugins[name]}")
        else:
            ok = False
            print(f"  [missing] {name} — download {url}, unzip, then Calibre → Preferences → Plugins → "
                  f"Load plugin from file → the inner {name}_plugin.zip (or `calibre-customize -a`). Restart Calibre.")
    acct = _config_dir() / "plugins" / "DeACSM" / "account" / "activation.xml"
    print(f"  [{'ok' if acct.exists() else 'missing'}] DeACSM authorization ({acct})"
          + ("" if acct.exists() else " — run `kobo_import.py authorize`"))
    print(f"  [{'busy' if _calibre_gui_running() else 'ok'}] Calibre GUI "
          + ("is open — close it before `fulfil`/`export`" if _calibre_gui_running() else "closed"))
    return 0 if ok and acct.exists() else 1


# ── authorize ──────────────────────────────────────────────────

_AUTH_SCRIPT = r'''
import os, sys, zipfile
from calibre.utils.config import config_dir
plugin_dir = os.path.join(config_dir, "plugins")
sys.path.insert(0, os.path.join(plugin_dir, "DeACSM.zip"))
mods = os.path.join(plugin_dir, "DeACSM", "modules")
sys.path.insert(0, mods)
for name in os.listdir(mods):
    sys.path.insert(0, os.path.join(mods, name))
import prefs as pluginprefs
from libadobe import createDeviceKeyFile, update_account_path, VAR_VER_SUPP_CONFIG_NAMES, get_activation_xml_path
from libadobeAccount import createDeviceFile, createUser, signIn, activateDevice, exportAccountEncryptionKeyDER, getAccountUUID
p = pluginprefs.DeACSM_Prefs()
update_account_path(p["path_to_account_data"])
if os.path.exists(get_activation_xml_path()):
    print("already authorized")
else:
    idx = VAR_VER_SUPP_CONFIG_NAMES.index("ADE 2.0.1")     # old DRM flavour; works for Kobo
    createDeviceKeyFile(); createDeviceFile(False, idx)
    for step, (ok, resp) in (("createUser", createUser(idx, None)),):
        if not ok: sys.exit(f"{step} failed: {resp}")
    ok, resp = signIn("anonymous", "", "")
    if not ok: sys.exit(f"signIn failed: {resp}")
    ok, resp = activateDevice(idx, None)
    if not ok: sys.exit(f"activateDevice failed: {resp}")
    print("authorized: anonymous ADE 2.0.1 identity")
dest = sys.argv[-1]
acc = p["path_to_account_data"]
with zipfile.ZipFile(os.path.join(dest, "deacsm_activation_backup.zip"), "w") as z:
    for f in ("activation.xml", "device.xml", "devicesalt"):
        z.write(os.path.join(acc, f), f)
der = os.path.join(dest, "adobe_uuid_" + getAccountUUID() + ".der")
exportAccountEncryptionKeyDER(der)
print("backup + key written to", dest)
'''


def cmd_authorize(args) -> int:
    if "DeACSM" not in _plugins():
        sys.exit("DeACSM plugin is not installed — see `check`")
    dest = Path(args.backup_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    script = dest / "_deacsm_authorize.py"
    script.write_text(_AUTH_SCRIPT, encoding="utf-8")
    r = _run([_exe("calibre-debug"), "-e", str(script), str(dest)])
    script.unlink(missing_ok=True)
    print(r.stdout.strip() or r.stderr.strip())
    print(textwrap.dedent(f"""
        Keep {dest / 'deacsm_activation_backup.zip'} — every book fulfilled with this identity is
        licensed to it. The .der next to it is only needed if DeDRM does not pick the key up on its
        own (Customize DeDRM → Adobe Digital Editions ebooks → import).""").strip())
    return r.returncode


# ── fulfil ─────────────────────────────────────────────────────

def cmd_fulfil(args) -> int:
    if _calibre_gui_running():
        sys.exit("Calibre GUI is open — close it (it holds the library lock)")
    plugins = _plugins()
    if "DeACSM" not in plugins or "DeDRM" not in plugins:
        sys.exit("both DeACSM and DeDRM must be installed — see `check`")
    files = [f for pat in args.acsm for f in glob.glob(os.path.expanduser(pat))]
    if not files:
        sys.exit("no .acsm files matched")
    added = []
    for f in files:
        r = _run([_exe("calibredb"), "add", f])
        out = r.stdout + r.stderr
        m = re.search(r"Added book ids: ([\d, ]+)", out)
        fulfilled = "successfully fulfilled" in out
        stripped = "Executing plugin DeDRM" in out and "Plugin returned path" in out
        print(f"{Path(f).name}: fulfilled={fulfilled} dedrm={stripped} ids={m.group(1) if m else '-'}")
        if not fulfilled:
            print("   ", [ln for ln in out.splitlines() if "E_" in ln or "rror" in ln][:3],
                  "\n    (expired link? re-download the .acsm from kobo.com → My Books)")
        if m:
            added += [int(x) for x in m.group(1).split(",") if x.strip()]
        if args.delete and fulfilled:
            os.remove(f)
    print("library ids:", added, "→ next: `kobo_import.py export --ids", ",".join(map(str, added)) + "`")
    return 0


# ── export ─────────────────────────────────────────────────────

def _ids(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def _clean_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", s).replace("  ", " ").strip()


def cmd_export(args) -> int:
    if _calibre_gui_running():
        sys.exit("Calibre GUI is open — close it (it holds the library lock)")
    r = _run([_exe("calibredb"), "list", "--fields", "id,title,authors,publisher,pubdate,formats", "--for-machine"])
    rows = {row["id"]: row for row in json.loads(r.stdout)}
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for i in _ids(args.ids):
        row = rows.get(i)
        if not row:
            print(f"id {i}: not in library")
            continue
        epubs = [f for f in row["formats"] if f.lower().endswith(".epub")]
        if not epubs:
            print(f"id {i}: no EPUB format ({row['formats']})")
            continue
        year = (row.get("pubdate") or "")[:4]
        publisher = row.get("publisher") or ""
        meta = ", ".join(x for x in (year, publisher) if x and year != "0101")
        name = _clean_name(f"{row['authors']} - {row['title']}" + (f" ({meta})" if meta else "")) + ".epub"
        target = dest / name
        shutil.copy2(epubs[0], target)
        print(f"id {i}: → {target.name}")
    print("Rename to the CORPUS.md scheme if the metadata was thin, add the title to SOURCE_PROFILE_MAP, then run verify.")
    return 0


# ── verify ─────────────────────────────────────────────────────

def cmd_verify(args) -> int:
    from bs4 import BeautifulSoup
    bad = 0
    for pat in args.epub:
        for f in glob.glob(os.path.expanduser(pat)):
            z = zipfile.ZipFile(f)
            names = z.namelist()
            docs = [n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))]
            chars = sum(len(BeautifulSoup(z.read(n), "lxml").get_text(" ", strip=True)) for n in docs)
            drm = any("encryption.xml" in n for n in names)
            images = sum(1 for n in names if re.search(r"\.(jpe?g|png|gif)$", n, re.I))
            verdict = "DRM" if drm else ("image-only (needs --vision on a PDF copy)" if chars < 20_000 else "ok")
            bad += verdict != "ok"
            print(f"{Path(f).name[:70]:70s} | {len(docs):3d} docs | {chars:9,} chars | {images:4d} images | {verdict}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    a = sub.add_parser("authorize")
    a.add_argument("--backup-dir", default=str(Path.home() / "Downloads"))
    f = sub.add_parser("fulfil")
    f.add_argument("acsm", nargs="+")
    f.add_argument("--delete", action="store_true", help="delete each .acsm after a successful fulfilment")
    e = sub.add_parser("export")
    e.add_argument("--ids", required=True, help="e.g. 7-11 or 7,9,11")
    e.add_argument("--dest", default=str(SOURCES_DIR))
    v = sub.add_parser("verify")
    v.add_argument("epub", nargs="+")
    args = ap.parse_args()
    return {"check": cmd_check, "authorize": cmd_authorize, "fulfil": cmd_fulfil, "export": cmd_export, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
