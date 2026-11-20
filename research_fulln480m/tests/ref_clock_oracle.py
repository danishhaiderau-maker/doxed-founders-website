"""TEST-ONLY, VERDICT-ONLY reference for the ONE reference non-X1 exit clock (batch B-R63D).

Binding: card B-R63D r2.2 24430bc38561 s4.1 / s4.3 item 11 / s6.3 (F-A9-TIE T1 / T3 / T4);
RK CORRECTION 4de6f32dde6c O1-O4; A9 r10 7579003b2b41 L178 (R63-E pin text), L195 (R63-F.3);
R63-E pin card 2a6f17d2d128 L225.

O1: this file lives under research_fulln480m/tests/ only. Production fulln_decide.py and
    fulln_decide_pins.py never import it (asserted by F-R63D-ORACLE-ISOLATION).
O2: it returns the clock identity and the R63-E verdict ONLY. There is no px, last_tr or nxt
    pricing and no exit pricing anywhere in this file (that half is HELD, H9).
O3: every clock rule below cites its ext2 line in /workspace/grok-strategist/ext2/ext2_search.py,
    sha256 6b5956fdc19f4fbc8061a20ed65e7b04e6b65d2039ed202c72535a15cc09cd61 (2274 lines), quoted
    byte-exact. HM byte-checks these cites (OPEN-12).
O4: the A9 L195 / L238 "pure reference oracle implementing the ext2 clock" is met for the VERDICT
    ONLY. Any test that needs the pricing half stays HELD with the exit-path work.

OPEN-12 cites (ext2 6b5956fdc19f, verbatim):
- L875  `    ent_s = nxt[np.clip(dec + 1, 0, S - 1)]`  (ext2 entry second e0 = first traded second >= T)
- L876  `    fresh &= (ent_s < S) & (ent_s - (dec + 1) < stale_s)`  (the pinned 600 s ent_s window)
- L1079 `        e0 = ent[TM[a:a + CH]]; m = len(e0)`  (prior trade's entry second e0)
- L1081 `        end = e0 + H`  (CLOCK IDENTITY: ext2 time-exit second end = e0 + H = t_x)
- L1106 `        lt = last_tr[end]; fresh = (lt >= e0) & (end - lt < stale)`  (cited for the R63-E
  reason only: the ext2 time exit reads second t_x itself; NOT implemented here, O2)
- T1 BLOCK rule: NOT FOUND as an explicit comparison in ext2. ext2's own non-overlap is space()
  L1243-L1252 (`if m >= last + gap:` at L1250, with `if H <= 60 or len(idx) == 0:` / `return idx`
  at L1245-L1246, i.e. no spacing for H <= 60), which is keyed off the decision minute and does
  not implement BLOCK at e1 = t_x. The BLOCK verdict is the adopted R63-E pin text (2) (A9 r10
  L178; pin card 2a6f L225), which DERIVES it from L1081 + L1105-L1109: the ext2 time exit is
  priced at last_tr[t_x], so an entry at e1 = t_x would share second t_x with the prior exit.
  This oracle encodes that pin verdict; it does not claim an ext2 line that states it.
"""

CLOCK_X1 = "X1"
CLOCK_EXT2 = "EXT2"
ENTER = "ENTER"
BLOCK = "BLOCK"

# Clock identity only (O2). Nothing here prices anything.
_IDENTITY = {
    CLOCK_X1: {
        "clock": CLOCK_X1,
        "exit_instant": "t_x = t_e + h; time-exit row t_x - 1 (A9 r10 L118 X1; R63-A)",
        "entry_window_s": (1, 5),  # A9 r10 L111 E1 / R63-F.2
    },
    CLOCK_EXT2: {
        "clock": CLOCK_EXT2,
        "exit_instant": "end = e0 + H (ext2 6b5956fdc19f L1081)",
        "entry_window_s": (0, 599),  # ext2 L875-L876: ent_s = nxt[T], ent_s - T < 600
    },
}


def clock_identity(clock):
    """Return the identity record of a governing exit clock (X1 or the reference EXT2)."""
    if clock not in _IDENTITY:
        raise ValueError("unknown clock %r" % (clock,))
    return dict(_IDENTITY[clock])


def prior_cursor(clock, e0, hold):
    """The prior trade's cursor t_x. X1: t_e + h (A9 r10 L118). EXT2: end = e0 + H (ext2 L1081).

    Integer arithmetic on seconds only; no forced-close clamp (ext2 L1082-L1086 is not used, EM4).
    """
    clock_identity(clock)
    return int(e0) + int(hold)


def verdict(clock, test_value, t_x, flags=None):
    """R63-E verdict for a later decision whose test value (t_d, or entry second e1) meets cursor t_x.

    Returns (clock_identity, verdict) and nothing else (O2). `flags` (adopted-pin labels such as
    R63_A / R63_D / R63_E) is accepted and deliberately IGNORED: the verdict is keyed to the clock
    that actually governs the prior trade's time exit, not to pin labels (A9 r10 L181 G1, L228-L229).
    """
    ident = clock_identity(clock)
    _ = flags
    test_value = int(test_value)
    t_x = int(t_x)
    if test_value < t_x:
        return ident, BLOCK  # C-1: enters only if t_d >= t_x (A9 r10 L158)
    if test_value > t_x:
        return ident, ENTER
    # equality tie (R63-E): (1) X1 ENTERS; (2) ext2 clock BLOCKS (strict >)
    if clock == CLOCK_X1:  # tie:x1
        return ident, ENTER
    return ident, BLOCK  # tie:nonx1
