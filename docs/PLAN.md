# PoE Harvest Tracker – agreed plan

_Last updated: 2026-11-22_

## Goal
Log every Harvest (Sacred Flora) encounter – the mods on the plant and the lifeforce it gave –
then use that data to estimate lifeforce from a plant's mods and show which mods are best and worst.

## Ground rules
- **Hotkey mode.** The screen is read only when you press a key: one screenshot per press
  (GGG's overlay rule: screen reads "once per manual key press").
- The app never sends input to the game, never reads game memory and never changes game files.
- It reads the game log (`D:\GGG\logs\LatestClient.txt`) for map names and to know whether you're
  in a map, which GGG allows when the user knows about it.

## Your setup
- Windows PC, 2560×1440, windowed fullscreen, 2 monitors, no HDR, English client.
- Loot filter (synced FilterBlade): every Crystallised Lifeforce label is magenta
  (background 255,0,255), black text and border, font size 45.
- Atlas Drops (80–85% on every map so far) is **not** recorded yet; we accept the few % of noise.
  For the finished app (22 Nov): a box to set it by hand - see Open items.
- Everything lives under `D:\poe harvest`.

## Controls
| Key | What happens |
|---|---|
| **Numpad 1** (node) | Reads the Flora tooltip nearest the mouse. Press again after a reroll: the new read replaces the old one and counts as 1 reroll (1 chaos). |
| **Numpad 3** (result) | Reads every magenta lifeforce label on screen, adds them up and saves the encounter. Pressing again replaces the read. |

- **Keys only react when Path of Exile is the active window and you're in a map** (area from the game log).
  Typing numbers in the currency exchange, search bars or chat in your hideout/town, or in any other
  program, is ignored – no screenshot, no sound.
- Works with NumLock on or off and with keys sent by mouse software; the separate End / Page Down keys do nothing.
- Sounds: one high beep = node read OK · two rising beeps = result saved ·
  low buzz = a tooltip/label was there but couldn't be read, press again.
  If a press in a map finds nothing to read at all (e.g. typing in a stack-split box), it stays silent.

## Popup
- Shows next to the Flora tooltip (never over it or the reroll buttons); the result popup shows near the lifeforce.
- **Closes when you click anywhere in the game**, like Awakened PoE Trade. It never takes keyboard focus,
  and the click still reaches the game.
- **Until there's enough data:** the node popup lists what was read (plant type, rarity, each mod and value)
  as confirmation the app is working; the result popup shows the amount saved. A footer shows how many
  encounters have been logged so far.
- **Once there's enough data:**
  - Estimate with a likely range, e.g. `≈ 450 Wild (380–530)`.
  - Breakdown per mod with its effect in %:
    ✔ green = raises lifeforce · ✘ red = lowers it · ~ yellow = no direct effect (e.g. added wisps).
  - `vs last roll: +35` after a reroll.
  - Result popup: actual vs estimate, e.g. `468 Wild – estimate was 450 (+4%)`.
  - Any mod's icon can be overridden in the dashboard.

## Wildwood wisps
- Some maps show a wisp counter at the bottom right, above the skill bar (e.g. yellow 2,378 · blue 4,571).
  Only the colours present are shown, and the UI above it moves up when it appears.
- Yellow = Vivid wisps = **more item quantity** for the monsters they empower; blue = Primal (extra currency);
  purple = Wild (more rarity). The bonus grows with the number of wisps, but GGG has never published
  a per-wisp number.
- Every Numpad 1 and Numpad 3 screenshot is checked for the counter; each colour's count is stored
  (0 when the counter isn't there).
- The model treats the yellow count as a factor, and learns how much it adds from your own data
  (trying both a straight and a diminishing curve and keeping whichever predicts better).
  Blue and purple are stored too, so the data can confirm they don't change the amount.
- The popup includes a line for it, e.g. `✔ Yellow wisps 2,378   +X%`.
- The Flora mod "Sacred Fauna are affected by N additional Wildwood Wisps" may also raise lifeforce
  directly; its icon follows what the data shows rather than being fixed to ~.

## How an encounter is tracked
1. Entering a map (from Client.txt) starts a new map record.
2. Numpad 1 creates a pending encounter; the last press before you activate the plant counts.
3. Numpad 3 completes it.
4. Leaving the map without a result press → saved as *incomplete* and left out of the statistics.
   A result press with no node press → ignored, low buzz.
5. Sacred Specimens are stored as their own kind with separate statistics.
6. Checks: lifeforce colour must match the plant type (except plants with "Vivid, Wild and Primal Sacred
   Fauna can spawn", which drop mixed colours), values must be in range, a new mod line needs
   one confirmation from you, and anything uncertain goes to a review queue instead of the statistics.

## Components
1. **Hotkey listener** – Windows keyboard hook; the hook only queues the press, so it adds no input lag.
2. **Capture** – one screenshot of the PoE window (DPI-aware, real 2560×1440 pixels).
3. **Detectors** (OpenCV) – the tooltip's gold header ornaments; the magenta lifeforce labels.
4. **Text reading** – Tesseract (won phase 1: 271/271 checks vs 236/271 for RapidOCR). Each text colour
   is isolated before reading; mod lines are matched to known wordings (`app\harvest\mods.py`).
5. **Encounter tracker** + Client.txt reader.
6. **Database** – SQLite, plus every cut-out image kept for checking.
7. **Popup** – always-on-top window that never takes focus; while it's open it checks the mouse buttons
   to know when you click into the game (no hook on mouse movement, so no mouse lag).
8. **Model** – scikit-learn.
9. **Dashboard** – a local page in your browser.

No constant screen capture: the app only does work for a moment after a key press, and heavy work
(OCR, model) runs away from the input handling.

## Database
- `maps` – name, time entered
- `encounters` – map, kind (flora / specimen), type, rarity, plant name, times, total lifeforce,
  rerolls, chaos spent, wisp counts (yellow / blue / purple) at the node press and at the result press,
  status (ok / needs review / incomplete)
- `encounter_mods` – encounter, mod, value
- `mods` – known mod wordings, e.g. `#% increased Sacred Fauna spawn rate`, plus any icon override
- `lifeforce_stacks` – encounter, colour, amount
- `reads` – every key press: image cut-out, raw text, which encounter it belongs to

## Estimates
- Model: lifeforce = base amount (by type) × an effect per mod × the yellow-wisp effect,
  fitted on the log scale so things that stack on each other (spawn rate × pack size × wisps) are handled.
  Each mod's effect is shown as a %, **against an average mod** (a rare plant always has 4 mods).
  Plants are always rare, so rarity isn't a factor.
- Built as ridge regression in numpy (no scikit-learn needed): every effect is pulled gently towards zero,
  except Duplicated, which is pulled towards ×2 (it drops every stack twice). Leave-one-out testing picks
  how strong the pull is, whether a mod's effect grows with its rolled value, and whether yellow wisps
  help in a straight line or with diminishing returns. Refitted after every finished plant.
- Yellow wisps per map = the reading seen most often across all presses in that map.
- Shown from 30 plants **and** only while it beats the type average; "rough" until 100 (agreed 22 Nov).
- Reroll advice (agreed 22 Nov): lifeforce prices from poe.ninja's currency exchange
  (`/poe1/api/economy/exchange/current/overview`, about once an hour, cached in `data\prices.json`).
  Every plant roll read is a sample of a fresh roll; the popup says Reroll while rerolling until a plant
  reaches a target value pays for its chaos on average, Close call under 1 chaos, else Keep.
- The wisp effect can only be measured once there are enough encounters both with and without yellow
  wisps, so it will firm up later than the mod effects.
- ✔ / ✘ when the effect is meaningful (3% or more) and its 80% range doesn't cross zero; ~ otherwise.
- Rough estimates after about 100 encounters, good ones after a few hundred. Once there's plenty of data
  a more flexible model is tried, and whichever predicts better on unseen encounters is kept.

## Dashboard
- Mod rankings, best to worst (lifeforce change per 10 points of each mod, with how certain it is)
- Average lifeforce with vs without each mod
- Common mod combinations: count, average, median
- Each mod's value vs lifeforce
- Lifeforce by type and rarity; Specimens shown separately
- Model accuracy: predicted vs actual
- Chaos spent on rerolls vs gain in the estimate
- Encounter log with images; fix misreads and clear the review queue

## Build phases
0. **Screenshot tool** – done, now retired (the tracker saves the same screenshots).
1. **Reader** – done: `D:\poe harvest\app`. 271/271 checks right on the first 48 screenshots
   (`app\harvest\answer_key.json`); `Read my screenshots.bat` builds `data\encounters_from_screenshots.csv`.
2. **Live tracker** – built: `Start Harvest tracker.bat`. Hotkeys, sounds, game log, database
   (`data\harvest.db`), popup showing what was read, reroll counting, screenshot import.
   Reading is now one OCR call per tooltip (~0.7 s on a slow test machine, faster on your PC).
   First live session done (11 encounters); fixes above. Reader: 401/401 checks on 61 shots.
3. **Dashboard + review page** – built: `Open dashboard.bat` (page at http://127.0.0.1:8765, this PC only).
   Tabs: Overview (totals, lifeforce per plant by type, rerolls), Mods (with vs without each mod,
   mod value vs lifeforce, mod pairs seen 3+ times), Encounters (filterable log; click a row for the
   cut-outs, edit mods / type / lifeforce, count or exclude), Review (everything not counted yet),
   Mod icons (force ✔/✘/~ per mod for phase 4). Corrections go to the database and
   `data\manual_fixes.json` (only what changed), so a rebuild keeps them. Damaged screenshots now
   fall back to the tracker's cut-out in the dashboard too. Still to come in phase 4: rankings per
   10 points of a mod, model accuracy, chaos vs gain, lifeforce by rarity.
4. **Estimates** – built (22 Nov), on 50 plants: typically 18% off when estimating a plant it never saw,
   against 29% for the type's average; 80% of plants land inside their 80% range. Shown in the popup
   (marked rough until 100 plants), with ✔/✘/~ per mod, yellow wisps, `vs last roll`, Keep/Reroll advice
   from poe.ninja prices, and actual vs estimate on Numpad 3. Dashboard: Mod effects, Estimates tab
   (estimate vs actual, reroll payoff in chaos, prices), best/worst mods, estimate column, auto icons.
5. **Reroll ledger** (asked 22 Nov) – built. Rerolls tab: chaos spent, extra lifeforce vs each plant's
   first roll (drop minus the first roll's estimate) and its worth in chaos at the price when the plant
   was finished, net, and "net without luck" (final roll's estimate minus the first roll's). Running chart
   of chaos spent vs lifeforce gained in chaos. Popups show it too. Reroll count editable in the dashboard.
   Agreed 22 Nov: a plant is always finished before another of the same type in that map is read, so
   same map + same type + no result since = the same plant (no time or distance limit); a plant with no
   Numpad 3 result is dropped from everything. A rebuild keeps the prices and estimates from the time.

6. **The app** (asked 22 Nov, agreed 22 Nov) – **steps 6a–6c built**: code in github.com/Alyrico/poe-harvest
   (public; © Alyrico, all rights reserved, free to use; app name PoE Harvest Tracker; starter estimates on).
   Every push builds `PoE-Harvest-Tracker-Setup-x.y.z.exe` on GitHub (Actions → Build → Artifacts), self-tested
   on Windows (bundled Tesseract reads, dashboard serves, starter model loads). Reader at simulated 1080p and
   4K: 402/402. Next: 6d - you install it, point it at D:\poe harvest\data, play a map, then 1080p screenshots.

## The app (proposal, 22 Nov)
Decided: for other players too · runs as a tray icon · installed with a Setup.exe that has everything
inside (Python, OCR, libraries - nothing else to install) · built automatically on GitHub.

**What you get**
- `PoE Harvest Setup 1.0.0.exe` → installs to Program Files, Start-menu shortcut, uninstaller,
  optional "start with Windows". About 100 MB.
- A tray icon by the clock (green = ready, grey = paused / not in a map). Right-click: Open dashboard ·
  Settings · Pause · Open data folder · Quit. No console window; problems go to a log file.
- Everything that works today: hotkeys, popups, sounds, estimates, reroll advice and ledger, dashboard
  (served by the app itself), corrections, rebuild from saved reads.
- Only one copy can run at a time (the double-tool problem from 21 Nov can't happen).

**Settings window** (small, opens from the tray)
| Setting | Default |
|---|---|
| Atlas Drops % | 0 until set; every plant is stamped with the number set when it was read |
| Plant / result hotkeys | Numpad 1 / Numpad 3; click "Change" and press any key or a mouse side button |
| Data folder | Documents\PoE Harvest (yours: D:\poe harvest\data) - "Move" copies everything over |
| Path of Exile folder | found automatically (standalone or Steam); "Browse" if not |
| Rich Soil allocated | asked on first start (changes the range checks ×1.5) |
| Sounds | on, with a volume slider |
| Popup size | 100% |
| Save full screenshots | off (cut-outs of what was read are always kept; screenshots are ~6 MB each) |
| Lifeforce prices | poe.ninja, current league (or pick one) |
| Check for updates | on (looks at the GitHub releases once a day; never installs by itself) |

**Making it work for other players**
- **Resolution**: the reader scales with the game window's height, so 1080p, 1440p, 4K and ultrawide work.
  Tested on your screenshots shrunk to 1080p and enlarged to 4K, plus a few real 1080p screenshots from you.
- **Steam or standalone**: the game log is found automatically.
- **Loot filter**: a short guide (FilterBlade steps for the magenta lifeforce labels), and the label colour
  is a setting. Settings has "Test": press the result key over a lifeforce drop and it shows what it read.
- **English client only** (said on the download page).
- **New players start with no data**: popups list the mods until 30 plants, then estimates appear.
- **Atlas Drops**: the estimate scales with it - straight-line until your white-map test says otherwise.

**Moving your setup over**: point the data folder at `D:\poe harvest\data` - your database, corrections,
cut-outs and screenshots carry over as they are. The .bat files stay working until you're happy.

**How it's built**
- Code moves into a GitHub repo. On every change, GitHub's Windows build server runs PyInstaller
  (Python + libraries into one folder), adds Tesseract and its English data, and wraps it all with Inno
  Setup into the Setup.exe. A version tag (v1.0.0) publishes it as a GitHub Release others can download.
- The tray icon, popups and hotkeys stay plain Windows calls (no extra libraries); the settings window
  uses tkinter, which comes with Python.
- Licences: Tesseract, OpenCV (Apache 2.0), numpy (BSD), Pillow, mss (MIT) - all fine to bundle.
- **Unsigned at first**: Windows SmartScreen will say "unknown publisher" (More info → Run anyway), and
  some antivirus may flag it. A code-signing certificate fixes that later (paid, yearly).
- Same GGG rules as now: one screen read per key press, no input sent, no memory reading.

**Steps**
6a. Restructure the code into the app layout; settings file; resolution scaling; Steam/standalone
    detection; Atlas Drops on every plant and in the estimate. (Built and tested here.)
6b. Tray icon, settings window, single instance, log file, update check. (Tested here, then on your PC.)
6c. GitHub repo + build workflow + installer.
6d. You install it on your PC, point it at your data, play a map. Then a few 1080p screenshots.
6e. v1.0.0 release with the download page and filter guide.

**Needs from you**: connect GitHub to Claude (claude.ai → Settings → Connectors → GitHub) and create an
empty repo for it; then I attach it and push the code.

**Open questions**: see the list in the chat of 22 Nov (repo name and public/private, app name, licence,
whether new players get starter estimates from your data).

## Learned from the first screenshots (21 Nov)
- Mod lines can wrap onto two lines; reward lines ("Tier 3 Sacred Fauna drop an additional …") are orange.
- Sacred Specimens have a blue frame, a cyan name ("Wild Specimen") and no mods.
- Plants can show three reroll buttons under the tooltip.
- Lifeforce labels show white text on magenta.
- The wisp counter only appears in some maps (see Wildwood wisps).

## Learned from phase 1 (48 screenshots, 22 complete encounters)
- Every plant so far has been rare; plants show 2–4 reroll buttons.
- 14 different mods so far, plus reward lines (Stacked Deck, Thread(s), Currency Items, Items Duplicated);
  full list in `app\harvest\mods.py`.
- Two labels dropped together sit directly on top of each other; the reader keeps them apart.
- "Vivid, Wild and Primal Sacred Fauna can spawn" gave 66 Primal + 278 Wild from a Vivid plant.
- Things drawn on top can hide text: boss health bars, a hovered monster's mod list, buff icons,
  health bars. A hidden corner is handled; hidden text means pressing again.

- A reroll changes the plant's name and all its mods, but not its type or where its tooltip sits.
  So: same type + same spot (or within 30 s) + different mods = reroll (1 chaos).
- You always roll plants to rare, so rarity isn't a factor in the model.
- The mixed-colour plant really dropped no Vivid at all (66 Primal + 278 Wild).
- A health bar across a reward line is tolerated (rewards are matched loosely).

## Learned from the first live session (21 Nov evening, 11 encounters)
- Reads are fast enough in play. Problems found and fixed:
  - A tooltip right under the buff icons (top-left) had its corner hidden; hidden corners are now
    traced along the header's *bottom* border, which the buff bar doesn't reach.
  - A health bar across one word of a mod line: a line that clearly matches one mod (and no other)
    is accepted; an ambiguous one ("...chance to be" without Magic/Rare) is flagged instead.
  - Wisp numbers over light stone or heavy sparks were misread: digits are now isolated by their
    yellow-green colour, sparks are filtered out, and a number that doesn't look valid is stored as
    unknown. 0 wrong readings on all counter shots so far.
- Wisp counts stay the same for every plant in a map, so phase 4 will use the most common
  reading per map (fixes any one-off "unknown").
- The old screenshot tool was left running alongside the tracker: every press was captured twice
  and two screenshots were damaged. Files are now written safely, and `Rebuild database.bat`
  re-reads everything (using the saved cut-outs if a screenshot is damaged).

## Mods: compared with poedb (all 22 Harvest tree mods)
- Seen so far: 19 of 22. Not seen yet: "Items dropped by Sacred Fauna are fully linked", "... have maximum
  sockets", "Tier 3 Sacred Fauna drop 1 additional Unique Items" - all three are now recognised too.
- Every value seen is exactly 1.5x poedb's range (rolls span the full 0-100% of it): Rich Soil, "Explicit
  modifiers on Sacred Flora have 50% increased effect". Set as FLORA_MOD_EFFECT = 1.5 in mods.py.
- A rare plant always has 2 prefixes + 2 suffixes (rewards are suffixes). Every plant so far fits, so a
  read with fewer is flagged (a line was missed), and so is a value outside the possible range.
- "Items dropped by Sacred Fauna are Duplicated" is orange like the rewards, but lifeforce is an item,
  so it doubles the lifeforce (Untamed Vegetation: 3,213). It's stored and modelled as a mod.
- Phase 3/4 can show each mod's roll quality (0 = lowest possible roll, 1 = highest).

## Open items
- **Atlas Drops box** (asked 22 Nov, for the finished app): set the map's Atlas Drops % by hand; every plant
  gets the number that was set when it was read, and the estimate scales with it - so other players'
  setups (and your white maps) give right numbers. You'll test on white maps whether lifeforce rises in a
  straight line with it. Not built yet; decide whether it goes in before that test so the test plants
  are recorded with their number.
- Rich Soil: confirmed allocated, 21 Nov. (If it's ever taken off, set FLORA_MOD_EFFECT = 1.0 in
  app\harvest\mods.py, or the range checks will flag every plant.)

## Folder layout
```
D:\poe harvest\
  PLAN.md               this file
  screenshot tool\      phase 0 helper (retired)
  screenshots\          shots from the helper (+ capture_log.csv)
  app\                  the reader (phase 1), the tracker (phase 2) and the dashboard (phase 3)
  data\                 harvest.db, encounters.csv, reads\ (image cut-outs), manual_fixes.json
```
