# PoE Harvest Tracker

Track your Path of Exile **Harvest** plants: press a hotkey at a Sacred Flora and the app reads its
mods; press another once the lifeforce drops and it reads the amount. Over time you get:

- **An estimate at every plant** – how much lifeforce to expect, with each mod marked ✔ (more
  lifeforce), ✘ (less) or ~ (no clear effect), and whether a reroll is worth 1 chaos at today's
  poe.ninja prices.
- **A dashboard** in your browser – best and worst mods, lifeforce per plant type, estimate
  accuracy, every plant you've done, and a ledger of what rerolling has earned you in chaos.

Everything stays on your PC.

## Download

1. Download **PoE-Harvest-Tracker-Setup-x.y.z.exe** from the
   [latest release](https://github.com/Alyrico/poe-harvest/releases/latest).
2. Run it. Windows may say *"Windows protected your PC"* because the installer isn't signed yet:
   click **More info → Run anyway**.
3. The app starts in the tray by the clock (a green leaf; click the **^** arrow if you don't see it)
   and opens its Settings the first time.

Nothing else needs installing: Python, the text reader (Tesseract) and all libraries are inside.
It installs for your user only (no administrator prompt) and keeps your data in
`Documents\PoE Harvest`.

## Set up your loot filter

The app finds lifeforce drops by the colour of their label, so give every Crystallised Lifeforce
label a **magenta background (255 0 255), white text, and font size 45**:

- **FilterBlade**: in the Customizer, find *Crystallised Lifeforce* (Harvest), set the background
  to 255 0 255, text to 255 255 255, font size 45, and save/sync your filter.
- **Your own filter file**: add this at the top:

  ```
  Show
      BaseType == "Wild Crystallised Lifeforce" "Vivid Crystallised Lifeforce" "Primal Crystallised Lifeforce" "Sacred Crystallised Lifeforce"
      SetBackgroundColor 255 0 255 255
      SetTextColor 255 255 255 255
      SetBorderColor 0 0 0 255
      SetFontSize 45
  ```

Prefer another colour? Pick it in **Settings → Game → Lifeforce label colour**. To check, stand
next to a lifeforce drop and use **Settings → Game → Test**.

## Use it

| When | Press | You get |
|---|---|---|
| At a Sacred Flora (its tooltip on screen) | **Numpad 1** | the estimate and each mod's effect; one high beep |
| After every reroll | **Numpad 1** again | the new roll, and how it compares with the first, in chaos |
| When the lifeforce has dropped (labels on screen) | **Numpad 3** | the amount, vs the estimate; two rising beeps |

- A low buzz means something was there but couldn't be read – press again.
- The popup closes on your next click; it never takes focus from the game.
- Keys only count while Path of Exile is the active window **and you're in a map** – typing
  numbers in the currency exchange or a search box is never read.
- Press Numpad 1 on **every** roll, including the first: the first roll is what rerolling is
  measured against, and each new roll counts 1 chaos.
- A plant that never gets a Numpad 3 is left out of everything.
- Hotkeys can be any key or a mouse side button (Settings → Hotkeys).

**Atlas Drops**: set your map's Atlas Drops % in Settings (or tray → *Atlas Drops*) whenever it
changes. Every plant is saved with the value set when you read it, and estimates scale with it.

**Tray menu** (right-click the leaf): Open dashboard · Settings · Pause · Atlas Drops · Open data
folder · Loot filter guide · Quit. Double-click the leaf to open the dashboard.

## Estimates

Lifeforce = a typical amount for the plant type × an effect per mod × the yellow (Vivid) wisp
effect × (1 + Atlas Drops). New players start from the author's plants ("starter estimates");
your own plants take over as you log them (fully at 100). The dashboard's **Estimates** tab shows
how far off the estimates have been on your own plants.

## Requirements

- Windows 10 or 11, 64-bit
- Path of Exile (1) in **English**, windowed fullscreen or windowed; standalone or Steam
- Any resolution (1080p, 1440p, 4K, ultrawide)

## Fair play

PoE Harvest Tracker is built to stay within Grinding Gear Games' rules for third-party tools:
it reads the screen **only when you press a hotkey** (one screenshot per press), **never sends
input** to the game, and **never reads the game's memory**. It reads the game's log file to know
when you're in a map. It isn't made or endorsed by Grinding Gear Games.

## Privacy

Your data never leaves your PC. The app goes online for two things only: lifeforce prices from
[poe.ninja](https://poe.ninja) (about once an hour) and a check for a new version on GitHub (once
a day; can be turned off in Settings). It never installs anything by itself.

## Where things are

| | |
|---|---|
| Your data | `Documents\PoE Harvest` (change in Settings → Data) – database, cut-outs of every read, corrections, `encounters.csv` for Excel, `logs\app.log` |
| Settings | `%APPDATA%\PoE Harvest\settings.json` |
| The app | `%LOCALAPPDATA%\Programs\PoE Harvest Tracker` |

Uninstalling keeps your data.

## Problems?

- **Nothing happens on a key press** – check the tray icon is green (not paused), you're in a map,
  and the game is the active window. If the tray icon is orange, the game's log wasn't found: set
  the Path of Exile folder in Settings.
- **Lifeforce not read** – check the loot filter (above) and use Settings → Game → Test.
- **Something else** – open an [issue](https://github.com/Alyrico/poe-harvest/issues) and attach
  `Documents\PoE Harvest\logs\app.log`.

## Licence

© 2026 Alyrico. All rights reserved. Free to download and use; see [LICENSE.txt](LICENSE.txt).
Bundled third-party components keep their own licences ([THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)).

---

### For the author: building

Every push to `main` builds the installer on GitHub (Actions → Build → the run's *Artifacts*).
To publish a release: set `__version__` in `src/poe_harvest/__init__.py`, update
`docs/release-notes.md`, then push a tag `vX.Y.Z` matching the version.

Run from source (Windows, Python 3.12, Tesseract installed): `pip install -r requirements.txt`,
then `python -m poe_harvest --debug` from the `src` folder. Checks: `python tools/test_app.py`;
reader accuracy on screenshots: `python tools/check_accuracy.py <screenshots folder> [--height 1080]`.
