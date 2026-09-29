"""Observed dispatch accounting, never trade completion or exhaustive fanout."""
from research.local_scan_reconciliation import _verify_page_ref


def verify_dispatch(root,plan_ref,admission_refs,child_refs,source,config,parent_refs):
    if len(admission_refs)>16 or len(child_refs)>32:
        return {'status':'UNKNOWN','reason':'DISPATCH_REFERENCE_LIMIT','qualification_eligible':False}
    plan=_verify_page_ref(root,plan_ref,source,config)
    keys=('scan_id','research_lane','policy_signature','job_key','payload_sha256')
    identity={key:plan.get(key) for key in keys}
    if len(parent_refs)!=1: raise ValueError('DISPATCH_PARENT_ADMISSION_UNKNOWN')
    parent=_verify_page_ref(root,parent_refs[0],source,config)
    if parent.get('decision_stage')!='SCAN_ADMISSION' or parent.get('scan_id')!=identity['scan_id']:
        raise ValueError('DISPATCH_PARENT_ADMISSION_CONFLICT')
    if (plan.get('decision_stage')!='SCAN_FANOUT_PLAN' or not all(isinstance(v,str) and v for v in identity.values())
            or identity['job_key']!=identity['research_lane']+':'+identity['scan_id']):
        raise ValueError('DISPATCH_PLAN_IDENTITY_INVALID')
    admissions=[]
    for ref in admission_refs:
        row=_verify_page_ref(root,ref,source,config)
        if row.get('plan_record_id')==plan['record_id']:
            if (row.get('decision_stage')!='SCAN_FANOUT_ADMISSION'
                    or row.get('admission_status') not in ('ENQUEUED','ADMISSION_UNKNOWN')):
                raise ValueError('DISPATCH_ADMISSION_CONFLICT')
            if {key:row.get(key) for key in keys}!=identity: raise ValueError('DISPATCH_ADMISSION_CONFLICT')
            admissions.append(row)
    if len(admissions)>1: raise ValueError('DISPATCH_ADMISSION_CONFLICT')
    resolutions=[]
    for ref in child_refs:
        row=_verify_page_ref(root,ref,source,config)
        binding=row.get('research_fanout_plan_reference')
        if not isinstance(binding,dict) or binding.get('plan_record_id')!=plan['record_id']: continue
        if (binding.get('schema')!='scan_fanout_plan_reference_v1' or binding.get('plan_identity')!=identity
                or binding.get('row_sha256')!=plan_ref['row_sha256']
                or binding.get('byte_offset')!=plan_ref['byte_offset'] or binding.get('row_length')!=plan_ref['row_length']
                or row.get('research_lane')!=identity['research_lane'] or row.get('policy_signature')!=identity['policy_signature']):
            raise ValueError('DISPATCH_CHILD_REFERENCE_CONFLICT')
        original=binding.get('source_identity') or {}
        if any(original.get(k)!=plan.get(k) for k in ('epoch_id','source_revision','deployed_revision','tile_config_signature')):
            raise ValueError('DISPATCH_CHILD_SOURCE_CONFLICT')
        if row.get('resolution_scope')=='LANE_ENTRY':
            resolution=row.get('entry_resolution')
            if resolution not in ('AWAITING','NO_ORDER','ORDER_SUBMITTED'): raise ValueError('DISPATCH_RESOLUTION_UNKNOWN')
            if row.get('entry_resolution_terminal') is not (resolution!='AWAITING'):
                raise ValueError('DISPATCH_RESOLUTION_CONFLICT')
            resolutions.append(resolution)
    terminals=set(resolutions)-{'AWAITING'}
    if len(terminals)>1: raise ValueError('DISPATCH_RESOLUTION_CONFLICT')
    return {'plan_record_id':plan['record_id'],'scan_id':identity['scan_id'],
        'admission_status':admissions[0].get('admission_status') if admissions else 'UNKNOWN',
        'entry_resolution':next(iter(terminals)) if terminals else 'AWAITING' if resolutions else 'UNKNOWN',
        'observed_only':True,'trade_completed':None,'qualification_eligible':False}
