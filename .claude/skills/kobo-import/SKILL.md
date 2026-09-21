---
name: kobo-import
description: Turn Kobo ebook purchases (URLLink*.acsm files) into clean EPUBs in oly-ingestion/sources/ and ingest them. Use when the user has bought a book on kobo.com, mentions .acsm files, Calibre, DeACSM/DeDRM, or asks to import/ingest a Kobo/Adobe-DRM ebook.
---

# Kobo purchase → corpus

Full background and pitfalls: `docs/KOBO-IMPORT.md`. The utility is `oly-ingestion/kobo_import.py`;
run every command from `oly-ingestion/` with `PYTHONUTF8=1 uv run python …`.

## Preconditions (say which are missing, don't work around them)
- Calibre 64-bit at `C:\Program Files\Calibre2` (or `CALIBRE_DIR`).
- Plugins loaded in Calibre: **DeACSM** (Leseratte10 `DeACSM_0.0.16.zip`, loaded as-is) and
  **DeDRM** (noDRM `DeDRM_tools_10.0.9.zip` → inner `DeDRM_plugin.zip`). The sandbox will refuse to
  download or install DeDRM — give the user the URL and the Preferences → Plugins → Load plugin
  from file steps, then continue once `kobo_import.py check` passes.
- Calibre GUI closed for `fulfil` / `export` (library lock). Wait with
  `until ! tasklist //FI "IMAGENAME eq calibre.exe" | grep -q calibre.exe; do sleep 3; done`.

## Steps
1. `kobo_import.py check` — stop and report if anything is `[missing]`.
2. `kobo_import.py authorize` — only if check says the DeACSM authorization is missing. Tell the
   user to keep `~/Downloads/deacsm_activation_backup.zip`.
3. `kobo_import.py fulfil ~/Downloads/URLLink*.acsm` — expect `fulfilled=True dedrm=True` per file.
   `E_ADEPT_*` means the ticket expired: ask the user to re-download that title from kobo.com → My
   Books and rerun for that file only. Do this the same day the .acsm was downloaded.
4. `kobo_import.py export --ids <ids from step 3>` — then rename in `sources/` to
   `Author - Title (Year, Publisher).epub` (Kobo metadata is often "Unknown"; use the book's title
   page, which `verify`'s char counts help confirm is real text).
5. `kobo_import.py verify "sources/<file>.epub"` — must print `ok` (not `DRM`, not `image-only`).
6. Add the title to `SOURCE_PROFILE_MAP` in `processors/chunker.py` (profile per `docs/CORPUS.md`),
   then `pipeline.py --source … --title "<map key>" --author … --type book --contextualize --classifier jev`.
7. Update `docs/CORPUS.md` (Ingested Sources table + Files on disk) with the new row.

Never move the EPUBs anywhere tracked by git; `sources/` is ignored except `sources/url_lists/`.
