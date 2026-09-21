# Importing Kobo purchases into the corpus

Kobo sells clean EPUBs, but getting one onto disk is a five-step chain that broke in
three places during 2025–26. This is the route that worked on 2026-09-20 (Calibre 9.15,
Windows 11) and the `oly-ingestion/kobo_import.py` utility that drives it.

## Why it is not just "download the EPUB"

| What you'd expect | What actually happens (2026) |
|---|---|
| Kobo Desktop downloads books to disk | Kobo Desktop is now a shell around the web reader; nothing decryptable is stored locally, so the old Obok plugin finds no books |
| kobo.com → My Books → Download gives an `.epub` | It gives a 1.4 KB `URLLink.acsm` — an Adobe DRM licence ticket, valid for hours |
| Adobe Digital Editions turns `.acsm` into the book | ADE is end-of-life: Adobe handed it to Wipro/ByteBooks, Adobe-ID sign-in ended 2026-06-23, the download page is gone |

So the working chain is **Calibre + two plugins**: one that fulfils the `.acsm` itself
(no Adobe software) and one that strips the DRM on import.

## Software needed

| Component | Where | Notes |
|---|---|---|
| **Calibre 64-bit** (≥ 7; tested 9.15) | https://calibre-ebook.com/download_windows | Default install path `C:\Program Files\Calibre2` — `kobo_import.py` reads `CALIBRE_DIR` to override |
| **DeACSM** — "ACSM Input" plugin, Leseratte10 (v0.0.16) | https://github.com/Leseratte10/acsm-calibre-plugin/releases → `DeACSM_0.0.16.zip` | Load the zip as-is. Python reimplementation of libgourou: creates an emulated ADE identity and talks to the Adobe licence server |
| **DeDRM** — noDRM fork (v10.0.9) | https://github.com/noDRM/DeDRM_tools/releases/download/v10.0.9/DeDRM_tools_10.0.9.zip | **Unzip first**; load the inner `DeDRM_plugin.zip`. The archive also holds `Obok_plugin.zip` (Kobo Desktop importer — useless now, see above). The original apprenticeharper repo stopped at v7.2.1 (2021) and errors on load |

Loading a plugin: Calibre → Preferences → Plugins → *Load plugin from file* → restart
Calibre. Or headless: `"C:\Program Files\Calibre2\calibre-customize.exe" -a <plugin>.zip`.
The DeDRM install has to be done by hand — the agent sandbox refuses to fetch or install it.

## Procedure

All commands from `oly-ingestion/`, with the Calibre GUI **closed** (it holds the library
lock; `calibredb` fails while it is open).

```bash
# 0. everything present?
PYTHONUTF8=1 uv run python kobo_import.py check

# 1. once per machine: anonymous ADE 2.0.1 identity for DeACSM, backup + key → ~/Downloads
PYTHONUTF8=1 uv run python kobo_import.py authorize

# 2. buy on kobo.com → My Books → ⋯ → Download → URLLink*.acsm land in Downloads
#    (if the ⋯ menu has no Download, the publisher disabled export — check before buying)
PYTHONUTF8=1 uv run python kobo_import.py fulfil ~/Downloads/URLLink*.acsm
#    each .acsm → "fulfilled=True dedrm=True ids=N": DeACSM downloaded the EPUB and DeDRM
#    stripped it on import. E_ADEPT_* = expired ticket → re-download that title, retry.

# 3. library → sources/ under the Author - Title (Year, Publisher).epub scheme
PYTHONUTF8=1 uv run python kobo_import.py export --ids 7-11
#    fix the names by hand where Calibre's metadata is thin (Kobo often ships "Unknown")

# 4. real text, no encryption.xml?
PYTHONUTF8=1 uv run python kobo_import.py verify "sources/*.epub"

# 5. map + ingest (add the title to SOURCE_PROFILE_MAP in processors/chunker.py first)
PYTHONUTF8=1 uv run python pipeline.py --source "sources/<file>.epub" --title "<map key>" \
    --author "<Author>" --type book --contextualize --classifier jev
```

`authorize` is the one step the plugin only offers through its GUI ("Create anonymous
authorization"); `kobo_import.py` runs the same four calls (`createDeviceKeyFile`,
`createDeviceFile`, `createUser`, `signIn("anonymous")`, `activateDevice`) through
`calibre-debug -e`, emulating ADE 2.0.1 — the old DRM flavour, which is what Kobo serves and
what DeDRM handles most reliably. **Keep `deacsm_activation_backup.zip`**: every fulfilled
book is licensed to that identity; losing it means re-buying nothing but re-fulfilling
needs a fresh identity and Kobo's ticket may have expired.

## Pitfalls seen on the first run

- "Not a valid plugin" → the outer `DeDRM_tools_10.0.9.zip` was loaded instead of the inner
  `DeDRM_plugin.zip`.
- `calibredb: Another calibre program … is running` → close the Calibre GUI.
- Fulfilment works but the EPUB is still encrypted (`verify` says DRM) → DeDRM was installed
  *after* the fulfil; re-add the `.acsm` (fresh ticket) or import the exported `.der` into
  DeDRM (Customize → Adobe Digital Editions ebooks) and re-add the EPUB.
- Amazon reviews calling the Sportivny Press ebooks "gibberish" were about the Kindle
  conversions; the Kobo EPUBs are real text (276–759k chars each, `verify` ok).
- DRM-free titles (small publishers on Kobo Writing Life) come straight down as `.epub` from
  My Books; `fulfil` is not needed, go to step 3.

## Legal note

Kobo's export button and the Adobe licence are the publisher-sanctioned path; what DeDRM
does with the result is circumvention for personal use, which is the purchaser's decision
and varies by jurisdiction (Canada: Copyright Act s. 41.1). Nothing here is for
redistribution — `sources/` stays gitignored.
