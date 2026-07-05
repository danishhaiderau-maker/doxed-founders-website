#!/usr/bin/env python3
from pathlib import Path
import shutil

p = Path(__file__).resolve().parents[1] / "services/btc-conservative-agent/research/analyzer_research_engine_v62.py"
text = p.read_text(encoding="utf-8")
old = '''    # AI funnel: approvals + rejects + skipped + timeout ≈ decision rows with AI outcome
    if decisions is not None and not decisions.empty:
        d = decisions.copy()
        ai_txt = d["ai_decision_text"].fillna("").astype(str).str.upper() if "ai_decision_text" in d.columns else pd.Series([], dtype=str)
        appr = int((ai_txt == "APPROVE").sum()) if len(ai_txt) else 0
        rej = int((ai_txt == "REJECT").sum()) if len(ai_txt) else 0
        timeout = int(ai_txt.str.contains("ERROR|TIMEOUT", regex=True).sum()) if len(ai_txt) else 0
        skipped = 0
        if "skip_stage" in d.columns:
            skipped = int(d["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum())
        elif "reason" in d.columns:
            skipped = int(d["reason"].fillna("").astype(str).str.contains("AI_COOLDOWN", regex=False).sum())
        funnel = appr + rej + skipped + timeout
        ai_rows = int((d["decision"].fillna("").astype(str).str.upper().isin(["AI", "BLOCKED"])).sum()) if "decision" in d.columns else len(d)
        _add(
            "ai_decision_funnel",
            abs(funnel - ai_rows) <= max(5, int(0.02 * len(d))),
            f"approvals+rejects+skipped+timeout={appr}+{rej}+{skipped}+{timeout}={funnel}",
            f"ai_decision_rows≈{ai_rows} (total decisions={len(d)})",
            "From decisions_3factor.ai_decision_text + skip_stage/reason",
        )'''
new = '''    # AI funnel: approvals + rejects + skipped + timeout reconcile on AI-involved rows
    if decisions is not None and not decisions.empty:
        d = decisions.copy()
        ai_txt = d["ai_decision_text"].fillna("").astype(str).str.upper() if "ai_decision_text" in d.columns else pd.Series([""] * len(d))
        dec = d["decision"].fillna("").astype(str).str.upper() if "decision" in d.columns else pd.Series([""] * len(d))
        skip_st = d["skip_stage"].fillna("").astype(str).str.upper() if "skip_stage" in d.columns else pd.Series([""] * len(d))
        ai_involved = d[dec.isin(["AI", "BLOCKED"]) | ai_txt.isin(["APPROVE", "REJECT"]) | skip_st.eq("COOLDOWN")]
        sub_txt = ai_involved["ai_decision_text"].fillna("").astype(str).str.upper()
        appr = int((sub_txt == "APPROVE").sum())
        rej = int((sub_txt == "REJECT").sum())
        timeout = int(sub_txt.str.contains("ERROR|TIMEOUT", regex=True).sum())
        skipped = int(ai_involved["skip_stage"].fillna("").astype(str).str.upper().eq("COOLDOWN").sum()) if "skip_stage" in ai_involved.columns else 0
        funnel = appr + rej + skipped + timeout
        n_ai = int(len(ai_involved))
        _add(
            "ai_decision_funnel",
            abs(funnel - n_ai) <= max(10, int(0.05 * n_ai)),
            f"approvals+rejects+skipped+timeout={appr}+{rej}+{skipped}+{timeout}={funnel}",
            f"ai_involved_rows={n_ai} (total decisions={len(d)})",
            "decisions_3factor: AI/BLOCKED + ai_decision_text APPROVE/REJECT + COOLDOWN skip_stage",
        )'''
if old not in text:
    raise SystemExit("AI funnel block not found")
p.write_text(text.replace(old, new, 1), encoding="utf-8")
shutil.copy2(p, p.parent.parent / "analyzer_research_engine_v62.py")
print("patched")
