# Frontend TODO — 2026-07-31 Frontend & Design Audit

Scope: `oly-agent/web/templates/**` plus the router/template contract in
`oly-agent/web/{app,options}.py` and `routers/{program,log_session,generate,setup,history}.py`.
Every item was verified against the code at the cited location before filing.
Prefix: **FE**. Work order: top to bottom.

## 1. High

- [ ] **FE-H1 — Completing a program leaves a stale "active" badge and duplicate `#status-badge` IDs**
  - `templates/program.html:36-43` — the Complete button does `hx-target="#program-actions" hx-swap="outerHTML"`; the response (`templates/partials/outcome_summary.html:5-8`) renders a *new* `<span id="status-badge">completed</span>` inside the actions area, while the original badge in the page header (`program.html:15-18`) still says "active" until a full reload. Result: two elements with the same ID and contradictory status shown on one page. Fix: have the complete response retarget/update the header badge too (e.g. `hx-swap-oob`), and don't emit a second `#status-badge`.
- [ ] **FE-H2 — Export buttons vanish after completing a program**
  - Same swap as FE-H1: `#program-actions` (`program.html:25-71`) contains the Export CSV / Export PDF controls, and `partials/outcome_summary.html` replaces the whole div without re-rendering them. After completing, the user can no longer export the program without reloading. Fix: move the outcome card out of `#program-actions` (target a dedicated `#outcome-area`), or include the export links in the partial.
- [ ] **FE-H3 — No HTMX error handling anywhere → failed actions fail silently**
  - htmx does not swap content on 4xx/5xx by default, and no template or script listens for `htmx:responseError`. Concrete silent failures: double-clicking Generate (`routers/generate.py:43-48` returns 409), any rate-limit 429 from slowapi (`web/deps.py` limiter on nearly every POST), re-clicking Activate after activation (409 from `routers/program.py:65-66`), and any 500 partial render. The user clicks and literally nothing happens. Fix: add a global `htmx:responseError` handler (toast/banner), or `hx-swap="none"` + error targets per form.
- [ ] **FE-H4 — Activate/Abandon buttons stay visible and clickable after the action succeeds**
  - `program.html:27-53` — Activate and Abandon swap only `#status-badge`, so the buttons (and their `hx-confirm` flows) remain on screen in their pre-action state. Re-clicking Activate now 409s (silently, per FE-H3); re-clicking Abandon re-posts — note `routers/program.py:118-134` (`abandon`) has no status guard, unlike `activate` (409 guard) and `complete` (409 guard), so it will happily flip a completed program to abandoned. Fix: swap the whole `#program-actions` region (or use `hx-swap-oob`) so buttons reflect the new status, and add the same status guard to `abandon`.
- [ ] **FE-H5 — Dashboard adherence bar overflows past 100%**
  - `queries/dashboard.py:69` computes `pct = round(n_logged / p * 100)` unclamped (goal progress and lift ratios *are* clamped — lines 117-118, 190 — so this is an inconsistency, not a policy). Logging more sessions than prescribed renders `style="width: 112%"` in `dashboard.html:52-54`, and the track `div` has no `overflow-hidden`, so the bar spills past the rounded container. Fix: `min(100, ...)` in the query and/or `overflow-hidden` on the track.

## 2. Medium

### Bugs / dead code

- [ ] **FE-M1 — `{% block extra_js %}` in `log_session.html` never renders**
  - `templates/log_session.html:87-96` defines `{% block extra_js %}`, but `base.html` only declares `title` and `content` blocks — Jinja silently drops the script (the date-input defaulting JS is dead). Harmless today because `today` is rendered server-side (`routers/log_session.py:49,58`), but it's a trap for the next person who adds JS to that block. Fix: add `{% block extra_js %}{% endblock %}` before `</body>` in `base.html`, or delete the block.
- [ ] **FE-M2 — Unused/dead frontend assets**
  - `templates/partials/exercise_logged_row.html` — no `{% include %}` references it anywhere (superseded by `exercise_log_entry.html`). Delete it.
  - `base.html:77` — `[x-cloak]` rule: Alpine.js is never loaded. Delete.
  - `base.html:78-79` — `.htmx-indicator` rules: no element in any template uses the class. Either wire indicators up (see FE-M3) or delete the CSS.

### UX

- [ ] **FE-M3 — No busy/loading feedback on any HTMX action**
  - Generate, Save exercise, delete ✕, max update, Activate/Complete/Abandon: buttons give no spinner/disabled state while the request is in flight (the `.htmx-indicator` machinery exists but is unused). Combined with FE-H3, the UI feels dead on slow connections and invites double-submits. Fix: use the existing indicator CSS on buttons, or `hx-disabled-elt` (htmx 1.9.3+) to disable during requests.
- [ ] **FE-M4 — "Save Session ✓" button is just a navigation link**
  - `templates/partials/exercise_log_section.html:192-195` — the big green button reads "Save Session ✓" but is an `<a href="/program/{{ session.program_id }}">`; everything is already persisted via HTMX. The label implies unsaved work and trains users to fear leaving the page. Rename to "Done — back to program".
- [ ] **FE-M5 — Generate page: hardcoded pipeline facts, and navigating away loses job state**
  - `templates/generate.html:6-9` hardcodes "16 LLM calls", "~5 minutes", "~$0.50" — these will drift from the pipeline as it evolves; derive them or soften the copy. Also, leaving `/generate` and returning while a job runs shows no in-flight indicator (polling only exists in the swapped partial `partials/generate_result.html:1-12`); the GET page (`routers/generate.py:15-26`) could check `jobs` for an in-flight job and resume polling.
- [ ] **FE-M6 — Program-list delete button is hover-only**
  - `templates/program_list.html:67-74` — the ✕ is `opacity-0 group-hover:opacity-100 focus:opacity-100`. Keyboard focus works, but touch devices have no hover: the only way to delete a program on mobile is an invisible button you must guess at. Fix: always show it at reduced opacity on `<sm` screens (e.g. `opacity-60 sm:opacity-0 sm:group-hover:opacity-100`).
- [ ] **FE-M7 — Setup vs. profile form drift (same data, different widgets and bounds)**
  - Weight class: setup is a free-text input (`setup.html:177-181`, placeholder "e.g. 89, 96, 102+" — actual classes use the "+102" suffix style shown in profile, i.e. "+87"/"+109", so the hint teaches a wrong format), profile is a `<select>` keyed off `biological_sex` (`profile.html:92-105`).
  - Bounds disagree: bodyweight max 250 (setup) vs 300 (profile); height step 0.1 vs 0.5; session duration max 240 vs 300; training age max 40 vs 50.
  - Checkbox naming: setup uses per-option `equip_<val>` / `fault_<val>` names but multi-value `strength_limiters` (`setup.html:219,256,270`) — three conventions for the same widget type; profile uses multi-value names for all three. It works (backend handles both), but it's friction for the next shared-form change.
  - Also: profile's weight-class options are rendered from the *saved* `biological_sex`; changing the sex select doesn't update the options until save + reload.
  - Fix: share one weight-class select partial, align min/max/step with server validation, and standardize checkbox naming.

### Accessibility

- [ ] **FE-M8 — Exercise row is a `<div onclick>` — keyboard and screen-reader inaccessible**
  - `templates/partials/exercise_log_entry.html:3-5` — the entire display row toggles edit via `onclick="toggleEdit(...)"` with no `role`, `tabindex`, or key handler; keyboard users cannot open the edit form at all (the ✕ and history link are reachable only because they stop propagation). Fix: make the row a `<button type="button">` (or add a real toggle button), with `aria-expanded`/`aria-controls` pointing at `tle-edit-<id>`.
- [ ] **FE-M9 — Form labels are not associated with their inputs**
  - Throughout `profile.html`, `setup.html`, `log_session.html`, `exercise_log_section.html`, `maxes_table.html`: `<label>Text</label>` followed by a sibling `<input>` with no `for`/`id` pair (login.html does it correctly — `login.html:80-93`). Clicking the label doesn't focus the field; screen readers announce unlabeled controls. Fix: add `for`/`id` pairs (or wrap the input in the label).
- [ ] **FE-M10 — Mobile nav toggle missing ARIA state; menu never closes**
  - `base.html:120-146` — the hamburger has `aria-label` but no `aria-expanded`/`aria-controls`, and the menu stays open after tapping a link (same-page anchorless nav does close via full page load, but it also stays open when tapping outside or re-tapping links client-side). Fix: toggle `aria-expanded` in the click handler; close on outside click / Escape.

## 3. Low

- [ ] **FE-L1 — Remapped gray text fails WCAG AA contrast on the warm card background**
  - `base.html:56-58` maps `text-gray-400` → `#A09A94` and `text-gray-500` → `#7A7570`; on `--card: #FDFBF8` that's roughly 2.4:1 and 3.9:1 — both under the 4.5:1 AA threshold for the small text they're used for (dates, "Day N" labels, helper text, table metadata). Darken `--text-faint`/`--text-muted` (target ≥ `#8A8580` / `#6E6963`-ish) or bump those usages to `text-gray-600`.
- [ ] **FE-L2 — Theme is a pile of `!important` utility overrides, duplicated four ways**
  - `base.html:13-80` restyles Tailwind's gray scale with `!important` instead of defining a custom palette; any non-gray utility (`bg-blue-50`, `text-green-700`…) keeps the default cool palette against the warm theme, and every *new* gray utility used must be manually added to the remap list. The same `:root` + remap block is copy-pasted into `login.html:11-41`, `setup.html:11-43`, and `error.html:11-26`, each a *different subset* (error.html remaps nothing and styles its button with inline `onmouseover`/`onmouseout` at `error.html:34-38` — also the only place that would break under a CSP). Fix: extract a shared `static/theme.css`, or better, a real Tailwind build with a custom `gray` palette (see FE-L3) so utilities generate correctly in the first place.
- [ ] **FE-L3 — All JS/CSS comes from third-party CDNs with no pinning or SRI**
  - `cdn.tailwindcss.com` (Play CDN — explicitly not for production; in-browser JIT on every page load, no caching win), `unpkg.com/htmx.org@1.9.12` (version-pinned but no `integrity`), `cdn.jsdelivr.net/npm/chart.js@4.4.0` (`program.html:214`), plus Google Fonts. Any of these going down (or being compromised) breaks/degrades every page, and nothing works offline. Fix: vendor htmx/chart.js into `static/` with SRI, and replace the Play CDN with a compiled `tailwind.css` build.
- [ ] **FE-L4 — Outcome card markup duplicated between `program.html` and `partials/outcome_summary.html`**
  - ~90 lines (metrics grid, maxes delta, phase-verdict block) exist in near-identical copies at `program.html:98-185` and `partials/outcome_summary.html:10-128`. They've already drifted slightly (the partial shows `sessions_completed/prescribed` and `athlete_feedback`; the full page doesn't). Fix: make `program.html` `{% include %}` the partial (passing `outcome=program.outcome_summary`), keeping FE-H1/FE-H2's retargeting in mind.
- [ ] **FE-L5 — "Warmup" badge keys off substring-matching a free-text rationale**
  - `program.html:349-351` — `'warmup' in ex.selection_rationale | lower` decides the badge. It's display-only, but it misfires on rationales like "not a warmup priority" and silently disappears if the generator's wording changes. Fix: carry an explicit flag/category on the exercise and key off that.
- [ ] **FE-L6 — Nav shows "Logout" unconditionally**
  - `base.html:115-117,137-139` — the Logout form renders even when `athlete_name` is absent from the session. Every page extending base is behind `AuthMiddleware` today, so it's latent, but the first semi-public page that extends base will show a Logout button to anonymous users. Guard it with the same `athlete_name` check as the profile link.

## Verified non-issues (so they don't get re-filed)

- `_safe_back` open-redirect handling in `routers/history.py:16-31` — correctly rejects protocol-relative, `://`, and control-char variants.
- CSRF posture — `OriginCheckMiddleware` (`app.py:131-152`) + `SameSite=Lax` session cookie + 64 KB body cap; adequate for this form-based app.
- Goal-progress and lift-ratio gauges clamp pct to 0–100 (`queries/dashboard.py:117-118,190`).
- `prefillExercise` uses `data-*` attributes rather than interpolating names into JS strings (`exercise_log_section.html:201-220`) — quote-safe.
- HTMX auth expiry — `AuthMiddleware` returns `HX-Redirect` for HTMX requests (`app.py:179-181`), so session expiry mid-interaction redirects to /login cleanly.
