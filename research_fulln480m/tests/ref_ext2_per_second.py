"""TEST-ONLY verbatim copy of ext2 per_second (card B-R63X r2.1 a312e9be2d95 s4.1, A-2 / R-C2, OPEN-X-5).

Source: ext2_search.py sha256 6b5956fdc19f4fbc8061a20ed65e7b04e6b65d2039ed202c72535a15cc09cd61
(119,529 B), lines L702-L710 inclusive (incl. the final `return out`; the GS quote L702-L709 omits
it). Call site of the "last" path: ext2 L752 (RK N-2), `px_d = per_second(sec, base, S, t[:, 3], "last")`
after bfx_day's lexsort by (ts, id) (EXT2-SORT, ext2 L610).
Box copy read for this file: /workspace/hm-480m-launch/hm-C6-1-ext2-LIQ-SLNONE-evidence/ext2_search_COPY.py
(same sha256). Quoted-bytes sha256 of L702-L710 (lines joined by LF, no trailing LF):
b181c7c0ed787d5055432b31c0338ef4ebbd7a49b43b26f2a6e97df9cdcb0867 (Coder-computed; HM byte-diff
against live ext2 is a one-time HM receipt, OPEN-X-5 point 3; there is NO runtime read of ext2).

This module is NEVER imported by production code (isolation test in tests/test_fulln_micro.py).
ext2 is the reference for LAST and has-trades ONLY; it has no OPEN / LOW / HIGH / count builder.
"""
import numpy as np

# ---- BEGIN VERBATIM ext2 L702-L710 (do not edit; hashed by test_F_M_REFSRC_quote_sha) ----
def per_second(sec, base, S, val, how):
    i = sec - base
    k = (i >= 0) & (i < S)
    i, val = i[k], val[k]
    if how == "last":
        out = np.full(S, np.nan); out[i] = val  # numpy fancy assignment: last write wins (sorted input)
        return out
    out = np.zeros(S); np.add.at(out, i, val)
    return out
# ---- END VERBATIM ext2 L702-L710 ----
