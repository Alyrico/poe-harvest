"""
Known Sacred Flora modifiers, and turning OCR'd tooltip lines into structured data.

Each mod has a stable id (used in the database) and its in-game wording with '#'
for the number. New wordings found in play are reported as "unknown" so they can be
added here after one confirmation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# id, wording, kind.  Full list from poedb (HarvestTreeMods, 22 entries).
MODS = [
    ("guarded",            "Guarded by Sacred Fauna",                                            "implicit"),
    ("spawn_rate",         "#% increased Sacred Fauna spawn rate",                              "mod"),
    ("spawned",            "#% increased Sacred Fauna spawned",                                 "mod"),
    ("pack_size",          "Sacred Fauna have #% increased Pack Size",                          "mod"),
    ("lifeforce_quantity", "Sacred Fauna drop #% increased Quantity of Lifeforce",              "mod"),
    ("item_rarity",        "Sacred Fauna have #% increased Item Rarity",                        "mod"),
    ("chance_magic",       "Sacred Fauna have #% increased chance to be Magic",                 "mod"),
    ("chance_rare",        "Sacred Fauna have #% increased chance to be Rare",                  "mod"),
    ("experience",         "Sacred Fauna grant #% increased Experience",                        "mod"),
    ("extra_rare_t3",      "Spawns # additional Rare Tier 3 Sacred Fauna",                      "mod"),
    ("extra_magic_t2",     "Spawns # additional Magic Tier 2 Sacred Fauna",                     "mod"),
    ("wisps_on_fauna",     "Sacred Fauna are affected by # additional Wildwood Wisps",          "mod"),
    ("wisp_monsters_after", "#% increased number of Monsters in Area affected by Wildwood Wisps on encounter completion", "mod"),
    ("wisps_released",     "#% increased Wildwood Wisps released on encounter completion",      "mod"),
    ("all_colours",        "Vivid, Wild and Primal Sacred Fauna can spawn",                     "mod"),
    # orange in game, but lifeforce is an item too, so this one doubles the lifeforce: a real mod
    ("items_duplicated",   "Items dropped by Sacred Fauna are Duplicated",                      "mod"),
    ("items_fully_linked", "Items dropped by Sacred Fauna are fully linked",                    "reward"),
    ("items_max_sockets",  "Items dropped by Sacred Fauna have maximum sockets",                 "reward"),
    ("specimen_prompt",    "Interact to begin Sacred Flora encounter",                          "implicit"),
]

# Prefix / suffix (poedb). A rare plant always has 2 of each, rewards included.
PREFIXES = {"spawn_rate", "spawned", "pack_size", "extra_rare_t3", "extra_magic_t2", "all_colours",
            "wisps_released", "wisp_monsters_after"}
SUFFIXES = {"lifeforce_quantity", "item_rarity", "chance_magic", "chance_rare", "experience",
            "wisps_on_fauna", "items_duplicated", "items_fully_linked", "items_max_sockets"}   # + every reward

# Value ranges before atlas bonuses (poedb). Your values are all 1.5x these: Rich Soil
# ("Explicit modifiers on Sacred Flora have 50% increased effect") - a setting, asked on first start.
REFERENCE_EFFECT = 1.5      # the estimate model measures values against Rich-Soil ranges, for everyone


def flora_effect() -> float:
    from . import settings
    return settings.current().flora_mod_effect
BASE_RANGES = {
    "spawn_rate": (40, 80), "spawned": (20, 40), "pack_size": (20, 40), "lifeforce_quantity": (50, 100),
    "item_rarity": (50, 100), "chance_magic": (50, 100), "chance_rare": (50, 100), "experience": (100, 200),
    "extra_rare_t3": (2, 4), "extra_magic_t2": (4, 8), "wisps_on_fauna": (2000, 4000),
    "wisp_monsters_after": (40, 80), "wisps_released": (25, 50),
}
REWARD_RANGES = {"Thread": (1, 2), "Stacked Deck": (1, 2), "Currency Items": (2, 3), "Unique Items": (1, 1)}


def value_range(mid: str, effect: float | None = None):
    """(lowest, highest) value that can appear in game (with Rich Soil if it's allocated)."""
    if mid.startswith("reward:"):
        lo, hi = REWARD_RANGES.get(mid.split(":", 1)[1], (1, 99))
    elif mid in BASE_RANGES:
        lo, hi = BASE_RANGES[mid]
    else:
        return None
    eff = flora_effect() if effect is None else effect
    return int(lo * eff), int(hi * eff + 0.999)


def roll_quality(mid: str, value) -> float | None:
    """0 = lowest possible roll, 1 = highest."""
    r = value_range(mid)
    if r is None or value is None or r[1] == r[0]:
        return None
    return max(0.0, min(1.0, (value - r[0]) / (r[1] - r[0])))


# "Tier 3 Sacred Fauna drop an additional Thread" / "... drop 3 additional Threads".
# Matched loosely: a health bar drawn across the line can garble a word or two.
REWARD_START_RE = re.compile(r"^TIER\s*3\s*SACRED\s*FAUNA\s*DROP\s*(AN|A|\d+)\b\s*(.*)$")
KNOWN_REWARDS = ["Stacked Deck", "Thread", "Currency Items", "Unique Items"]

BASES = ["WILD ROOTS", "VIVID ROOTS", "PRIMAL ROOTS"]
# rare plant names are "<first> <second>"; seen so far:
NAME_FIRST = ["WARPED", "FERTILE", "KNOTTED", "TWISTED", "VERDANT", "SNARLED", "MISSHAPEN", "GNARLED",
              "UNTAMED", "TANGLED", "WEATHERED"]
NAME_SECOND = ["GROWTH", "SHRUB", "TREE", "PLANT", "BRUSH", "OAK", "VEGETATION", "ROOTS"]


def clean_name(raw: str) -> str:
    """Drop OCR junk (e.g. buff icons drawn over the header): keep words that look like name words."""
    words = [w for w in re.findall(r"[A-Z0-9]+", normalise(raw)) if len(w) >= 3 and w.isalpha()]
    known = []
    for w in words:
        score, best = max((SequenceMatcher(None, w, k).ratio(), k) for k in NAME_FIRST + NAME_SECOND)
        if score >= 0.75:
            known.append(best if score >= 0.85 else w)      # a letter dropped by OCR: TWISTE -> TWISTED
    return " ".join(known or words).title()


def normalise(text: str) -> str:
    t = text.upper()
    t = re.sub(r"[‘’`'\"“”|_]", " ", t)
    # letters that OCR sometimes gives inside numbers
    t = re.sub(r"(?<=\d)[O](?=[\d%])|(?<=[\d])O\b", "0", t)
    t = re.sub(r"(?<=\d)[Il](?=[\d%])", "1", t)
    t = re.sub(r"[^A-Z0-9%+, ]", " ", t)
    t = re.sub(r"(?<=[A-Z]),(?=[A-Z])", " ", t)            # SACRED,FAUNA -> SACRED FAUNA
    return re.sub(r"\s+", " ", t).strip()


def skeleton(text: str) -> str:
    """Letters only - spacing and punctuation from OCR don't matter."""
    return re.sub(r"[^A-Z]", "", text.upper())


def numbers_in(text: str) -> list[int]:
    return [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]


_TEMPLATE_SKELETONS = [(mid, wording, kind, skeleton(wording.replace("#", "")), numbers_in(wording.replace("#", "")))
                       for mid, wording, kind in MODS]
_PLACEHOLDERS = {mid: wording.count("#") for mid, wording, _ in MODS}


def _best_template(norm: str):
    """(score, mod id, margin over the runner-up)."""
    sk = skeleton(norm)
    scores = sorted(((SequenceMatcher(None, sk, tsk).ratio(), mid)
                     for mid, wording, kind, tsk, _ in _TEMPLATE_SKELETONS), reverse=True)
    best, second = scores[0], scores[1] if len(scores) > 1 else (0.0, None)
    return best[0], best[1], best[0] - second[0]


def _accept(score, margin):
    """A clean match, or a partly covered line (health bar over a word) that still clearly
    matches one mod and no other."""
    return ((score >= MATCH_OK and margin >= MARGIN_MIN)          # e.g. "...to be" without Magic/Rare
            or (score >= MATCH_LOOSE and margin >= MARGIN_NEEDED))


@dataclass
class ParsedMod:
    id: str
    value: int | None
    text: str
    confidence: float
    kind: str = "mod"


@dataclass
class ParsedTooltip:
    kind: str                 # flora / specimen
    rarity: str
    name: str
    base: str
    colour: str | None        # Wild / Vivid / Primal
    mods: list[ParsedMod] = field(default_factory=list)
    rewards: dict = field(default_factory=dict)   # item -> count
    unknown: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def mod_values(self) -> dict:
        return {m.id: m.value for m in self.mods if m.kind == "mod"}


def _value_for(mid: str, norm: str):
    want = _PLACEHOLDERS[mid]
    if want == 0:
        return None, True
    # digits glued to letters ("WI1SPS", "4NCREASED") are OCR noise, so prefer standalone numbers
    strict = [int(n.replace(",", "")) for n in re.findall(r"(?<![A-Z])\d[\d,]*(?![A-Z])", norm)]
    literals0 = next(t[4] for t in _TEMPLATE_SKELETONS if t[0] == mid)
    for lit in literals0:
        if lit in strict and len(strict) > want:
            strict.remove(lit)
    if len(strict) == want:
        return strict[0], True
    nums = numbers_in(norm)
    literals = next(t[4] for t in _TEMPLATE_SKELETONS if t[0] == mid)
    for lit in literals:                       # drop literal numbers like the 3 in "Tier 3"
        if lit in nums and len(nums) > want:
            nums.remove(lit)
    if len(nums) == want:
        return nums[0], True
    return (nums[0] if nums else None), False


def _parse_reward(norm: str):
    text = norm.replace(",", "")
    # tolerate OCR noise in the fixed start of the line
    head = skeleton(text)[:len("TIERSACREDFAUNADROP")]
    if SequenceMatcher(None, head, "TIERSACREDFAUNADROP").ratio() < 0.85:
        return None
    m = REWARD_START_RE.search(text)
    if m:
        count = 1 if m.group(1) in ("AN", "A") else int(m.group(1))
        rest = m.group(2)
    else:                                   # start too garbled for the pattern: assume one
        count, rest = 1, re.sub(r"^.*?DROP\s*", "", text)
    words = rest.split()
    if words and SequenceMatcher(None, words[0], "ADDITIONAL").ratio() > 0.5:
        words = words[1:]                   # drop the (possibly garbled) "additional"
    item_text = " ".join(words)
    best = max(KNOWN_REWARDS, key=lambda k: SequenceMatcher(None, skeleton(item_text), skeleton(k)).ratio())
    score = SequenceMatcher(None, skeleton(item_text), skeleton(best)).ratio()
    if score < 0.6:
        return item_text.title() or "?", count, False
    return best, count, True


MATCH_OK = 0.86
MATCH_LOOSE = 0.72
MARGIN_NEEDED = 0.08
MARGIN_MIN = 0.03


def parse_tooltip(reading) -> ParsedTooltip:
    """reading: reader.TooltipReading"""
    name = clean_name(reading.name) if reading.kind == "flora" else normalise(reading.name).title()
    base_norm = normalise(reading.base)
    base, colour = "", None
    if reading.kind == "flora":
        if not base_norm:
            # a normal or magic plant has one header line, and the base is in it ("Vivid Roots")
            words = normalise(reading.name).split()
            pairs = [" ".join(words[i:i + 2]) for i in range(max(len(words) - 1, 1))]
            r, base_norm = max(((SequenceMatcher(None, p, x).ratio(), p) for p in pairs for x in BASES),
                               default=(0, ""))
            if reading.rarity == "normal":
                name = base_norm.title()
        r, b = max((SequenceMatcher(None, base_norm, x).ratio(), x) for x in BASES)
        if r > 0.6:
            base = b.title()
            colour = b.split()[0].title()
    else:
        m = re.search(r"(WILD|VIVID|PRIMAL|SACRED)", normalise(reading.name))
        colour = m.group(1).title() if m else None
        base = "Specimen"
    out = ParsedTooltip(reading.kind, reading.rarity, name, base, colour)

    lines = [normalise(l.text) for l in reading.lines]
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line == "?":
            out.problems.append(f"unreadable line {i + 1}")
            i += 1
            continue
        reward = _parse_reward(line)
        if reward:
            item, count, known = reward
            out.rewards[item] = count
            out.mods.append(ParsedMod("reward:" + item, count, line, 1.0 if known else 0.5, "reward"))
            if not known:
                out.unknown.append(line)            # a new reward item: confirm once
            i += 1
            continue
        score, mid, margin = _best_template(line)
        used = 1
        if i + 1 < len(lines):                 # long mods wrap onto a second line
            joined = line + " " + lines[i + 1]
            s2, m2, mg2 = _best_template(joined)
            if s2 > score + 0.03 and _accept(s2, mg2):
                score, mid, margin, line, used = s2, m2, mg2, joined, 2
        if not _accept(score, margin):
            out.unknown.append(line)
            i += used
            continue
        kind = next(k for t, _, k, _, _ in _TEMPLATE_SKELETONS if t == mid)
        value, ok = _value_for(mid, line)
        if not ok:
            out.problems.append(f"number unclear in: {line}")
        out.mods.append(ParsedMod(mid, value, line, round(score, 3), kind))
        if kind == "reward":
            out.rewards[mid] = 1
        i += used

    if reading.kind == "flora" and not base:
        out.problems.append("plant type (Wild/Vivid/Primal Roots) not read")
    if reading.kind == "flora":
        for m in out.mods:
            r = value_range(m.id)
            if r and m.value is not None and not (r[0] - 1 <= m.value <= r[1] + 1):
                out.problems.append(f"{m.value} is outside the possible range {r[0]}-{r[1]}: {m.text.lower()}")
        if out.rarity == "rare" and not out.unknown:
            n_pre = sum(1 for m in out.mods if m.id in PREFIXES)
            n_suf = sum(1 for m in out.mods if m.id in SUFFIXES or m.id.startswith("reward:"))
            if (n_pre, n_suf) != (2, 2):
                out.problems.append(f"read {n_pre} prefixes + {n_suf} suffixes; a rare plant has 2 + 2 - a line was missed?")
    return out
