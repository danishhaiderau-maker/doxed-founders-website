"""AMEND6 bounded immutable-cache viewer. HTTP performs ZERO file/IPC work.

Only the separately authorized supervisor can supply/publish the bounded JSON.
No producer, timer, process, engine or refresh is started by this module/GET.
Absent certified loader/producer bindings => empty view, writer UNKNOWN, HOLD.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

N = 181442
FORMULA = "(6+C(6,2))*4*5*2*4*(3*3*2*3)+2 = 181,442"
BANNER = "Live OFF · PAPER ONLY · LIMITED SCOPE · R24 NOT TESTED · SCREEN-LEVEL · INVALID-for-fills"
GROSS = "gross (no fees, funding or slippage)"
POWER = "R61(h): forward floor is at least 4 weeks AND 100 trades. Power at alpha 0.05/N with 100 forward trades is low (~17% at Sharpe 0.3); real procedure power NOT TESTED. Passing is evidence, not proof; a fail may miss an edge. Larger samples are optional analysis, not a gate."
EMPTY = "nothing found yet / no completed real batches yet"
CALENDAR = (
    ("B1", "2024-11-29", "2025-01-28", "W1; launch authorization separate"),
    ("B2", "2025-01-31", "2025-04-01", "sealed H1 overlap; eligibility unresolved"),
    ("B3", "2025-04-04", "2025-06-03", "trailing warmup requires runner card"),
    ("B4", "2025-06-06", "2025-08-05", "trailing warmup requires runner card"),
    ("B5", "2025-08-08", "2025-10-07", "trailing warmup requires runner card"),
    ("B6", "2025-10-10", "2025-12-09", "trailing warmup requires runner card"),
    ("B7", "2025-12-12", "2026-02-10", "trailing warmup requires runner card"),
    ("B8", "2026-02-13", "2026-04-14", "spent holdout overlap; eligibility unresolved"),
    ("B9", "2026-04-17", "2026-06-16", "trailing warmup requires runner card"),
    ("B10", "2026-06-19", "2026-08-18", "RUN-A2 heavily viewed"),
    ("B11", "2026-08-21", "2026-10-20", "RUN-A / Phase-B heavily viewed"),
)
STATS = ("n_trades", "n_eff", "win_rate", "p_hac", "bh_q", "white_rc_pass", "dsr",
         "liquidation_count", "max_drawdown_bp")
PROVENANCE = ("generation_id", "analyzer_git_rev", "source_data_revision", "generated_at",
              "window_start", "cutoff", "search_space_rev", "gate_rev")

SCHEMA = "twoyear-summary-v2"
SNAPSHOT_NAME = "twoyear-summary-v2.json"
BYTE_CAP = 524288
HTML_CAP = 1048576
ROW_CAP = 4096
TTL = 5.0
HTTP_BUDGET = .250
FRESH_SEC = 60
STALE_SEC = 3600
RANKING = "research ranking only — forward paper decides"
SLIPPAGE = "slippage NOT MODELLED (R61(g), no historical depth)"
BADGE = "lead, not proof · LEAD (INVALID-for-fills)"
SIGN = "(ii)-(i); positive = cost, negative = improvement"
UNKNOWN = "UNKNOWN"
BASELINE = "4978afd087c198e35efadaa9ec4b59b765fd0310"


def fact(value=None, source=None, state=None):
    return {"value": value, "state": state or (UNKNOWN if value is None else "OBSERVED"), "source": source}


def bounded_json(value):
    """Incremental output, refuse before extending beyond cap, never truncate."""
    chunks, size = [], 0
    for text in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
        chunk = text.encode("utf-8")
        if size+len(chunk)>BYTE_CAP:
            raise ValueError("API output byte cap")
        chunks.extend(chunk[i:i+16384] for i in range(0,len(chunk),16384))
        size += len(chunk)
    return b"".join(chunks)


def _token_bounds(raw):
    """Structural cap on bounded UTF-8 bytes BEFORE allocating JSON containers."""
    raw.decode("utf-8-sig")  # validate encoding, bounded by the 512KiB input cap
    stack=[]
    records=0
    i=0
    while i<len(raw):
        ch=raw[i]
        if ch in b" \r\n\t":
            i+=1
            continue
        if stack and stack[-1][0]==91 and stack[-1][1] and ch!=93:
            records+=1
            stack[-1][1]=False
            if records>ROW_CAP:
                raise ValueError("record cap before parse")
        if ch==34:
            start=i
            i+=1
            while i<len(raw):
                if raw[i]==92:
                    i+=2
                elif raw[i]==34:
                    break
                else:
                    i+=1
                if i-start>12290:
                    raise ValueError("string token cap before parse")
            if i>=len(raw):
                raise ValueError("unterminated string")
        elif ch in (91,123):
            stack.append([ch,ch==91])
            if len(stack)>12:
                raise ValueError("nesting cap before parse")
        elif ch in (93,125):
            if not stack:
                raise ValueError("unmatched container")
            stack.pop()
        elif ch==44 and stack and stack[-1][0]==91:
            stack[-1][1]=True
        i+=1


def validate(raw):
    if len(raw)>BYTE_CAP:
        raise ValueError("snapshot byte cap BEFORE parse")
    def invalid(_):
        raise ValueError("non-finite JSON")
    _token_bounds(raw)
    def pairs(items):
        out={}
        for key,value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key]=value
        return out
    data=json.loads(raw.decode("utf-8-sig"),parse_constant=invalid,object_pairs_hook=pairs)
    records=[0]
    def walk(value,depth=0):
        if depth>12:
            raise ValueError("nesting cap")
        if isinstance(value,list):
            records[0]+=len(value)
            if records[0]>ROW_CAP:
                raise ValueError("cumulative row cap")
            for row in value:
                walk(row,depth+1)
        elif isinstance(value,dict):
            if len(value)>128:
                raise ValueError("mapping/provenance cap")
            for key,item in value.items():
                walk(key,depth+1)
                walk(item,depth+1)
        elif isinstance(value,str) and len(value.encode("utf-8"))>2048:
            raise ValueError("string byte cap")
        elif isinstance(value,float) and not math.isfinite(value):
            raise ValueError("non-finite value")
    walk(data)
    if not isinstance(data,dict) or data.get("schema")!=SCHEMA or data.get("live") is not False or data.get("basis")!=GROSS:
        raise ValueError("schema / Live OFF / gross basis mismatch")
    for key in ("generation_id","series","freeze_sha","chain_sha","rules_sha","selection_ledger_sha","accounting_policy_sha","board_sha","code_sha"):
        value=data.get(key)
        if not isinstance(value,str) or not value:
            raise ValueError("missing identity: "+key)
        if key.endswith("_sha") and (len(value)!=64 or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("invalid full hash: "+key)
    for key in ("generated_at","source_as_of"):
        ts=datetime.fromisoformat(data.get(key,"").replace("Z","+00:00"))
        if ts.tzinfo is None:
            raise ValueError("unknown source clock")
    policy=data.get("accounting_policy",{})
    if policy.get("schema")!="pin15-accounting-v1" or policy.get("mutually_exclusive") is not True or policy.get("unpriced_worst_in_filled") is not True or policy.get("precedence")!="A_THEN_PROVEN_FILLED_C_THEN_B_THEN_NORMAL":
        raise ValueError("accounting category/denominator freeze missing")
    batches=data.get("batches",[])
    rows=data.get("ranking",[])
    if not isinstance(batches,list) or len(batches)!=11 or not isinstance(rows,list) or len(rows)>50:
        raise ValueError("batch/strategy cap")
    bids=[b.get("batch_id") for b in batches]
    if bids!=[b[0] for b in CALENDAR]:
        raise ValueError("ordered batch identity mismatch")
    allowed={"UNKNOWN","not_started","running","complete","failed_closed","sealed","not_included"}
    for b in batches:
        if b["batch_id"] in ("B6","B7") and b.get("status") not in ("sealed","UNKNOWN"):
            raise ValueError("sealed batch cannot acquire a completion badge")
        if b["batch_id"] in ("B8","B9","B10","B11") and b.get("status") not in ("not_included","UNKNOWN"):
            raise ValueError("excluded batch cannot acquire a completion badge")
        if b.get("status") not in allowed:
            raise ValueError("batch status")
        if b.get("counted_for_ranking"):
            if b["batch_id"] not in ("B1","B2","B3","B4","B5"):
                raise ValueError("AMEND6 maximum partial scope B1-B5 only")
            if b.get("status")!="complete" or b.get("count_reconciled") is not True or b.get("freeze_sha")!=data["freeze_sha"]:
                raise ValueError("incompatible completed ranking scope")
            if b["batch_id"]=="B2" and not b.get("role_release_sha"):
                raise ValueError("reserved/exposed window lacks explicit role release")
    ranked=[b["batch_id"] for b in batches if b.get("counted_for_ranking")]
    if data.get("ranked_batch_ids")!=ranked or data.get("ranked_batch_count")!=len(ranked):
        raise ValueError("explicit n-of-11 contributor list/count mismatch")
    if rows and not ranked:
        raise ValueError("no verified completed ranking scope")
    if data.get("selection_base")!=832 or not isinstance(data.get("variants_tried"),int) or data["variants_tried"]<0 or data.get("n_selection")!=832+data["variants_tried"]:
        raise ValueError("selection family must bind 832 + all recorded variants")
    seen=set()
    for row in rows:
        cid=str(row.get("strategy_id",""))
        if not cid or cid in seen or len(row.get("per_batch",[]))>11 or len(row.get("folds",[]))>20:
            raise ValueError("strategy/detail/fold shape")
        seen.add(cid)
        if row.get("scope_batches")!=len(ranked) or not isinstance(row.get("gross_positive_batches"),int) or not 0<=row["gross_positive_batches"]<=len(ranked):
            raise ValueError("completed-scope denominator mismatch")
        if any(b.get("batch_id") not in ranked for b in row.get("per_batch",[])):
            raise ValueError("per-batch values outside authorized scope")
        accounting=row.get("accounting")
        if not isinstance(accounting,dict):
            raise ValueError("Pin15 accounting missing")
        validate_accounting(accounting,policy)
        for fold in row.get("folds",[]):
            validate_accounting(fold.get("accounting",{}),policy)
        if any(accounting["missing_extreme_count"].values()) or any(any(fold.get("accounting",{}).get("missing_extreme_count",{}).values()) for fold in row.get("folds",[])):
            raise ValueError("unresolved adverse extreme requires UNKNOWN P&L; numeric ranking generation refused")
        if len(row.get("equity_points",[]))>11:
            raise ValueError("equity cap")
        # No ranking/statistical/price computation happens here. Frozen producer
        # accounting is displayed, and absent fields remain individually UNKNOWN.
    if len(data.get("findings",[]))>50 or len(data.get("provenance",[]))>128:
        raise ValueError("Findings/provenance cap")
    for finding in data.get("findings",[]):
        if str(finding.get("strategy_id")) not in seen:
            raise ValueError("Finding is not an allowlisted strategy")
        validate_accounting(finding.get("accounting",{}),policy)
        for fold in finding.get("folds",[]):
            validate_accounting(fold.get("accounting",{}),policy)
    distributions=data.get("distributions",{})
    if not isinstance(distributions,dict) or any(bid not in ranked and dist.get("bins") for bid,dist in distributions.items()) or any(len(v.get("bins",[]))>32 for v in distributions.values()):
        raise ValueError("histogram bin cap")
    forward=data.get("forward")
    if isinstance(forward,dict):
        if any(k in forward for k in ("accepted","passed","live_ready")):
            raise ValueError("viewer cannot display forward acceptance from ranking")
        i,ii,difference=(forward.get(k) for k in ("fill_model_bp","gross_mark_bp","slippage_signed_bp"))
        if (i is None or ii is None) and (difference is not None or forward.get("ratio") is not None):
            raise ValueError("unknown forward input requires UNKNOWN difference/ratio")
        if i is not None and ii is not None:
            if forward.get("same_signals_timestamps") is not True or difference is None or not math.isclose(ii-i,difference,abs_tol=1e-9):
                raise ValueError("signed forward difference / signal-time provenance")
            if forward.get("deciding_record")!="(i) fill model" or forward.get("gross_role")!="ratio only":
                raise ValueError("forward roles: fill model decides; gross ratio-only")
        ref=forward.get("backtest_expectancy_bp")
        if (ref is None or ref<=0) and forward.get("ratio") is not None:
            raise ValueError("ratio requires positive comparable backtest reference")
    return data


def validate_accounting(a,policy):
    keys=("count_a_no_fill","count_b_exit_unpriceable","count_c_unpriced_worst","capture_count","filled_count","normal_count")
    if any(not isinstance(a.get(k),int) or isinstance(a.get(k),bool) or a[k]<0 for k in keys):
        raise ValueError("Pin15 unknown/invalid count; cannot rank")
    nofill,b,c,cap,filled,normal=(a[k] for k in keys)
    if cap!=filled+nofill or filled!=normal+b+c or a.get("no_fill_wins")!=0:
        raise ValueError("Pin15 category/denominator reconciliation")
    missing=a.get("missing_extreme_count")
    if not isinstance(missing,dict) or any(not isinstance(missing.get(k),int) or missing[k]<0 for k in ("B","C")) or missing["B"]>b or missing["C"]>c:
        raise ValueError("missing-extreme substate must retain B/C categories")
    if any(missing.values()) and (a.get("mark_status")!="UNKNOWN_MISSING_EXTREME" or a.get("win_state")!="UNKNOWN"):
        raise ValueError("missing extreme cannot become a win")
    share=(b+c)/filled if filled else None
    if a.get("board_review_5pct")!=(share>.05 if share is not None else None):
        raise ValueError("strict >5% flag mismatch")
    for key,numerator,denominator in (("no_fill_share",nofill,cap),("exit_unpriceable_share",b,filled),("unpriced_worst_share",c,filled)):
        actual=a.get(key)
        expected=numerator/denominator if denominator else None
        if (expected is None and actual is not None) or (expected is not None and (actual is None or not math.isclose(actual,expected,abs_tol=1e-9))):
            raise ValueError("Pin15 share mismatch")
    if a.get("exit_mark")!="LOW_LONG_HIGH_SHORT_1S" or a.get("worst_mark")!="ADVERSE_HOLDING_WINDOW":
        raise ValueError("Pin15 frozen adverse mark semantics missing")


def text(value):
    if isinstance(value,dict) and "value" in value:
        return escape(str(value["value"] if value["value"] is not None else UNKNOWN))+" ["+escape(value.get("state",UNKNOWN))+"]"
    if value is None:
        return UNKNOWN
    if isinstance(value,(dict,list)):
        return escape(json.dumps(value,ensure_ascii=False,allow_nan=False))
    return escape(str(value))


class Html:
    def __init__(self):
        self.chunks=[]
        self.size=0
    def add(self,value):
        chunk=value.encode("utf-8")
        if self.size+len(chunk)>HTML_CAP:
            raise ValueError("HTML output byte cap")
        self.size+=len(chunk)
        self.chunks.extend(chunk[i:i+16384] for i in range(0,len(chunk),16384))
    def finish(self):
        return b"".join(self.chunks)


def shell(title,body="",stale=False):
    out=Html()
    out.add("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>"+escape(title)+"</title><style>body{font:16px system-ui;max-width:1400px;margin:auto;padding:20px;background:#111827;color:#f3f4f6}a{color:#9dc8ff}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #627084;padding:6px}section{margin:14px 0;padding:12px;border:1px solid #627084;overflow:auto}svg{width:100%;height:180px}.unknown{background:repeating-linear-gradient(45deg,#333 0 4px,#555 4px 8px)}.stale{color:#ffb4ad}pre{white-space:pre-wrap}@media print{body{background:white;color:black}}</style></head><body>")
    out.add("<header><b>"+BANNER+"</b><p>"+GROSS+" · "+SLIPPAGE+" · funding SET ASIDE</p><p>"+RANKING+" · "+BADGE+"</p><p>"+POWER+"</p></header>")
    out.add("<nav><a href='/details'>Overview / Archive</a> · <a href='/twoyear-batch'>Partial Two-Year Batch Study</a> · <a href='/strategy-findings'>Strategy Findings</a> · <a href='/data-health'>Data Health KNOWN GAP</a></nav>")
    if stale:
        out.add("<h2 class='stale'>data stale / STALE-RED — old generation, not a current run or decision</h2>")
    out.add("<h1>"+escape(title)+"</h1>"+body)
    return out


def context(data,stale):
    ids=[b["batch_id"] for b in data.get("batches",[]) if b.get("counted_for_ranking")]
    eligible=5 if any(b.get("batch_id")=="B2" and b.get("role_release_sha") for b in data.get("batches",[])) else 4
    return {"ranked_batch_count":len(ids),"scope_label":f"{len(ids)} of 11 batches ranked: {', '.join(ids) or 'none'}; eligible scope {eligible} of 11; B6/B7 sealed; B8–B11 excluded", "completion_count":data.get("completion_count"),"schema":SCHEMA,"live":False,"banner":BANNER,"basis":GROSS,"slippage_note":SLIPPAGE,
            "ranking_label":RANKING,"lead_badge":BADGE,"forward_power_note":POWER,
            "state":"STALE-RED" if stale else "OBSERVED","source_as_of":data.get("source_as_of"),
            "generated_at":data.get("generated_at"),"generation_id":data.get("generation_id"),
            "freeze_sha":data.get("freeze_sha"),"chain_sha":data.get("chain_sha"),"writer":data.get("writer",UNKNOWN),
            "slippage_sign":SIGN,"ranked_batch_ids":[b["batch_id"] for b in data.get("batches",[]) if b.get("counted_for_ranking")],"eligible_scope_ids":["B1","B2","B3","B4","B5"] if any(b.get("batch_id")=="B2" and b.get("role_release_sha") for b in data.get("batches",[])) else ["B1","B3","B4","B5"],"coverage_note":"B6/B7 sealed; B8–B11 excluded; maximum 5 of 11 partial scope","data_refresh":"supervisor-owned; never triggered by request"}


def _render(data,k=40,findings=False,detail=None,stale=False):
    rows=data.get("ranking",[])[:k]
    out=shell("Strategy Findings" if findings else "Partial Two-Year Batch Study",stale=stale)
    c=context(data,stale)
    out.add("<p>Source as of "+text(c["source_as_of"])+"; generation "+text(c["generation_id"])+"; writer "+text(c["writer"])+"</p>")
    ids=[b["batch_id"] for b in data.get("batches",[]) if b.get("counted_for_ranking")]
    release=any(b.get("batch_id")=="B2" and b.get("role_release_sha") for b in data.get("batches",[]))
    ranked=len(ids)
    scope=f"{ranked} of 11 batches ranked: {', '.join(ids) or 'none'}; eligible scope {5 if release else 4} of 11; B6/B7 sealed; B8–B11 excluded"
    out.add("<p>"+scope+"</p>")
    out.add("<p>Stage D = RESEARCH RANKING ONLY; never a pass, promotion or go-live decision.</p>")
    out.add("<section><h2>What is tested / scope</h2><p>Bitfinex tBTCF0:USTF0 · EXT2-EXTERNAL · public trade-derived research, candles cross-check only; previously viewed (Study1).</p><p>Historical grammar: "+FORMULA+" (workload only; not selection N).</p>")
    for key in ("selection_base","variants_tried","n_selection","evaluated_count","excluded_count","remaining_count","alpha","selection_ledger_sha"):
        out.add("<p>"+key+": "+text(data.get(key))+"</p>")
    out.add("<p>Documentary margin $0.20–0.25 at 100x. R24 wider space NOT TESTED.</p></section>")
    out.add("<section><h2>R59 bars — producer reported, no viewer verdict</h2>")
    for key in ("pooled_gross_positive","backtest_trades_ge_200","positive_folds_ge_70pct","window_dominance_le_40pct","no_hard_stop_thesis_breach","selection_family_correction"):
        out.add("<p>"+key+": "+text(data.get("bars",{}).get(key))+"</p>")
    out.add("</section>")
    if not findings:
        out.add("<section><h2>Proposed calendar / actual roles</h2><table><tr><th>Batch / Sydney half-open instants</th><th>Reported status / ranking scope</th><th>Exposure / source</th></tr>")
        batches=data.get("batches",[])
        for i,(bid,start,end,note) in enumerate(CALENDAR):
            b=batches[i] if i<len(batches) else {}
            startdt=datetime.fromisoformat(start).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Australia/Sydney"))
            enddt=(datetime.fromisoformat(end).replace(tzinfo=timezone.utc)+timedelta(days=1)).astimezone(ZoneInfo("Australia/Sydney"))
            out.add("<tr><td>"+bid+" "+startdt.strftime("%d %b %Y %H:%M %Z")+" → "+enddt.strftime("%d %b %Y %H:%M %Z")+"</td><td>"+text(b.get("status"))+" / "+text(b.get("counted_for_ranking"))+"</td><td>"+escape(note)+"; previously viewed (Study1); "+text(b.get("role"))+"; "+text(b.get("stop_reason"))+"</td></tr>")
        out.add("</table><p>B2 sealed unless exact release; B6/B7 sealed; B8/B10/B11 NOT INCLUDED by default; B9 spent-span disclosure. Warmup/queue only from frozen chain.</p></section>")
        coverage=data.get("coverage") or {}
        out.add("<section><h2>Coverage donut / evaluated family</h2><p>"+text(coverage)+"</p>")
        stops=coverage.get("gradient_stops",[]) if isinstance(coverage,dict) else []
        safe=[]
        for stop in stops[:7]:
            if isinstance(stop,dict) and stop.get("status") in ("complete","running","failed_closed","not_started","sealed","not_included","UNKNOWN") and isinstance(stop.get("to_deg"),(int,float)) and 0<=stop["to_deg"]<=360:
                colors={"complete":"#32875d","running":"#517ca7","failed_closed":"#a44b54","not_started":"#626773","sealed":"#8b7eaa","not_included":"#725f4a","UNKNOWN":"#383e48"}
                safe.append(colors[stop["status"]]+" "+str(stop["to_deg"])+"deg")
        background="conic-gradient("+",".join(safe)+")" if safe else "#4b5563"
        out.add("<div style='width:120px;height:120px;border-radius:50%;display:grid;place-items:center;background:"+background+"'>"+str(ranked)+"/11 ranked</div></section>")
        out.add("<section id='leaderboard'><h2>Gross-positive research ranking</h2><p>"+" · ".join(f"<a href='?k={n}'>{n}</a>" for n in (30,40,50))+"</p><p>"+text(data.get("score_def"))+"</p><table><tr><th>Rank / strategy</th><th>Gross-positive batches / scope batches</th><th>Gross P&amp;L / expectancy / drawdown</th><th>Pin15 accounting</th><th>Per-batch gross statistics</th></tr>")
        for row in rows:
            cid=str(row["strategy_id"])
            out.add("<tr><td>"+text(row.get("rank"))+" <a href='?k="+str(k)+"&amp;cell="+escape(cid,quote=True)+"#detail'>"+escape(cid)+"</a> SCREEN ONLY</td><td>"+text(row.get("gross_positive_batches"))+" / "+text(row.get("scope_batches"))+"</td><td>"+text(row.get("gross_pnl_bp"))+" / "+text(row.get("gross_expectancy_bp"))+" / "+text(row.get("gross_drawdown_bp"))+"</td><td>"+text(row.get("accounting"))+"</td><td>")
            for batch in row.get("per_batch",[]):
                out.add("<p>"+text(batch)+"</p>")
            out.add("<p>Pooled statistics UNKNOWN unless explicit frozen method supplied.</p></td></tr>")
        out.add("</table></section>")
        out.add("<section><h2>Gross heatmap — missing/sealed hatched</h2><table>")
        for row in rows:
            out.add("<tr><th>"+text(row["strategy_id"])+"</th>")
            by={b.get("batch_id"):b for b in row.get("per_batch",[])}
            for bid,*_ in CALENDAR:
                value=by.get(bid,{}).get("gross_pnl_bp")
                shade=by.get(bid,{}).get("shade","unknown")
                color={"positive":"#275741","negative":"#662f38","neutral":"#454954"}.get(shade)
                style=" style='background:"+color+"'" if color and value is not None else " class='unknown'"
                out.add("<td"+style+" title='"+GROSS+"'>"+bid+": "+text(value)+"</td>")
            out.add("</tr>")
        out.add("</table></section><section><h2>Precomputed gross distribution — selection-biased top-K overlay</h2>")
        for bid,dist in data.get("distributions",{}).items():
            out.add("<h3>"+text(bid)+"</h3>")
            for bin_ in dist.get("bins",[]):
                out.add("<p>"+text(bin_)+"</p>")
                for field,color in (("all_width_pct","#557a9e"),("top_width_pct","#ffcb66")):
                    width=bin_.get(field)
                    if isinstance(width,(int,float)) and 0<=width<=100:
                        out.add("<div style='height:8px;background:"+color+";width:"+str(width)+"%'></div>")
        out.add("</section>")
    details=data.get("findings",[]) if findings else ([r for r in rows if str(r["strategy_id"])==str(detail)] if detail is not None else rows[:1])
    if not details:
        out.add("<p>nothing found yet — no verified Findings summary available</p>" if findings else "<p>no completed batches yet — no verified completed-batch summary available</p>")
    for row in details:
        out.add("<section id='detail'><h2>Strategy "+text(row.get("strategy_id"))+" — SCREEN ONLY</h2>")
        for key in ("source","window","series","freeze_sha","entry","trigger","composite_exit","profit_taking","thesis_cut","hard_stop","time_exit","risk","margin","leverage","concurrency","regime","accounting","folds","bars","provenance"):
            out.add("<p>"+key+": "+text(row.get(key))+"</p>")
        out.add("<h3>Cumulative gross net bp — precomputed equity, gaps broken</h3><svg viewBox='0 0 800 180' role='img' aria-label='gross equity'>")
        points=[]
        for point in row.get("equity_points",[])+[None]:
            if point is None:
                if points:
                    out.add("<polyline fill='none' stroke='#9bcfae' stroke-width='2' points='"+" ".join(points)+"'/>")
                    points=[]
            elif isinstance(point,list) and len(point)==2 and all(isinstance(n,(float,int)) and math.isfinite(n) for n in point):
                points.append(str(point[0])+","+str(point[1]))
        out.add("</svg>")
        out.add("</section>")
    out.add("<section><h2>Pin15 / denominators</h2><p>NO_FILL: never win, retained in capture. Unpriceable EXIT: LOW long/HIGH short at 1s exit; neither price: UNPRICED_WORST holding-window adverse extreme. Missing adverse extreme retains count, P&amp;L UNKNOWN. Frozen membership/denominators only. Strictly &gt;5% of filled trades flags BOARD REVIEW; exactly 5% does not.</p></section>")
    out.add("<section><h2>Forward comparison — NOT TESTED unless producer-bound</h2><p>"+POWER+"</p><p>(i) unchanged fill-model P&amp;L DECIDES forward; (ii) same-signal/time gross mark is ONLY for the 50% ratio. "+SIGN+"; measured slippage, information only.</p><p>"+text(data.get("forward"))+"</p><p>Negative (i) cannot become acceptance from a positive gross ratio. No viewer verdict or tile creation.</p></section>")
    out.add("<footer><p>Sources/hashes were verified offline, not by GET. Baseline "+BASELINE+"; Data Health KNOWN GAP / Fly HOLD unchanged.</p><pre>"+text(data.get("provenance"))+"</pre></footer></body></html>")
    return out.finish()


@dataclass(frozen=True)
class Entry:
    loaded: float
    source_time: float
    fresh: dict
    stale: dict
    metadata: dict


class SnapshotStore:
    """Atomic reference replacement only. NEVER reads, parses, locks or refreshes on GET."""
    def __init__(self,clock=time.monotonic,wall=time.time,bindings=None):
        self.clock,self.wall=clock,wall
        self.bindings=dict(bindings) if bindings else None
        required={"chain_sha","freeze_sha","rules_sha","board_sha","code_sha","accounting_policy_sha","selection_ledger_sha"}
        if self.bindings is not None and not required.issubset(self.bindings):
            raise ValueError("all immutable chain/oracle/rules/board/code/family bindings required")
        self.entry=None
        self.reason="writer UNKNOWN; no certified supervisor/cache loader; real-data refresh HOLD"

    @property
    def reason(self):
        return self._reason

    @reason.setter
    def reason(self,value):
        # Supervisor-only mutation: prebuild an honest bounded failure shell.
        # Fail closed to EMPTY rather than preserve attractive stale numbers
        # without their specific latch reason. HTTP only reads these bytes.
        self._reason=value
        if value is not None:
            metadata=self.entry.metadata if self.entry is not None else None
            self._failure_json=empty_response("data stale / "+str(value),metadata=metadata)
            self._failure_html=empty_response("data stale / "+str(value),html=True,metadata=metadata)

    def publish(self,raw):
        """Supervisor/fixture hook only; not exposed by HTTP or standalone auto-start."""
        if self.bindings is None:
            raise ValueError("chain card PENDING; no numeric rendering or interim writer")
        data=validate(raw)
        for key,value in self.bindings.items():
            if data.get(key)!=value:
                self.reason="prebound identity mismatch: "+key
                raise ValueError(self.reason)
        for b in data["batches"]:
            if b["batch_id"]=="B2" and b.get("counted_for_ranking") and (len(str(b.get("role_release_sha","")))!=64 or self.bindings.get("h1_role_release_sha")!=b.get("role_release_sha")):
                raise ValueError("B2 role release must match immutable approved binding")
        fresh,stale={},{}
        for state,target in ((False,fresh),(True,stale)):
            common=context(data,state)
            summary={**common,**{k:data.get(k) for k in ("batches","selection_base","variants_tried","n_selection","evaluated_count","excluded_count","remaining_count","alpha","coverage","bars")},"n_grammar":N}
            target["summary"]=bounded_json(summary)
            for k in (30,40,50):
                target["board:"+str(k)]=bounded_json({**common,"k":k,"rows":data.get("ranking",[])[:k],"score_def":data.get("score_def")})
                target["html:"+str(k)]=_render(data,k,stale=state)
            for row in data.get("ranking",[]):
                cid=str(row["strategy_id"])
                target["cell:"+cid]=bounded_json({**common,"cell":row})
                target["detail:"+cid]=_render(data,detail=cid,stale=state)
            for bid,dist in data.get("distributions",{}).items():
                target["distribution:"+bid]=bounded_json({**common,"batch_id":bid,"distribution":dist})
            target["distribution:"]=bounded_json({**common,"distribution":next(reversed(data.get("distributions",{}).values()),None)})
            target["findings"]=bounded_json({**common,"findings":data.get("findings",[]),"forward":data.get("forward"),"ledger":data.get("ledger"),"inventory":data.get("inventory")})
            target["findings_html"]=_render(data,findings=True,stale=state)
        source=datetime.fromisoformat(data["source_as_of"].replace("Z","+00:00")).timestamp()
        self.entry=Entry(self.clock(),source,fresh,stale,{"source_as_of":data["source_as_of"],"generation_id":data["generation_id"]})
        self.reason=None

    def response(self,key,html=False):
        entry=self.entry  # immutable snapshot captured by one atomic reference load
        if self.reason is not None:
            return self._failure_html if html else self._failure_json
        if entry is None:
            return empty_response(self.reason,html=html)
        source_age=self.wall()-entry.source_time
        age=self.clock()-entry.loaded
        if source_age>STALE_SEC or source_age < -5:
            return empty_response("data stale / source clock UNKNOWN; numeric results hidden",html=html,metadata=entry.metadata)
        stale=source_age>FRESH_SEC or age>=TTL or self.reason is not None
        value=(entry.stale if stale else entry.fresh).get(key)
        if value is None:
            return empty_response("unknown precomputed id/batch; no source lookup",html=html,status=404,metadata=entry.metadata)
        return value,200,"text/html; charset=utf-8" if html else "application/json"


def empty_response(reason,html=False,status=200,metadata=None):
    reason=str(reason or UNKNOWN)[:1024]
    if html:
        out=shell("Partial Two-Year Batch Study / Strategy Findings","<p>nothing found yet</p><p>no completed batches yet — no verified summary available</p><p>0 of 11 batches displayed in ranking: none — chain card PENDING; underlying completion UNKNOWN; index writer UNKNOWN</p><p>completion_count UNKNOWN · writer UNKNOWN · real-data refresh HOLD</p><p>"+escape(reason)+"</p><p>"+text(metadata)+"</p>")
        out.add("</body></html>")
        return out.finish(),status,"text/html; charset=utf-8"
    return bounded_json({"schema":SCHEMA,"live":False,"banner":BANNER,"basis":GROSS,"slippage_note":SLIPPAGE,"ranking_label":RANKING,"forward_power_note":POWER,"slippage_sign":SIGN,"state":UNKNOWN,"reason":reason,"completion_count":None,"displayed_ranked_batch_count":0,"ranked_batch_ids":[],"eligible_scope_ids":["B1","B3","B4","B5"],"coverage_note":"B6/B7 sealed; B8–B11 excluded; chain card PENDING","writer":UNKNOWN,"rows":[],"metadata":metadata}),status,"application/json"


STORE=SnapshotStore()


class Study:
    """Compatibility shim for narrow Flask routes; root is documentary only."""
    def __init__(self,root=None):
        pass


def render_study(study,k=40,cid=None):
    return STORE.response("detail:"+str(cid) if cid is not None else "html:"+str(k),html=True)[0]


def render_findings(study):
    return STORE.response("findings_html",html=True)[0]
