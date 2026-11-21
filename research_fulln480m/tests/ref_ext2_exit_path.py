"""TEST-ONLY verbatim copy of the ext2 first-hit path (card B-R63X r2.1 a312 s4.1 / R-C5).

Source: ext2_search.py sha256 6b5956fdc19f4fbc8061a20ed65e7b04e6b65d2039ed202c72535a15cc09cd61
(119,529 B). CODER-PROPOSED copy range L1087-L1104 (TP, SL and TR loops complete; GS quote
EXT2-PATH L1087-L1103 stops one line before the TR store line L1104). Context: ext2 L1074
(the 17-level T/R store) and L1078-L1086 (forced-close cap; not used under G.1 / EM4).
Call site of the first-hit loops: inside the chunk loop after r is built (ext2 L1090-L1092).
Box copy: /workspace/hm-480m-launch/hm-C6-1-ext2-LIQ-SLNONE-evidence/ext2_search_COPY.py
(same sha256). Quoted-bytes sha256 of L1087-L1104 (lines joined by LF, no trailing LF):
aca6b3a5db1ae1778c75ca02866653bde1b6aa30a6fbb73fccbcea4335b78c49 (Coder-computed; HM byte-diff
is a consult item). There is NO runtime read of ext2.

This file contains NO LIQ code (ext2 first-hit has none, s5.2.2). Never imported by
production. Holds the test-only literal PARITY_LIQ_METHOD = "NON_PARITY_LIQ" (S-13 released;
RK d21b13e682e3 / addendum 6fcd9bfcb9e7).
"""
import numpy as np

# Ruled method (s5.2.2): LIQ is NON-PARITY. S-13 does not fire.
PARITY_LIQ_METHOD = "NON_PARITY_LIQ"

# Grid literals as in ext2 L152-L155 (copied as names used by the verbatim block).
TP_BP = [None, 5, 10, 15, 20, 30, 50, 100]
SL_BP = [None, 5, 10, 15, 20, 30, 50, 100]
TR_BP = [None, 10, 20, 50]

# ---- BEGIN VERBATIM ext2 L1087-L1104 (do not edit; hashed by test_F_E_REFSRC) ----
        off = np.arange(1, H + 1)
        ix = e0[:, None] + off[None, :]
        inside = ix <= end[:, None]
        P = px[np.clip(ix, 0, S - 1)]; P[~inside] = np.nan
        with np.errstate(all="ignore"):
            r = d * (P / p0[:, None] - 1.0) * 1e4
        big = H + 1
        col = 0
        for tp in TP_BP[1:]:
            h = r >= tp; f = np.where(h.any(1), h.argmax(1), -1)
            T[a:a + m, col] = np.where(f >= 0, f + 1, big); R[a:a + m, col] = np.where(f >= 0, r[np.arange(m), np.clip(f, 0, None)], 0); col += 1
        for sl in SL_BP[1:]:
            h = r <= -sl; f = np.where(h.any(1), h.argmax(1), -1)
            T[a:a + m, col] = np.where(f >= 0, f + 1, big); R[a:a + m, col] = np.where(f >= 0, r[np.arange(m), np.clip(f, 0, None)], 0); col += 1
        pk = np.fmax.accumulate(np.where(np.isnan(r), -np.inf, r), axis=1); pk = np.maximum(pk, 0.0)
        for tr in TR_BP[1:]:
            h = (pk - r) >= tr; f = np.where(h.any(1), h.argmax(1), -1)
            T[a:a + m, col] = np.where(f >= 0, f + 1, big); R[a:a + m, col] = np.where(f >= 0, r[np.arange(m), np.clip(f, 0, None)], 0); col += 1
# ---- END VERBATIM ext2 L1087-L1104 ----

# The verbatim block is indented as in ext2 (it is not a standalone function). The hash test
# reads the 18 source lines between the BEGIN/END markers.
