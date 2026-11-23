"""
Lifeforce estimate.

    lifeforce = base amount for the plant type (a plant with no lifeforce mods)
                x  an effect for each lifeforce mod
                x  the yellow-wisp effect
                x  (1 + Atlas Drops %)  x  (1 + lifeforce quantity from atlas passives %)
                x  a white-map effect (maps without map mods)

fitted on the log scale (effects multiply).

Mods that do nothing for lifeforce (agreed 22 Nov from game knowledge, and the data agreed): more
experience, item rarity, Wildwood wisps released, monsters affected by wisps, and the item-reward
lines (Stacked Decks, Threads, Currency, Uniques, fully linked, max sockets) are fixed at no effect:
they're empty slots as far as lifeforce goes. So a plant's effects are measured against a plant
with no lifeforce mods, and every plant with no-effect mods helps pin that base down. The other
mods are decided by the data.

Every effect is pulled gently towards a starting value so a mod seen on only a few plants can't get
a wild number:
  * "own" mode (enough plants of your own): ridge regression towards zero - except Duplicated
    (towards x2: it drops every stack twice), Atlas Drops and atlas-passive quantity (towards a
    straight-line increase) and white maps (towards no difference). How strong the pull is,
    whether a mod's effect grows with its rolled value, and whether yellow wisps help in a straight
    line or with diminishing returns are chosen by leave-one-out testing.
  * "starter" mode (new players): the author's fitted model is the starting point, and your own
    plants move it - the base amounts quickly (every setup differs), the mod effects slowly.

Pure numpy; fitting 50-500 plants takes a few milliseconds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import mods as modlib

TYPES = ("Wild", "Vivid", "Primal")
Z80 = 1.2816                    # 80% range
MEANINGFUL = 0.03               # an effect under 3% is shown as ~
LAMBDAS = (0.3, 1, 3, 10, 30, 100)
MIN_PLANTS = 15                 # below this there's nothing to fit on your own plants
SHOW_FROM = 30                  # own-mode estimates need this many plants AND must beat the type average
ROUGH_UNTIL = 100               # ...and are marked "rough" until here
STARTER_UNTIL = 100             # "auto": starter estimates until you have this many plants of your own
STARTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "starter_model.json")

# no effect on lifeforce (fixed at zero)
NO_EFFECT = {"experience", "item_rarity", "wisps_released", "wisp_monsters_after",
             "items_fully_linked", "items_max_sockets"}            # + every item-reward line
MOD_IDS = [mid for mid, _, kind in modlib.MODS if kind == "mod"]
LF_MODS = [mid for mid in MOD_IDS if mid not in NO_EFFECT]          # mods that may change lifeforce

WISPS, ATLAS, TREE, WHITE = "yellow_wisps", "atlas_drops", "tree_lifeforce", "white_map"
LF_DUP, FAUNA_DUP = "lifeforce_dup", "fauna_dup"
# The map setup, saved with every plant: (feature, key in a plant / setup dict, kind, prior, prior sd).
#   pct:  a percentage, as log(1 + pct/100) - a coefficient of 1 = lifeforce grows in step with it
#   flag: on / off
# Duplicate chances are exact on average (x(1 + chance)), so their prior sd is tiny: fixed, not fitted.
CONDITION_SPEC = [
    (ATLAS, "atlas", "pct", 1.0, 0.25),
    (TREE, "tree", "pct", 1.0, 0.25),
    (LF_DUP, "lf_dup", "pct", 1.0, 0.02),        # "Lifeforce dropped by Sacred Fauna has X% chance to be Duplicated"
    (FAUNA_DUP, "fauna_dup", "pct", 1.0, 0.02),  # "Sacred Fauna have X% chance to be duplicated"
    (WHITE, "white", "flag", 0.0, 0.5),
]
COND_KEY = {name: key for name, key, _, _, _ in CONDITION_SPEC}
# Known or assumed from how the game works, until the data says otherwise:
PRIOR = {"items_duplicated": math.log(2), **{name: prior for name, _, _, prior, _ in CONDITION_SPEC}}
PRIOR_SD = {name: sd for name, _, _, _, sd in CONDITION_SPEC}   # how sure we are of those before testing them


def typical_value(mid: str):
    """The middle of the mod's range WITH Rich Soil - the same yardstick for every player, so a
    player without Rich Soil (smaller numbers) gets proportionally smaller effects."""
    r = modlib.value_range(mid, effect=modlib.REFERENCE_EFFECT)
    return (r[0] + r[1]) / 2 if r else None


def has_effect(mid: str) -> bool:
    return mid in LF_MODS


# ---------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------
def map_wisps(con) -> dict:
    """The wisp counter doesn't change within a map, so each map gets the reading seen most often
    across all its presses (unreadable presses don't vote). {map_id: {"yellow": n or None, ...}}"""
    votes: dict = {}
    for r in con.execute("""SELECT map_id, wisps_yellow_node, wisps_blue_node, wisps_purple_node,
                                   wisps_yellow_result, wisps_blue_result, wisps_purple_result
                            FROM encounters WHERE map_id IS NOT NULL"""):
        mv = votes.setdefault(r[0], {"yellow": Counter(), "blue": Counter(), "purple": Counter()})
        for i, colour in enumerate(("yellow", "blue", "purple")):
            for v in (r[1 + i], r[4 + i]):
                if v is not None:
                    mv[colour][v] += 1
    out = {}
    for mid, mv in votes.items():
        out[mid] = {c: (max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0] if cnt else None) for c, cnt in mv.items()}
    return out


def rolls(con) -> list[dict]:
    """Every distinct plant roll you've seen (first rolls and rerolls, read cleanly) - an unbiased
    sample of what a fresh roll looks like, used for the reroll advice. Rare plants only."""
    seen, out = set(), []
    for eid, parsed in con.execute("""SELECT r.encounter_id, r.parsed FROM reads r JOIN encounters e ON e.id = r.encounter_id
                                      WHERE r.key = 'node' AND r.ok = 1
                                      AND e.status NOT IN ('dropped', 'excluded', 'pending')"""):
        try:
            p = json.loads(parsed or "{}")
        except ValueError:
            continue
        if p.get("type") not in TYPES or not p.get("mods") or p.get("rarity", "rare") != "rare":
            continue
        key = (eid, json.dumps(p["mods"], sort_keys=True), json.dumps(p.get("rewards", {}), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": eid, "type": p["type"], "mods": p["mods"], "rewards": p.get("rewards", {})})
    return out


CONDITION_COLUMNS = {"atlas": "atlas_drops", "tree": "tree_lifeforce", "lf_dup": "lifeforce_dup",
                     "fauna_dup": "fauna_dup", "white": "white_map", "level": "area_level"}


def plants(con, only_ok=True, defaults=None) -> list[dict]:
    """Completed flora encounters as plain dicts. `defaults` fill in conditions that weren't recorded
    (plants from before the app recorded them): {"atlas": 82, "tree": 0, "white": False}."""
    defaults = defaults or {}
    wisps = map_wisps(con)
    where = "status = 'ok' AND kind = 'flora' AND lifeforce_total > 0" if only_ok else "kind = 'flora'"
    cols = {r[1] for r in con.execute("PRAGMA table_info(encounters)")}
    cond_sql = ", ".join(c if c in cols else "NULL" for c in CONDITION_COLUMNS.values())
    out = []
    for e in con.execute(f"SELECT id, map_id, plant_type, lifeforce_total, rerolls, {cond_sql} FROM encounters "
                         f"WHERE {where}"):
        if e[2] not in TYPES:
            continue
        mods = {r[0]: r[1] for r in con.execute("SELECT mod_id, value FROM encounter_mods WHERE encounter_id = ?", (e[0],))}
        rewards = {r[0]: r[1] for r in con.execute("SELECT item, count FROM encounter_rewards WHERE encounter_id = ?", (e[0],))}
        p = {"id": e[0], "map_id": e[1], "type": e[2], "lifeforce": e[3], "rerolls": e[4] or 0,
             "mods": mods, "rewards": rewards, "yellow": (wisps.get(e[1]) or {}).get("yellow")}
        for i, key in enumerate(CONDITION_COLUMNS):
            v = e[5 + i]
            p[key] = v if v is not None else defaults.get(key)
        out.append(p)
    return out


def fingerprint(con) -> set:
    """Short hashes of the result screenshots - lets the app tell that a database is the one the
    starter model came from (so the author doesn't count his plants twice)."""
    out = set()
    for (shot,) in con.execute("SELECT screenshot FROM reads WHERE key = 'result' AND screenshot != ''"):
        out.add(hashlib.sha1(os.path.basename(shot).encode()).hexdigest()[:10])
    return out


# ---------------------------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------------------------
def _wisp_feature(yellow, curve):
    y = (yellow or 0) / 1000
    return y if curve == "straight" else math.log1p(y)


def pct_feature(pct) -> float:
    return math.log1p((pct or 0) / 100)


def feature_names() -> list[str]:
    return list(TYPES) + LF_MODS + [WISPS] + [name for name, *_ in CONDITION_SPEC]


K = len(TYPES)
I_WISP = K + len(LF_MODS)
I_COND = {name: I_WISP + 1 + i for i, (name, *_) in enumerate(CONDITION_SPEC)}
I_ATLAS, I_TREE, I_WHITE = I_COND[ATLAS], I_COND[TREE], I_COND[WHITE]
N_FEATURES = I_WISP + 1 + len(CONDITION_SPEC)
CONDITIONS = tuple(I_COND.values())


def _set_conditions(v: np.ndarray, cond: dict):
    for name, key, kind, _, _ in CONDITION_SPEC:
        v[I_COND[name]] = (1.0 if cond.get(key) else 0.0) if kind == "flag" else pct_feature(cond.get(key))


def features(plant: dict, scaled: bool, curve: str) -> np.ndarray:
    x = np.zeros(N_FEATURES)
    x[TYPES.index(plant["type"])] = 1
    for j, mid in enumerate(LF_MODS):
        if mid in plant["mods"]:
            v, t = plant["mods"][mid], typical_value(mid)
            x[K + j] = (v / t) if (scaled and v is not None and t) else 1.0
    x[I_WISP] = _wisp_feature(plant.get("yellow"), curve)
    _set_conditions(x, plant)
    return x


def mods_mask() -> np.ndarray:
    m = np.zeros(N_FEATURES)
    m[K:I_WISP] = 1
    return m


def condition_vector(cond: dict | None) -> np.ndarray:
    v = np.zeros(N_FEATURES)
    _set_conditions(v, cond or {})
    return v


# ---------------------------------------------------------------------------------------------
# the fitted model
# ---------------------------------------------------------------------------------------------
@dataclass
class Model:
    n: int                                  # your own plants used
    scaled: bool
    curve: str
    lam: float
    beta: np.ndarray
    cov: np.ndarray                         # covariance of beta
    sigma: float                            # spread of single plants around the estimate (log scale)
    mode: str = "own"                       # "own" or "starter"
    n_starter: int = 0                      # plants behind the starter model (starter mode)
    loo_pred: dict = field(default_factory=dict)      # encounter id -> leave-one-out estimate
    loo_abs_err: float | None = None        # median |error| of leave-one-out estimates (fraction)
    base_abs_err: float | None = None       # same for "type average" (no mods)
    coverage: float | None = None           # share of plants inside their 80% range (leave-one-out)
    counts: dict = field(default_factory=dict)   # plants with yellow wisps / white maps / Atlas levels...

    def _part(self, v):
        m, s = float(v @ self.beta), math.sqrt(max(float(v @ self.cov @ v), 0))
        return m, s

    def _rng(self, v):
        m, s = self._part(v)
        return math.expm1(m), math.expm1(m - Z80 * s), math.expm1(m + Z80 * s)

    def base(self, ptype: str, cond: dict | None = None) -> float:
        """Lifeforce from a plant of this type with no lifeforce mods (and no yellow wisps)."""
        v = condition_vector(cond)
        v[TYPES.index(ptype)] = 1
        return math.exp(float(v @ self.beta))

    def mod_effect(self, mid: str, value=None):
        """(effect, low, high) of this mod against no mod, 80% range; None if it has no effect."""
        if mid not in LF_MODS:
            return None
        v = np.zeros_like(self.beta)
        t = typical_value(mid)
        v[K + LF_MODS.index(mid)] = (value / t) if (self.scaled and value is not None and t) else 1.0
        return self._rng(v)

    def wisp_effect(self, yellow):
        v = np.zeros_like(self.beta)
        v[I_WISP] = _wisp_feature(yellow, self.curve)
        return self._rng(v)

    def condition_effect(self, which: str, value):
        """Lifeforce at this Atlas Drops % / atlas-passive quantity % / white map, vs 0% / normal map."""
        v = np.zeros_like(self.beta)
        _set_conditions(v, {COND_KEY[which]: value})
        return self._rng(v)

    def per_step(self, mid: str):
        """(step, change in lifeforce per step of the mod's value) - e.g. (10, 0.04) = +4% per 10
        points. Only when the chosen model lets effects grow with the rolled value."""
        t, r = typical_value(mid), modlib.value_range(mid, effect=modlib.REFERENCE_EFFECT)
        if not (self.scaled and t and mid in LF_MODS):
            return None
        step = 1 if r[1] <= 20 else 10 if r[1] <= 400 else 1000
        return step, math.expm1(self.beta[K + LF_MODS.index(mid)] * step / t)

    def mod_part(self, plant: dict) -> float:
        """log-scale effect of the plant's lifeforce mods together (0 = no lifeforce mods)."""
        return float((features(plant, self.scaled, self.curve) * mods_mask()) @ self.beta)

    def estimate(self, plant: dict):
        """(estimate, low, high) for one plant: the typical outcome and an 80% range."""
        x = features(plant, self.scaled, self.curve)
        m = float(x @ self.beta)
        s = math.sqrt(max(float(x @ self.cov @ x), 0) + self.sigma ** 2)
        return math.exp(m), math.exp(m - Z80 * s), math.exp(m + Z80 * s)

    def icon(self, effect, lo, hi, override="auto"):
        if override in ("good", "bad", "neutral"):
            return override
        if effect >= MEANINGFUL and lo > 0:
            return "good"
        if effect <= -MEANINGFUL and hi < 0:
            return "bad"
        return "neutral"

    @property
    def usable(self) -> bool:
        if self.mode == "starter":
            return True
        return self.n >= SHOW_FROM and self.loo_abs_err is not None and self.loo_abs_err < self.base_abs_err

    @property
    def rough(self) -> bool:
        return self.n < ROUGH_UNTIL

    def summary(self, cond=None) -> dict:
        return {"n": self.n, "mode": self.mode, "n_starter": self.n_starter, "scaled": self.scaled,
                "curve": self.curve, "lambda": self.lam, "sigma": self.sigma,
                "typical_error": self.loo_abs_err, "baseline_error": self.base_abs_err,
                "coverage": self.coverage, **self.counts,
                "coef": {name: float(self.beta[i]) for name, i in I_COND.items()},
                "base": {t: self.base(t, cond) for t in TYPES}}

    def to_json(self, extra=None) -> dict:
        d = {"n": self.n, "scaled": self.scaled, "curve": self.curve, "lam": self.lam,
             "beta": self.beta.tolist(), "cov": self.cov.tolist(), "sigma": self.sigma,
             "features": feature_names(), "no_effect": sorted(NO_EFFECT)}
        d.update(extra or {})
        return d


# ---------------------------------------------------------------------------------------------
# fitting on your own plants
# ---------------------------------------------------------------------------------------------
def _accuracy(data, X, y, loo, cov, sigma2):
    """Leave-one-out: how far off, compared with the type average, and how often inside the range."""
    n = len(data)
    loo_log = y - loo
    abs_err = np.abs(np.expm1(loo_log - y))
    base = np.empty(n)
    for i, p in enumerate(data):
        others = [y[j] for j in range(n) if j != i and data[j]["type"] == p["type"]]
        base[i] = np.mean(others) if others else np.mean(np.delete(y, i))
    base_err = np.abs(np.expm1(base - y))
    s_loo = np.sqrt(np.einsum("ij,jk,ik->i", X, cov, X) + sigma2)
    inside = np.abs(loo) <= Z80 * s_loo
    return loo_log, float(np.median(abs_err)), float(np.median(base_err)), float(np.mean(inside))


def _clean(data):
    return [p for p in data if p["lifeforce"] and p["lifeforce"] > 0 and p["type"] in TYPES]


def _counts(data):
    return {"has_yellow": sum(1 for p in data if p.get("yellow")),
            "white_plants": sum(1 for p in data if p.get("white")),
            "atlas_levels": len({round(p.get("atlas") or 0) for p in data}),
            "tree_levels": len({round(p.get("tree") or 0) for p in data}),
            "no_lf_mod_plants": sum(1 for p in data if not any(m in LF_MODS for m in p["mods"]))}


def fit(data: list[dict]) -> Model | None:
    """Your own plants only: fit every variant, keep the one with the best leave-one-out predictions."""
    data = _clean(data)
    if len(data) < MIN_PLANTS:
        return None
    y = np.log([p["lifeforce"] for p in data])
    names = feature_names()
    prior = np.array([PRIOR.get(nm, 0.0) for nm in names])
    best = None
    for scaled in (True, False):
        for curve in ("straight", "diminishing"):
            X = np.array([features(p, scaled, curve) for p in data])
            for lam in LAMBDAS:
                # penalty per feature: none for the base amounts, lam for mods and wisps, and a fixed
                # strength (whatever lam is) for the conditions, from their prior sd
                pen = np.array([0.0] * K + [lam] * (I_WISP + 1 - K) + [0.0] * len(CONDITIONS))
                for nm, i in I_COND.items():
                    pen[i] = 0.06 / PRIOR_SD[nm] ** 2
                A = X.T @ X + np.diag(pen)
                A_inv = np.linalg.pinv(A)
                beta = A_inv @ (X.T @ y + pen * prior)
                H = X @ A_inv @ X.T
                resid = y - X @ beta
                h = np.clip(np.diag(H), 0, 0.999)
                loo = resid / (1 - h)                       # exact leave-one-out residuals for ridge
                score = float(np.mean(loo ** 2))
                if best is None or score < best[0]:
                    best = (score, scaled, curve, lam, X, beta, A_inv, resid, loo, float(np.trace(H)))
    score, scaled, curve, lam, X, beta, A_inv, resid, loo, df = best
    n = len(data)
    sigma2 = float(resid @ resid) / max(n - df, 1)
    cov = sigma2 * A_inv
    loo_log, err, base_err, coverage = _accuracy(data, X, y, loo, cov, sigma2)
    return Model(n=n, scaled=scaled, curve=curve, lam=lam, beta=beta, cov=cov, sigma=math.sqrt(sigma2),
                 loo_pred={p["id"]: float(math.exp(v)) for p, v in zip(data, loo_log)},
                 loo_abs_err=err, base_abs_err=base_err, coverage=coverage, counts=_counts(data))


# ---------------------------------------------------------------------------------------------
# fitting with the starter model as the starting point
# ---------------------------------------------------------------------------------------------
def load_starter(path: str = STARTER_PATH) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("features") != feature_names():
            return None                       # built for a different set of mods: not usable
        return s
    except (OSError, ValueError):
        return None


def fit_with_starter(data: list[dict], starter: dict) -> Model:
    """Bayesian update: the starter's effects (with their uncertainty, widened) are the prior,
    your plants the evidence. The base amounts are allowed to move a lot (every player's atlas and
    scarabs differ); the mod effects need more plants to move."""
    data = _clean(data)
    scaled, curve = starter["scaled"], starter["curve"]
    mu = np.array(starter["beta"])
    S = np.array(starter["cov"]) * 4.0                     # widened: other players differ
    for i in range(K):
        S[i, i] += 0.3 ** 2                                 # base amounts: +-30% is quite possible
    for nm, i in I_COND.items():
        S[i, :] = S[:, i] = 0
        S[i, i] = PRIOR_SD[nm] ** 2
    P = np.linalg.pinv(S)
    n = len(data)
    sigma2 = starter["sigma"] ** 2
    if n:
        X = np.array([features(p, scaled, curve) for p in data])
        y = np.log([p["lifeforce"] for p in data])
    else:
        X, y = np.zeros((0, N_FEATURES)), np.zeros(0)
    for _ in range(2):                                      # sigma from the starter, then updated by your plants
        A = X.T @ X / sigma2 + P
        cov = np.linalg.pinv(A)
        beta = cov @ (X.T @ y / sigma2 + P @ mu)
        if n >= SHOW_FROM:
            resid = y - X @ beta
            sigma2 = (10 * starter["sigma"] ** 2 + float(resid @ resid)) / (10 + n)
    m = Model(n=n, scaled=scaled, curve=curve, lam=starter.get("lam", 0), beta=beta, cov=cov,
              sigma=math.sqrt(sigma2), mode="starter", n_starter=starter.get("n", 0), counts=_counts(data))
    if n >= 10:
        H = X @ cov @ X.T / sigma2
        resid = y - X @ beta
        loo = resid / (1 - np.clip(np.diag(H), 0, 0.999))
        loo_log, m.loo_abs_err, m.base_abs_err, m.coverage = _accuracy(data, X, y, loo, cov, sigma2)
        m.loo_pred = {p["id"]: float(math.exp(v)) for p, v in zip(data, loo_log)}
    return m


def choose(con, mode: str = "auto", defaults=None, starter: dict | None = None) -> Model | None:
    """The model the app uses. mode: "auto" (starter until you have enough plants of your own, and
    never on the database the starter came from), "on", "off"."""
    data = plants(con, defaults=defaults)
    starter = starter if starter is not None else load_starter()
    use_starter = False
    if starter and mode == "on":
        use_starter = True
    elif starter and mode == "auto" and len(data) < STARTER_UNTIL:
        mine = fingerprint(con)
        theirs = set(starter.get("fingerprint", []))
        use_starter = not (theirs and len(mine & theirs) >= 0.5 * len(theirs))
    if use_starter:
        return fit_with_starter(data, starter)
    return fit(data)


# ---------------------------------------------------------------------------------------------
# Stacked Decks (and other item rewards): predicted from the mods, never read from the screen
# ---------------------------------------------------------------------------------------------
def tier3_count(mods: dict, base_t3):
    """Tier 3 Sacred Fauna the plant spawns: your counted base + "Spawns N additional Rare Tier 3"."""
    if base_t3 is None:
        return None
    return base_t3 + (mods.get("extra_rare_t3") or 0)


def reward_counts(plant: dict, base_t3) -> dict:
    """Expected items per reward line, e.g. {"Stacked Deck": 18}. None for a count when the Tier 3
    base hasn't been set. Duplicated doubles them."""
    t3 = tier3_count(plant["mods"], base_t3)
    dup = 2 if "items_duplicated" in plant["mods"] else 1
    return {item: (None if t3 is None else per_t3 * t3 * dup) for item, per_t3 in plant.get("rewards", {}).items()
            if item in modlib.KNOWN_REWARDS}
