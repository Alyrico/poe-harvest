"""
Lifeforce estimate.

    lifeforce = typical amount for the plant type  x  an effect for each mod  x  the yellow-wisp effect
                x  (1 + Atlas Drops %)

fitted on the log scale (effects multiply). Every effect is pulled gently towards a starting
value so a mod seen on only a few plants can't get a wild number:

  * "own" mode (enough plants of your own): ridge regression towards zero - except Duplicated
    (towards x2: it drops every stack twice) and Atlas Drops (towards a straight-line increase).
    How strong the pull is, whether a mod's effect grows with its rolled value, and whether yellow
    wisps help in a straight line or with diminishing returns are chosen by leave-one-out testing.
  * "starter" mode (new players): the author's fitted model is the starting point, and your own
    plants move it - the type amounts quickly (every setup differs), the mod effects slowly.

Effects are shown relative to an *average mod*: a rare plant always has 4 mods, so what matters is
whether this mod is better or worse than whatever would have been in its place. The "typical"
amount per type is a plant with 4 average mods and no yellow wisps, at your Atlas Drops %.

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
SLOTS = 4                       # a rare plant: 2 prefixes + 2 suffixes
Z80 = 1.2816                    # 80% range
MEANINGFUL = 0.03               # an effect under 3% is shown as ~
LAMBDAS = (0.3, 1, 3, 10, 30, 100)
MIN_PLANTS = 15                 # below this there's nothing to fit on your own plants
SHOW_FROM = 30                  # own-mode estimates need this many plants AND must beat the type average
ROUGH_UNTIL = 100               # ...and are marked "rough" until here
STARTER_UNTIL = 100             # "auto": starter estimates until you have this many plants of your own
REWARD = "item_reward"          # all "Tier 3 drop additional ..." / linked / sockets lines share one effect
ATLAS = "atlas_drops"
# Known from how the game works: Duplicated drops every lifeforce stack twice. Atlas Drops: assumed to
# raise lifeforce in a straight line (x1.82 at 82%) until there's data from different % to test it.
PRIOR = {"items_duplicated": math.log(2), ATLAS: 1.0}
ATLAS_PRIOR_SD = 0.25           # how sure we are of the straight line before testing it
STARTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "starter_model.json")

MOD_IDS = [mid for mid, _, kind in modlib.MODS if kind == "mod"]
REWARD_IDS = [mid for mid, _, kind in modlib.MODS if kind == "reward"]   # fully linked, max sockets


def typical_value(mid: str):
    """The middle of the mod's range WITH Rich Soil - the same yardstick for every player, so a
    player without Rich Soil (smaller numbers) gets proportionally smaller effects."""
    r = modlib.value_range(mid, effect=modlib.REFERENCE_EFFECT)
    return (r[0] + r[1]) / 2 if r else None


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
    sample of what a fresh roll looks like, used for the reroll advice."""
    seen, out = set(), []
    for eid, parsed in con.execute("""SELECT r.encounter_id, r.parsed FROM reads r JOIN encounters e ON e.id = r.encounter_id
                                      WHERE r.key = 'node' AND r.ok = 1
                                      AND e.status NOT IN ('dropped', 'excluded', 'pending')"""):
        try:
            p = json.loads(parsed or "{}")
        except ValueError:
            continue
        if p.get("type") not in TYPES or not p.get("mods"):
            continue
        key = (eid, json.dumps(p["mods"], sort_keys=True), json.dumps(p.get("rewards", {}), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": eid, "type": p["type"], "mods": p["mods"], "rewards": p.get("rewards", {})})
    return out


def plants(con, only_ok=True, default_atlas=None) -> list[dict]:
    """Completed flora encounters as plain dicts."""
    wisps = map_wisps(con)
    where = "status = 'ok' AND kind = 'flora' AND lifeforce_total > 0" if only_ok else "kind = 'flora'"
    cols = {r[1] for r in con.execute("PRAGMA table_info(encounters)")}
    atlas_col = "atlas_drops" if "atlas_drops" in cols else "NULL"
    out = []
    for e in con.execute(f"SELECT id, map_id, plant_type, lifeforce_total, rerolls, {atlas_col} FROM encounters "
                         f"WHERE {where}"):
        if e[2] not in TYPES:
            continue
        mods = {r[0]: r[1] for r in con.execute("SELECT mod_id, value FROM encounter_mods WHERE encounter_id = ?", (e[0],))}
        rewards = {r[0]: r[1] for r in con.execute("SELECT item, count FROM encounter_rewards WHERE encounter_id = ?", (e[0],))}
        out.append({"id": e[0], "map_id": e[1], "type": e[2], "lifeforce": e[3], "rerolls": e[4] or 0,
                    "mods": mods, "rewards": rewards, "yellow": (wisps.get(e[1]) or {}).get("yellow"),
                    "atlas": e[5] if e[5] is not None else default_atlas})
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


def atlas_feature(atlas) -> float:
    return math.log1p((atlas or 0) / 100)


def feature_names() -> list[str]:
    return list(TYPES) + MOD_IDS + [REWARD, "yellow_wisps", ATLAS]


K = len(TYPES)
I_REWARD = K + len(MOD_IDS)
I_WISP = I_REWARD + 1
I_ATLAS = I_WISP + 1
N_FEATURES = I_ATLAS + 1


def features(plant: dict, scaled: bool, curve: str) -> np.ndarray:
    x = np.zeros(N_FEATURES)
    x[TYPES.index(plant["type"])] = 1
    for j, mid in enumerate(MOD_IDS):
        if mid in plant["mods"]:
            v, t = plant["mods"][mid], typical_value(mid)
            x[K + j] = (v / t) if (scaled and v is not None and t) else 1.0
    x[I_REWARD] = sum(1 for mid in plant["mods"] if mid in REWARD_IDS) + len(plant.get("rewards", {}))
    x[I_WISP] = _wisp_feature(plant.get("yellow"), curve)
    x[I_ATLAS] = atlas_feature(plant.get("atlas"))
    return x


def slot_count(plant: dict) -> int:
    return len(plant["mods"]) + len(plant.get("rewards", {}))


def mod_mask() -> np.ndarray:
    """1 for the mod and item-reward features (the parts that make up "the average mod")."""
    m = np.zeros(N_FEATURES)
    m[K:I_WISP] = 1
    return m


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
    g: np.ndarray                           # average mod's contribution, as a linear combination of beta
    mode: str = "own"                       # "own" or "starter"
    n_starter: int = 0                      # plants behind the starter model (starter mode)
    loo_pred: dict = field(default_factory=dict)      # encounter id -> leave-one-out estimate
    loo_abs_err: float | None = None        # median |error| of leave-one-out estimates (fraction)
    base_abs_err: float | None = None       # same for "type average" (no mods)
    coverage: float | None = None           # share of plants inside their 80% range (leave-one-out)
    has_yellow: int = 0                     # plants with yellow wisps in the data
    atlas_levels: int = 0                   # different Atlas Drops values in your data

    # ---- pieces for the popup / dashboard
    def _idx(self, mid):
        return K + MOD_IDS.index(mid)

    def _part(self, v):
        m, s = float(v @ self.beta), math.sqrt(max(float(v @ self.cov @ v), 0))
        return m, s

    def typical(self, ptype: str, atlas=0) -> float:
        """Typical lifeforce for a plant of this type with 4 average mods and no yellow wisps."""
        return math.exp(self.beta[TYPES.index(ptype)] + SLOTS * float(self.g @ self.beta)
                        + self.beta[I_ATLAS] * atlas_feature(atlas))

    def mod_effect(self, mid: str, value=None):
        """(effect as a fraction, low, high) of this mod vs an average mod, 80% range.
        Any id that isn't a lifeforce mod is an item-reward line (Stacked Deck, fully linked...)."""
        v = np.zeros_like(self.beta)
        if mid in MOD_IDS:
            t = typical_value(mid)
            v[self._idx(mid)] = (value / t) if (self.scaled and value is not None and t) else 1.0
        else:
            v[I_REWARD] = 1.0
        m, s = self._part(v - self.g)
        return math.expm1(m), math.expm1(m - Z80 * s), math.expm1(m + Z80 * s)

    def wisp_effect(self, yellow):
        v = np.zeros_like(self.beta)
        v[I_WISP] = _wisp_feature(yellow, self.curve)
        m, s = self._part(v)
        return math.expm1(m), math.expm1(m - Z80 * s), math.expm1(m + Z80 * s)

    def atlas_effect(self, atlas):
        """Lifeforce at this Atlas Drops % compared with 0%."""
        v = np.zeros_like(self.beta)
        v[I_ATLAS] = atlas_feature(atlas)
        m, s = self._part(v)
        return math.expm1(m), math.expm1(m - Z80 * s), math.expm1(m + Z80 * s)

    def per_step(self, mid: str):
        """(step, change in lifeforce per step of the mod's value) - e.g. (10, 0.04) = +4% per 10
        points. Only when the chosen model lets effects grow with the rolled value."""
        t, r = typical_value(mid), modlib.value_range(mid, effect=modlib.REFERENCE_EFFECT)
        if not (self.scaled and t and mid in MOD_IDS):
            return None
        step = 1 if r[1] <= 20 else 10 if r[1] <= 400 else 1000
        return step, math.expm1(self.beta[self._idx(mid)] * step / t)

    def mod_part(self, plant: dict) -> float:
        """log-scale effect of the plant's mods together, vs 4 average mods."""
        x = features(plant, self.scaled, self.curve) * mod_mask()
        return float(x @ self.beta) - SLOTS * float(self.g @ self.beta)

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

    def summary(self, atlas=0) -> dict:
        return {"n": self.n, "mode": self.mode, "n_starter": self.n_starter, "scaled": self.scaled,
                "curve": self.curve, "lambda": self.lam, "sigma": self.sigma,
                "typical_error": self.loo_abs_err, "baseline_error": self.base_abs_err,
                "coverage": self.coverage, "has_yellow": self.has_yellow, "atlas_levels": self.atlas_levels,
                "atlas_coef": float(self.beta[I_ATLAS]),
                "typical": {t: self.typical(t, atlas) for t in TYPES}}

    def to_json(self, extra=None) -> dict:
        d = {"n": self.n, "scaled": self.scaled, "curve": self.curve, "lam": self.lam,
             "beta": self.beta.tolist(), "cov": self.cov.tolist(), "sigma": self.sigma, "g": self.g.tolist(),
             "features": feature_names()}
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


def fit(data: list[dict]) -> Model | None:
    """Your own plants only: fit every variant, keep the one with the best leave-one-out predictions."""
    data = _clean(data)
    if len(data) < MIN_PLANTS:
        return None
    y = np.log([p["lifeforce"] for p in data])
    names = feature_names()
    penal = np.array([0.0] * K + [1.0] * (len(names) - K))
    prior = np.array([PRIOR.get(nm, 0.0) for nm in names])
    best = None
    for scaled in (True, False):
        for curve in ("straight", "diminishing"):
            X = np.array([features(p, scaled, curve) for p in data])
            for lam in LAMBDAS:
                pen = penal.copy()
                pen[I_ATLAS] = 0.06 / ATLAS_PRIOR_SD ** 2 / lam      # fixed strength, whatever lam is
                A = X.T @ X + lam * np.diag(pen)
                A_inv = np.linalg.pinv(A)
                beta = A_inv @ (X.T @ y + lam * pen * prior)
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
    slots = sum(slot_count(p) for p in data) or 1
    g = (X * mod_mask()).sum(axis=0) / slots
    loo_log, err, base_err, coverage = _accuracy(data, X, y, loo, cov, sigma2)
    return Model(n=n, scaled=scaled, curve=curve, lam=lam, beta=beta, cov=cov, sigma=math.sqrt(sigma2), g=g,
                 loo_pred={p["id"]: float(math.exp(v)) for p, v in zip(data, loo_log)},
                 loo_abs_err=err, base_abs_err=base_err, coverage=coverage,
                 has_yellow=sum(1 for p in data if p.get("yellow")),
                 atlas_levels=len({round(p.get("atlas") or 0) for p in data}))


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
    your plants the evidence. The type amounts are allowed to move a lot (every player's atlas and
    scarabs differ); the mod effects need more plants to move."""
    data = _clean(data)
    scaled, curve = starter["scaled"], starter["curve"]
    mu = np.array(starter["beta"])
    S = np.array(starter["cov"]) * 4.0                     # widened: other players differ
    for i in range(K):
        S[i, i] += 0.3 ** 2                                 # type amounts: +-30% is quite possible
    S[I_ATLAS, :] = S[:, I_ATLAS] = 0
    S[I_ATLAS, I_ATLAS] = ATLAS_PRIOR_SD ** 2
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
    g = np.array(starter["g"])
    if n >= SHOW_FROM:
        slots = sum(slot_count(p) for p in data) or 1
        g = (X * mod_mask()).sum(axis=0) / slots
    m = Model(n=n, scaled=scaled, curve=curve, lam=starter.get("lam", 0), beta=beta, cov=cov,
              sigma=math.sqrt(sigma2), g=g, mode="starter", n_starter=starter.get("n", 0),
              has_yellow=sum(1 for p in data if p.get("yellow")),
              atlas_levels=len({round(p.get("atlas") or 0) for p in data}))
    if n >= 10:
        H = X @ cov @ X.T / sigma2
        resid = y - X @ beta
        loo = resid / (1 - np.clip(np.diag(H), 0, 0.999))
        loo_log, m.loo_abs_err, m.base_abs_err, m.coverage = _accuracy(data, X, y, loo, cov, sigma2)
        m.loo_pred = {p["id"]: float(math.exp(v)) for p, v in zip(data, loo_log)}
    return m


def choose(con, mode: str = "auto", default_atlas=None, starter: dict | None = None) -> Model | None:
    """The model the app uses. mode: "auto" (starter until you have enough plants of your own, and
    never on the database the starter came from), "on", "off"."""
    data = plants(con, default_atlas=default_atlas)
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


def fit_from_db(con, default_atlas=None) -> Model | None:
    return fit(plants(con, default_atlas=default_atlas))
