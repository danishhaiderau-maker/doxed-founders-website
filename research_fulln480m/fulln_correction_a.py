"""C2 FALLBACK A - two-stage correction at full N with the ELIGIBLE-ONLY audit frame (batch B2).

Binding: Coder plan r3 33f3ec1e0e07 s7 C2 / s12 B2; GS card r3 0173cd11a090 s5.2 (literal) and s5.1
(block statistic, K_c >= 30); stop spec r3 76dc076cd771 S1.3 rules 1-4; RK c07fa806dfad (4) ("t >= 3.0"
STOP dropped; disclosure column stays); RK 6bf51f6bf4ac (b) + RK 2e6b9127bf3e (1) (GOVERNS): INELIGIBLE =
K_c < 30, n < 2 or V_block = 0, excluded from the audit FRAME BEFORE sampling, all 100,000 audited cells
eligible, ineligible cells stay in N and are disclosed by class, NaN/Inf on an eligible cell's defined
statistic = STOP + latch; RK 22ee55488e0c (B2 scope); HM consult 8b8ec9208448 3(c)/(d); RULES d8594afbb02e.

There is NO z option for any decision (z is an info column only). There is NO "t_block >= 3.0" STOP: that
count is a disclosure column. Every refusal / breach raises fulln_closure.ClosureViolation (exit 78, latched).
Box build evidence only; not a PASS, CLEAR or freeze (R56). Live OFF. Gross only (R61).
"""
import hashlib

import numpy as np
from scipy import stats as _st

from research_fulln480m import pins
from research_fulln480m.fulln_closure import ClosureViolation

ALPHA = pins.ALPHA_BONF_ONE_SIDED  # 1.0405e-10 one-sided (card r3 s5.2)
PRESCREEN_T = pins.PLAIN_T_PRESCREEN  # 3.0, stage 1 only
KC_MIN = pins.KC_MIN_ELIGIBLE  # 30
RC_BOUND = 0.22282  # stop spec r3 S1.3 rule 2: (3.0 / 6.3552)^2, literal
AUDIT_N = 100_000
AUDIT_STRATUM_N = 10_000
AUDIT_REST_N = AUDIT_N - AUDIT_STRATUM_N  # 90,000
STRATUM_LO, STRATUM_HI = 2.0, 3.0  # 2.0 <= plain t < 3.0
DISCLOSURE_T = 3.0  # block t >= 3.0 count: DISCLOSURE ONLY, never a STOP

# Ineligibility classes (RK 6bf51f6bf4ac (b)(3): reported by reason). KC_LT_2 is a disclosure sub-class of
# KC_LT_30 (HM 3(c) "K_c < 2" is subsumed, RK 2e6b (1)).
INELIGIBLE_CLASSES = ("KC_LT_30", "N_LT_2", "V_ZERO")


class AuditBreach(ClosureViolation):
    """Stop spec r3 S1.3 BREACH: STOP + latch, exit 78; the run's results are NOT citable."""


def _u4(a, name):
    a = np.asarray(a)
    if a.dtype != np.dtype("uint32"):
        raise ClosureViolation("%s must be uint32, got %s" % (name, a.dtype))
    return a


def _f8(a, name):
    a = np.asarray(a)
    if a.dtype != np.dtype("float64"):
        raise ClosureViolation("%s must be float64 (never float32), got %s" % (name, a.dtype))
    return a


# ---------------------------------------------------------------- statistics
def plain_var(n, s, q):
    """Sample variance of per-trade net (bp^2): (Q - S^2/n)/(n - 1); NaN where n < 2."""
    n = _u4(n, "n")
    s = _f8(s, "s")
    q = _f8(q, "q")
    nf = n.astype(np.float64)
    with np.errstate(all="ignore"):
        v = (q - s * s / nf) / (nf - 1.0)
    return np.where(n >= 2, v, np.nan)


def plain_t(n, s, q):
    """Plain iid t = (S/n) / sqrt(var_plain / n) (card r3 s5.2 stage 1); NaN where undefined (n < 2 or var <= 0)."""
    n = _u4(n, "n")
    v = plain_var(n, s, q)
    nf = n.astype(np.float64)
    with np.errstate(all="ignore"):
        t = (np.asarray(s) / nf) / np.sqrt(v / nf)
    return np.where((n >= 2) & (v > 0.0), t, np.nan)


def block_t(s_k, n_k):
    """1-UTC-day block-variance t (card r3 s5.1): t = S / sqrt(V), V = sum_k (S_k - n_k S/n)^2.

    s_k f8[K, T], n_k u4[K, T] with K = 728. Returns (t f8[T], V f8[T], K_c u2[T]); K_c = non-empty day
    blocks. t is NaN where n < 2 or V == 0; V is NaN where n == 0.
    """
    s_k = _f8(s_k, "s_k")
    n_k = _u4(n_k, "n_k")
    if s_k.ndim != 2 or s_k.shape != n_k.shape or s_k.shape[0] != pins.K_DAY_BLOCKS:
        raise ClosureViolation("day sums must be [728, T] with matching shapes, got %s / %s" % (s_k.shape, n_k.shape))
    S = s_k.sum(axis=0)
    n = n_k.sum(axis=0, dtype=np.uint64)
    if np.any(n > np.iinfo(np.uint32).max):
        raise ClosureViolation("trade count overflows uint32")
    nf = n.astype(np.float64)
    with np.errstate(all="ignore"):
        mean = np.where(n > 0, S / nf, np.nan)
        V = ((s_k - n_k.astype(np.float64) * mean[None, :]) ** 2).sum(axis=0)
        t = S / np.sqrt(V)
    V = np.where(n > 0, V, np.nan)
    t = np.where((n >= 2) & (V > 0.0), t, np.nan)
    k_c = (n_k > 0).sum(axis=0).astype("<u2")
    return t, V, k_c


def assert_day_sums_match(s_k, n_k, n, s, rtol=1e-9):
    """Day sums must reproduce the pass totals: sum n_k == n exactly, sum S_k == S within 1e-9 relative."""
    n = _u4(n, "n")
    s = _f8(s, "s")
    nk = _u4(n_k, "n_k").sum(axis=0, dtype=np.uint64)
    if not np.array_equal(nk, n.astype(np.uint64)):
        raise ClosureViolation("day-count totals != n")
    sk = _f8(s_k, "s_k").sum(axis=0)
    if not np.all(np.abs(sk - s) <= rtol * np.maximum(1.0, np.abs(s))):
        raise ClosureViolation("day-sum totals != S")


def t_crit(df, alpha=ALPHA):
    """Student t one-sided critical value, df = K_c - 1 ONLY (no z option; HM B13). df 727 -> 6.4468."""
    d = np.asarray(df)
    if d.dtype.kind not in "iu":
        raise ClosureViolation("df must be an integer (K_c - 1)")
    if d.size and int(d.min()) < 1:
        raise ClosureViolation("df must be >= 1")
    if not (isinstance(alpha, float) and 0.0 < alpha < 1.0):
        raise ClosureViolation("alpha must be a float in (0, 1)")
    out = np.empty(d.shape, dtype=np.float64)
    for v in np.unique(d):
        out[d == v] = _st.t.isf(alpha, int(v))
    return out if d.ndim else float(out)


def z_info_column(alpha=ALPHA):
    """Normal z at alpha (6.3552 at 1.0405e-10). INFO COLUMN ONLY - never a decision input (mutant M2f)."""
    return float(_st.norm.isf(alpha))


# ---------------------------------------------------------------- eligibility (RK 6bf51 (b), 2e6b (1))
def eligibility(n, v_block, k_c):
    """Eligible = K_c >= 30 AND n >= 2 AND V_block > 0. Returns (eligible bool[T], reasons dict name -> bool[T]).

    A cell with n >= 2 whose V_block is NaN/Inf cannot be classified: STOP 78 (NaN on a defined statistic).
    """
    n = _u4(n, "n")
    v = _f8(v_block, "v_block")
    kc = np.asarray(k_c)
    if kc.dtype != np.dtype("uint16") or kc.shape != n.shape or v.shape != n.shape:
        raise ClosureViolation("k_c must be uint16 and shapes must match")
    defined = n >= 2
    if np.any(defined & ~np.isfinite(v)):
        raise ClosureViolation("NaN/Inf V_block on a cell with n >= 2 - STOP")
    if np.any(defined & (v < 0.0)):
        raise ClosureViolation("negative V_block - STOP")
    reasons = {
        "KC_LT_30": kc < KC_MIN,
        "N_LT_2": n < 2,
        "V_ZERO": defined & (v == 0.0),
    }
    inel = reasons["KC_LT_30"] | reasons["N_LT_2"] | reasons["V_ZERO"]
    return ~inel, reasons


def eligibility_report(n, v_block, k_c):
    """Disclosure by class. Every cell stays in N (n_cells_in_N == input size); classes may overlap."""
    eligible, reasons = eligibility(n, v_block, k_c)
    kc = np.asarray(k_c)
    rep = {
        "n_cells_in_N": int(eligible.size),
        "n_eligible": int(np.count_nonzero(eligible)),
        "n_ineligible": int(eligible.size - np.count_nonzero(eligible)),
        "by_class": {k: int(np.count_nonzero(v)) for k, v in reasons.items()},
        "by_class_disclosure_KC_LT_2": int(np.count_nonzero(kc < 2)),
    }
    return eligible, rep


# ---------------------------------------------------------------- stage 1 / stage 2
def prescreen(n, s, q, floors_pass):
    """Stage 1: plain t >= 3.0 AND every R59 floor exactly as pinned (card r3 s5.2, RK cond. 5).

    floors_pass must be a COMPUTED bool[T] (floors evaluated from data). None / any other dtype -> 78
    (Q-GS1: an unevaluated floor is NON-EVIDENTIAL, never a pass).
    """
    if floors_pass is None:
        raise ClosureViolation("floors not evaluated - NON-EVIDENTIAL, refused")
    fp = np.asarray(floors_pass)
    t = plain_t(n, s, q)
    if fp.dtype != np.bool_ or fp.shape != t.shape:
        raise ClosureViolation("floors_pass must be bool[T]")
    with np.errstate(invalid="ignore"):
        return (t >= PRESCREEN_T) & fp


def stage2_reject(t_blk, k_c, eligible):
    """Stage 2 on stage-1 survivors: reject iff eligible AND t_block > t_{K_c-1}.isf(1.0405e-10).

    Strict '>' is the conservative (refusing) reading of an unstated tie rule (OPEN, plan r3 B2 note).
    NaN/Inf on an eligible cell = STOP 78. Ineligible cells are never rejected.
    """
    t_blk = _f8(t_blk, "t_block")
    kc = np.asarray(k_c)
    el = np.asarray(eligible)
    if el.dtype != np.bool_ or el.shape != t_blk.shape or kc.shape != t_blk.shape:
        raise ClosureViolation("eligible / k_c shapes")
    if np.any(el & ~np.isfinite(t_blk)):
        raise ClosureViolation("NaN/Inf t_block on an eligible cell - STOP")
    out = np.zeros(t_blk.shape, dtype=bool)
    if np.any(el):
        crit = t_crit(kc[el].astype(np.int64) - 1)
        out[el] = t_blk[el] > crit
    return out


def window_major_order(window_ids=pins.DISCOVERY_WINDOW_IDS, n_tiles=pins.N_TILES):
    """[A] loop order (stop spec r3 [A], HM B11): window outer, tile inner - (window_id, tile) pairs."""
    if list(window_ids) != list(pins.DISCOVERY_WINDOW_IDS):
        raise ClosureViolation("method A runs exactly W01..W12")
    return [(w, t) for w in window_ids for t in range(int(n_tiles))]


# ---------------------------------------------------------------- audit (stop spec r3 S1.3, eligible frame)
def audit_frame(failed, eligible):
    """Frame = cells that FAILED the pre-screen AND are ELIGIBLE, canonical lattice order (int64 indices)."""
    f = np.asarray(failed)
    e = np.asarray(eligible)
    if f.dtype != np.bool_ or e.dtype != np.bool_ or f.shape != e.shape or f.ndim != 1:
        raise ClosureViolation("failed / eligible must be bool[N] of equal shape")
    return np.flatnonzero(f & e).astype("<i8")


def audit_sample(frame_idx, plain_t_frame, seed=pins.SEED_A_AUDIT):
    """100,000 eligible cells without replacement: 10,000 uniform over 2.0 <= plain t < 3.0 FIRST, then 90,000
    uniform over the rest of the frame (no overlap), numpy Generator(PCG64(seed)).choice.

    A stratum under 10,000 or a rest under 90,000 is NOT topped up: fewer than 100,000 audited = rule 3 STOP.
    """
    idx = np.asarray(frame_idx)
    pt = _f8(plain_t_frame, "plain_t_frame")
    if idx.dtype != np.dtype("int64") or idx.ndim != 1 or pt.shape != idx.shape:
        raise ClosureViolation("frame_idx must be int64[M] with plain_t_frame f8[M]")
    if idx.size > 1 and not np.all(np.diff(idx) > 0):
        raise ClosureViolation("frame must be in canonical (strictly increasing) lattice order")
    if not np.all(np.isfinite(pt)):
        raise AuditBreach("S1.3 rule 3: NaN/Inf plain t on an eligible frame cell")
    if seed != pins.SEED_A_AUDIT:
        raise AuditBreach("S1.3 rule 4: audit seed %r != pinned %d" % (seed, pins.SEED_A_AUDIT))
    rng = np.random.Generator(np.random.PCG64(seed))
    stratum = idx[(pt >= STRATUM_LO) & (pt < STRATUM_HI)]
    if stratum.size < AUDIT_STRATUM_N:
        raise AuditBreach("S1.3 rule 3: stratum 2.0 <= t < 3.0 has %d < 10,000 eligible cells (no top-up)" % stratum.size)
    s1 = rng.choice(stratum, size=AUDIT_STRATUM_N, replace=False)
    rest = np.setdiff1d(idx, s1, assume_unique=True)
    if rest.size < AUDIT_REST_N:
        raise AuditBreach("S1.3 rule 3: rest of frame has %d < 90,000 eligible cells (no top-up)" % rest.size)
    s2 = rng.choice(rest, size=AUDIT_REST_N, replace=False)
    return np.concatenate([s1, s2]).astype("<i8")


def sample_sha256(sample_idx):
    return hashlib.sha256(np.ascontiguousarray(np.asarray(sample_idx, dtype="<i8")).tobytes()).hexdigest()


def audit_check(sample_idx, n, plain_variance, v_block, t_blk, k_c, eligible,
                expected_sample_sha256, expected_seed, seed_used):
    """Stop spec r3 S1.3 on the sampled cells (arrays aligned with sample_idx). Returns the audit record;
    BREACH = any of rules 1-4. The block t >= 3.0 count is a DISCLOSURE column only (RK c07f (4))."""
    s_idx = np.asarray(sample_idx)
    n = _u4(n, "n")
    pv = _f8(plain_variance, "plain_variance")
    vb = _f8(v_block, "v_block")
    tb = _f8(t_blk, "t_block")
    kc = np.asarray(k_c)
    el = np.asarray(eligible)
    for a in (n, pv, vb, tb, kc, el):
        if a.shape != s_idx.shape:
            raise ClosureViolation("audit arrays must align with sample_idx")
    if el.dtype != np.bool_ or not np.all(el):
        raise ClosureViolation("ineligible cell in the audit sample - frame must be eligible-only")
    rule4 = (sample_sha256(s_idx) != expected_sample_sha256) or (seed_used != expected_seed) or (
        expected_seed != pins.SEED_A_AUDIT)
    n_unique = int(np.unique(s_idx).size)
    with np.errstate(all="ignore"):
        rc = (vb / n.astype(np.float64)) / pv
    finite = np.isfinite(pv) & np.isfinite(vb) & np.isfinite(tb) & np.isfinite(rc)
    rule3 = (n_unique < AUDIT_N) or (s_idx.size != AUDIT_N) or (not bool(np.all(finite)))
    ok = finite
    crit = np.full(tb.shape, np.inf)
    if np.any(ok):
        crit[ok] = t_crit(kc[ok].astype(np.int64) - 1)
    rule1 = int(np.count_nonzero(ok & (tb >= crit)))
    rule2 = int(np.count_nonzero(ok & (rc < RC_BOUND)))
    rec = {
        "n_audited": n_unique,
        "rule1_block_t_ge_crit": rule1,
        "rule2_rc_lt_0.22282": rule2,
        "rule3_short_or_nonfinite": bool(rule3),
        "rule4_sha_or_seed_mismatch": bool(rule4),
        "disclosure_block_t_ge_3.0": int(np.count_nonzero(np.isfinite(tb) & (tb >= DISCLOSURE_T))),
        "z_info_column": z_info_column(),
    }
    rec["breach"] = bool(rule1 or rule2 or rule3 or rule4)
    return rec


def raise_on_breach(rec):
    """BREACH -> STOP + latch (exit 78). A non-breach record returns None."""
    if rec.get("breach"):
        raise AuditBreach("S1.3 audit BREACH: %s" % {k: v for k, v in rec.items() if k.startswith("rule")})
