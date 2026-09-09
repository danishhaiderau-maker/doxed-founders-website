"""Bounded legacy LAB accounting; gross simulation is never net trading PnL."""
import json
import math
from collections import Counter

TERMINAL_EXITS=frozenset({'THESIS_FAST_CUT','STOP_LOSS','PROFIT_LOCK_LADDER','TAKE_PROFIT','TIME_EXIT'})


def completed_legacy_lab(row):
    pnl=row.get('net_pnl_usd')
    return (row.get('filled') is True and row.get('exit_reason') in TERMINAL_EXITS
        and type(pnl) in (int,float) and math.isfinite(pnl))


class LabOutcomeReconciler:
    """Incremental read-only fixed file-generation scan. No partial totals.

    Any file change invalidates the snapshot. At most 256KiB/100 rows per
    refresh and 10,000 identities per generation are retained.
    """
    def __init__(self):
        self.binding=None; self.offset=0; self.rows={}; self.excluded=Counter(); self.failed=None

    def advance(self,path,identity,*,max_bytes=262144,max_rows=100):
        if (type(max_bytes) is not int or not 0<max_bytes<=262144
                or type(max_rows) is not int or not 0<max_rows<=100):
            raise ValueError('LAB_RECONCILIATION_BUDGET_INVALID')
        stat=path.stat()
        binding=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,
            json.dumps(identity,sort_keys=True,separators=(',',':')))
        if binding!=self.binding:
            self.binding=binding; self.offset=0; self.rows={}; self.excluded=Counter(); self.failed=None
        if not self.failed and self.offset<stat.st_size:
            with path.open('rb') as stream:
                stream.seek(self.offset); raw=stream.read(min(max_bytes,stat.st_size-self.offset))
            lines=raw.splitlines(keepends=True)
            consumed=0
            for line in lines[:max_rows]:
                if not line.endswith(b'\n'):
                    if consumed==0 or self.offset+len(raw)==stat.st_size:
                        self.failed='INCOMPLETE_OR_OVERSIZED_ROW'
                    break
                consumed+=len(line)
                try: row=json.loads(line)
                except (ValueError,UnicodeError): self.excluded['MALFORMED_ROW']+=1; continue
                if not isinstance(row,dict): self.excluded['MALFORMED_ROW']+=1; continue
                lane=row.get('research_lane')
                if not isinstance(lane,str) or not 0<len(lane)<=256:
                    self.excluded['LANE_INVALID']+=1; continue
                expected=identity['policies'].get(lane)
                if (row.get('schema')!='shadow_lane_outcome_v1' or row.get('collection_mode')!='LAB'
                        or row.get('epoch_id')!=identity['epoch']
                        or row.get('collection_epoch_id')!=identity['epoch']
                        or not expected or row.get('policy_version')!=expected['version']
                        or row.get('policy_signature')!=expected['signature']):
                    self.excluded['EPOCH_OR_POLICY_OR_SCOPE_MISMATCH']+=1; continue
                tid=row.get('study_id') or row.get('trade_id')
                if not isinstance(tid,str) or not 0<len(tid)<=256:
                    self.excluded['IDENTITY_MISSING']+=1; continue
                key=(lane,tid)
                selected={k:row.get(k) for k in ('filled','exit_reason','net_pnl_usd','direction')}
                if key in self.rows:
                    if self.rows[key]!=selected: self.rows[key]=None
                    else: self.excluded['DUPLICATE_IDENTICAL']+=1
                elif len(self.rows)>=10000: self.failed='IDENTITY_LIMIT'; break
                else: self.rows[key]=selected
            self.offset+=consumed
        after=path.stat()
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=binding[:5]:
            self.binding=None; self.failed='SOURCE_CHANGED'
        status='UNAVAILABLE' if self.failed else 'CURRENT' if self.offset==stat.st_size else 'BUILDING'
        result={}
        for lane in identity['policies']:
            selected=[r for (ln,_),r in self.rows.items() if ln==lane]
            completed=[r for r in selected if r is not None and completed_legacy_lab(r)]
            available=status=='CURRENT'
            pnl=sum(r['net_pnl_usd'] for r in completed)
            n=len(completed); wins=sum(r['net_pnl_usd']>0 for r in completed); losses=sum(r['net_pnl_usd']<0 for r in completed)
            result[lane]={'lab_accounting_status':status,'lab_accounting_reason':self.failed,
                'lab_pnl_source':'EXACT_EPOCH_POLICY_RECONCILED_LEGACY_GROSS',
                'lab_economics_basis':'LEGACY_GROSS_BEFORE_COSTS','lab_net_after_costs_usd':None,
                'lab_costs_status':'UNMODELED','lab_qualification_allowed':False,
                'lab_closes':n if available else None,'lab_wins':wins if available else None,
                'lab_losses':losses if available else None,'lab_zero_closes':n-wins-losses if available else None,
                'lab_net_pnl':pnl if available else None,'lab_gross_before_costs_usd':pnl if available else None,
                'lab_win_rate':100*wins/n if available and n else None,
                'lab_per_close_ev':pnl/n if available and n else None,
                'lab_incomplete_outcomes':sum(r is not None and r.get('filled') is True and not completed_legacy_lab(r) for r in selected) if available else None,
                'lab_conflicting_studies':sum(r is None for r in selected) if available else None,
                'lab_scan_excluded_counts':dict(self.excluded),
                'lab_scan_offset':self.offset,'lab_scan_bytes':stat.st_size}
        return result
