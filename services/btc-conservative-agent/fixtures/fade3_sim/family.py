import numpy as np, itertools
from feat import *
gate=(spread<=2.0)
def entry_signals(mode,regime,W):
    z=FZ[W]
    if regime=="TREND": rg=TREND
    elif regime=="NOTREND": rg=~TREND&np.isfinite(ER30)
    else: rg=np.ones_like(TREND)
    base=gate&rg&np.isfinite(z)&np.isfinite(BZ)&np.isfinite(mom5)&np.isfinite(ATR30)
    if mode=="FOLLOW":
        L=base&(z>=2.5)&(mom5>0)&(imb5>=0.2)&(BZ<2)
        Sh=base&(z<=-2.5)&(mom5<0)&(imb5<=-0.2)&(BZ>-2)
    else:
        Sh=base&(z>=2.5)&(mom5>=2.0*ATR30)&(imb5<=-0.2)&(BZ>-2)
        L=base&(z<=-2.5)&(mom5<=-2.0*ATR30)&(imb5>=0.2)&(BZ<2)
    return L,Sh
ENTRIES=[("E1","FOLLOW","TREND",1),("E2","FOLLOW","TREND",3),("E3","FOLLOW","TREND",5),
         ("E4","FADE","NOTREND",1),("E5","FADE","NOTREND",3),("E6","FADE","NOTREND",5),
         ("E7","FOLLOW","ALL",3),("E8","FADE","ALL",3)]
EXITS=[dict(tp=tp,hard=h,mgmt=m,tmin=tm) for tp,h,m,tm in itertools.product((15,30),(25,35),("NONE","BE","THESIS"),(20,60))]
CANDS=[(e,x) for e in ENTRIES for x in EXITS]
t0,t1=TC[0],TC[-1]; span=t1-t0
SPLITS={"train":(t0,t0+0.6*span),"validate":(t0+0.6*span,t0+0.8*span),"holdout":(t0+0.8*span,t1+1)}
def cname(e,x): return f"{e[0]}-{e[1]}-{e[2]}-W{e[3]}|TP{x['tp']}-H{x['hard']}-{x['mgmt']}-T{x['tmin']}"
