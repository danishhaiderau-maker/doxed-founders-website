import numpy as np
Z=np.load("/workspace/grok-strategist/scratch/sim-20261023/data.npz")
S,B,A,T,D=Z["S"],Z["B"],Z["A"],Z["T"],Z["D"]
TC=S+1.0  # decision/content time of each row
bid0,ask0=B[:,0,0],A[:,0,0]; mid=(bid0+ask0)/2
spread=(ask0-bid0)/mid*1e4
bs,as_=B[:,:5,1].sum(1),A[:,:5,1].sum(1); imb5=(bs-as_)/(bs+as_)
tts=T[:,1]/1000.0; tamt=T[:,2]; tpx=T[:,3]
cum=np.r_[0,np.cumsum(tamt)]
def flow(W):
    hi=np.searchsorted(tts,TC,'right'); lo=np.searchsorted(tts,TC-W*60,'right')
    return cum[hi]-cum[lo]
def minute_z(x, hours=6):
    # z of x vs minute samples strictly before the current minute over prior `hours`
    mins=(TC//60).astype(np.int64); m0=mins[0]; idx=mins-m0
    nm=idx[-1]+1; samp=np.full(nm,np.nan)
    # minute sample = last value observed in that minute
    last=np.r_[np.diff(idx)>0,True]; samp[idx[last]]=x[last]
    ok=~np.isnan(samp); v=np.where(ok,samp,0); c1=np.r_[0,np.cumsum(ok)]; c2=np.r_[0,np.cumsum(v)]; c3=np.r_[0,np.cumsum(v*v)]
    L=hours*60; hi=idx; lo=np.maximum(0,idx-L)  # minutes [lo,hi) strictly earlier
    n=c1[hi]-c1[lo]; s=c2[hi]-c2[lo]; ss=c3[hi]-c3[lo]
    mu=np.where(n>0,s/np.maximum(n,1),np.nan); var=np.where(n>1,(ss-n*mu*mu)/np.maximum(n-1,1),np.nan)
    sd=np.sqrt(np.maximum(var,1e-18)); z=(x-mu)/sd
    z[(n<L*0.8)]=np.nan
    return z
FZ={W:minute_z(flow(W)) for W in (1,3,5)}
j300=np.searchsorted(TC,TC-300,'right')-1
mom5=np.where(j300>=0,(mid/mid[np.maximum(j300,0)]-1)*1e4,np.nan)
# 1-min bars from mid (completed minutes only)
mins=(TC//60).astype(np.int64); m0=mins[0]; idx=mins-m0; nm=idx[-1]+1
hi_=np.full(nm,-np.inf); lo_=np.full(nm,np.inf); cl=np.full(nm,np.nan)
np.maximum.at(hi_,idx,mid); np.minimum.at(lo_,idx,mid)
last=np.r_[np.diff(idx)>0,True]; cl[idx[last]]=mid[last]
cl=np.array(__import__('pandas').Series(cl).ffill()) if np.isnan(cl).any() else cl
rng=np.where(np.isfinite(hi_),(hi_-lo_)/cl*1e4,np.nan)
rng=np.nan_to_num(rng,nan=0.0)
cr=np.r_[0,np.cumsum(rng)]; ach=np.r_[0,np.cumsum(np.r_[0,np.abs(np.diff(cl))])]
k=idx  # completed minutes are [k-30,k)
ok30=k>=31
ATR30=np.where(ok30,(cr[k]-cr[np.maximum(k-30,0)])/30,np.nan)
net=np.where(ok30,np.abs(cl[np.maximum(k-1,0)]-cl[np.maximum(k-31,0)]),np.nan)
path=np.where(ok30,ach[k]-ach[np.maximum(k-30,0)],np.nan)  # sum |diff| over closes k-31..k-1
ER30=np.where(path>0,net/np.where(path>0,path,1),0.0)
TREND=ER30>=0.35
# basis from deriv rows with s <= t-1 (i.e. s <= S)
dj=np.searchsorted(D[:,0],S,'right')-1
basis_raw=np.where(dj>=0,(D[np.maximum(dj,0),1]/D[np.maximum(dj,0),2]-1)*1e4,np.nan)
BZ=minute_z(basis_raw)
hourblk=((TC%86400)//21600).astype(int)
