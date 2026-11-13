#!/usr/bin/env python3
"""Frozen b2 c_T: analytic continuous Brownian drawdown, no MC/data/network.
Run: OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 THIS_FILE [--output-dir DIR]
Only writes local CSV/receipt. No author scripts run/imported. Seed unused (analytic).
"""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse,bisect,cmath,csv,hashlib,io,math,platform,time
from pathlib import Path
import numpy as np
import scipy
from scipy.linalg import eigh_tridiagonal
from scipy.optimize import brentq

SEED=20261110
SIMULATION_COUNT=0
MU=2.0/math.sqrt(365.0)
P=.95
HORIZONS=list(range(28,365,7))+[365]
C365_OPERATIONAL=22.958
RULES_SHA='a9db190ee251c66f4dc8cbd7f608d6464678e7cdf1609230d2e6584070c933aa'
AMEND_SHA='56173d1b971d0c64769167874b976661a881f1a74c94e212aa1bccdbc4f4eb74'
ADDENDUM_SHA='08a84c45fc7d52e9bd845d7329028ec1139ad7507b98e6ff90a22de6b51e8097'


def survival(a,mu,T,modes=80):
    """P(M_T<=a), X=mu*t+B_t, M_T=max(X_u-X_v), u<=v; sigma_day=1.
    Generator .5*d2-mu*d; reflect0, absorb a. Exact spectral coefficients.
    """
    if a<=0 or T<=0 or mu<0:
        raise ValueError('requires positive a,T and nonnegative mu')
    b=mu*a; scale=T/(a*a); terms=[]
    if abs(b-1)<1e-8:
        terms.append(3*math.exp(-b-.5*b*b*scale))
    elif b>1:
        h=brentq(lambda z:z/math.tanh(z)-b,1e-7,b+1,xtol=1e-13,rtol=1e-14)
        norm=(math.sinh(2*h)/(4*h)-.5)/(math.sinh(h)**2)
        terms.append(math.exp(-b)*math.sinh(h)/h/norm*math.exp(-.5*(b*b-h*h)*scale))
    else:
        z=brentq(lambda z:z/math.tan(z)-b,1e-8,math.pi-1e-8,xtol=1e-13,rtol=1e-14)
        norm=(.5-math.sin(2*z)/(4*z))/(math.sin(z)**2)
        terms.append(math.exp(-b)*math.sin(z)/z/norm*math.exp(-.5*(b*b+z*z)*scale))
    for m in range(1,modes):
        z=brentq(lambda z:z/math.tan(z)-b,m*math.pi+1e-8,(m+1)*math.pi-1e-8,xtol=1e-13,rtol=1e-14)
        norm=(.5-math.sin(2*z)/(4*z))/(math.sin(z)**2)
        terms.append(math.exp(-b)*math.sin(z)/z/norm*math.exp(-.5*(b*b+z*z)*scale))
    return math.fsum(terms)


def quantile(T,mu=MU,modes=80):
    # sqrt(T) only brackets a root; it does NOT determine a b2 table value.
    return brentq(lambda a:survival(a,mu,T,modes)-P,.1*math.sqrt(T),4*math.sqrt(T),xtol=1e-11,rtol=1e-14)


def zero_survival(a,T):
    # Independent driftless closed series, sup|B_t|.
    return 4/math.pi*math.fsum((-1)**j/(2*j+1)*math.exp(-(2*j+1)**2*math.pi**2*T/(8*a*a)) for j in range(120))


def inverse_laplace_hit_cdf(a,mu,T,A=24,n=1400):
    # Independent Fourier/Bromwich inversion of E[exp(-s*tau_a)]/s.
    def transform(s):
        q=cmath.sqrt(mu*mu+2*s)
        return math.exp(-mu*a)/(s*(cmath.cosh(q*a)-mu/q*cmath.sinh(q*a)))
    terms=[.5*transform(A/(2*T)).real]
    terms.extend((-1)**k*transform(complex(A,2*k*math.pi)/(2*T)).real for k in range(1,n+1))
    return math.exp(A/2)/T*math.fsum(terms)


def fd_survival(a,mu,T,cells):
    # Independent centered finite-difference method from prior HM receipt.
    h=a/cells; down=.5/h**2+mu/(2*h); up=.5/h**2-mu/(2*h)
    assert up>0
    upper=np.full(cells-1,up); upper[0]=1/h**2
    diag=np.full(cells,-1/h**2); off=np.sqrt(upper*down)
    sqrt_pi=np.ones(cells); sqrt_pi[1:]=np.sqrt(np.cumprod(upper/down))
    ev,V=eigh_tridiagonal(diag,off,lapack_driver='stev')
    return float(np.dot(V[0,:]*np.exp(ev*T),V.T @ sqrt_pi))


def omitted_tail_log10(a,T,modes):
    # |c_m|<=1/(m*pi/2-1/4), z_m>=m*pi. Geometric bound on exp(-k*m^2).
    k=.5*math.pi**2*T/a**2; n=modes
    return (-k*n*n-math.log(n*math.pi/2-.25)-math.log1p(-math.exp(-k*(2*n+1))))/math.log(10)


def frozen_lookup(elapsed_days,rows):
    if not math.isfinite(elapsed_days) or elapsed_days<0:
        raise ValueError('elapsed days must be finite and nonnegative')
    d=math.floor(elapsed_days)
    if d<28:return None
    if d>364:return rows[-1]
    return rows[bisect.bisect_right(HORIZONS,d)-1]


def sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args(); started=time.perf_counter()
    # MANDATORY STEP2 BEFORE other horizons and BEFORE output writing.
    c365=quantile(365)
    assert abs(c365-C365_OPERATIONAL)<=.01,'STOP: c365 reproduction failed'
    print(f'STEP2_PASS c365={c365:.12f} abs_error={abs(c365-C365_OPERATIONAL):.12g}',flush=True)
    rows=[]
    for T in HORIZONS:
        q=quantile(T); q40=quantile(T,modes=40); q120=quantile(T,modes=120)
        z=brentq(lambda a:zero_survival(a,T)-P,math.sqrt(T),4*math.sqrt(T),xtol=1e-11)
        z_spectral=quantile(T,mu=0)
        inv24=inverse_laplace_hit_cdf(q,MU,T,A=24)
        inv20=inverse_laplace_hit_cdf(q,MU,T,A=20)
        conv=max(abs(q-q40),abs(q-q120)); cdferr=abs(survival(q,MU,T)-P); inverr=abs(inv24-.05)
        assert conv/q<1e-8 and cdferr<1e-10 and inverr<2e-8
        assert abs(inv20-inv24)<2e-7 and abs(z-z_spectral)<1e-8 and 0<q<z
        rows.append(dict(T_days=T,c_T=C365_OPERATIONAL if T==365 else q,c_T_analytic=q,driftless_q95=z,
                         analytic_mode_convergence_abs=conv,cdf_residual_abs=cdferr,
                         inverse_laplace_cdf_error_abs=inverr,spectral_tail_bound_log10=omitted_tail_log10(q,T,80)))
    assert len(rows)==50
    for key in ('c_T','driftless_q95'):
        assert all(rows[i+1][key]>=rows[i][key] for i in range(len(rows)-1))
    assert frozen_lookup(27.999,rows) is None
    for d,t in ((28,28),(34.999,28),(35,35),(364.999,364),(365,365),(999,365)):
        assert frozen_lookup(d,rows)['T_days']==t
    assert frozen_lookup(365,rows)['c_T']==22.958
    fd_roots={}
    for cells in (600,1200):
        root=brentq(lambda a:fd_survival(a,MU,365,cells)-P,c365-.02,c365+.02,xtol=1e-8)
        fd_roots[cells]=root
        assert abs(root-c365)<.0001
    code_sha=sha256(__file__); out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    csv_path=out/'b2-cT-table-20261110.csv'; receipt_path=out/'hm-b2-cT-table-receipt-20261110.md'
    comments=[
        'b2_cT_version=20261110; Live OFF; mathematical calibration only',
        'model=X(t)=mu*t+B(t); continuous maximum drawdown; time unit=day; daily_sigma=1',
        f'S2_annual_sharpe=2; annualisation_days=365; mu_per_day={MU:.17g}; g=1; quantile=0.95',
        'basis=GROSS under R61; fee=0; funding=0; slippage=0; not LOSS-at-W; operational DD uses record(i)',
        f'seed={SEED}; seed_usage=UNUSED_analytic_no_RNG; simulation_count={SIMULATION_COUNT}; MC_SE=NOT_APPLICABLE_analytic',
        'method=analytic_reflected_Brownian_spectral_series_80_modes; checked_at_40_and_120_modes; inverse_Laplace_and_PDE_checks',
        f'code_sha256={code_sha}',f'rules_sha256={RULES_SHA}',
        f'R59_2prime_amendment_sha256={AMEND_SHA}',f'R59_2prime_addendum_sha256={ADDENDUM_SHA}',
        'lookup=d=floor(whole elapsed days); below28=NO_DECISION; 28<=d<=364:largest tabled T<=d; d>364:c365=22.958; NO_INTERPOLATION',
        'c_T=PRIMARY_binding_row; c_T_analytic=analytic_comparison; c365_PRIMARY=22.958_exact_operational_pin',
        'DD_365d<=22.958*sigma_hat_D remains continuing kill; BOTH review triggers:1.5*backtest_DD and1.5*c_T*sigma_hat_D; neither trigger alone is a kill',
        'table/code hashes pinned before ANY Stage-D byte; freeze mismatch=STOP; actual pre-D freeze enforcement NOT_TESTED']
    stream=io.StringIO(newline='')
    for c in comments:stream.write('# '+c+'\n')
    fields=list(rows[0]); w=csv.writer(stream,lineterminator='\n'); w.writerow(fields)
    for r in rows:
        w.writerow([r['T_days']]+[f'{r[k]:.12f}' for k in fields[1:5]]+[f'{r[k]:.12e}' for k in fields[5:]])
    csv_path.write_text(stream.getvalue(),encoding='utf-8'); csv_sha=sha256(csv_path)
    max_conv=max(r['analytic_mode_convergence_abs'] for r in rows)
    max_inv=max(r['inverse_laplace_cdf_error_abs'] for r in rows)
    max_tail=max(r['spectral_tail_bound_log10'] for r in rows)
    table='\n'.join(f'| {r["T_days"]} | {r["c_T"]:.9f} | {r["driftless_q95"]:.9f} |' for r in rows)
    receipt=f'''# HM frozen b2 c_T table — numerical receipt (10 Nov 2026)

## Outcome and authority

**COMPUTED / c365 REPRODUCED.** Box-only mathematical calibration, not strategy/system-health approval or run/freeze/arm permission. Live OFF. No laptop, network fetches, credentials, repository writes or market-data reads. Only the three requested box artifacts are produced. CSV header comments contain all model/seed/count/code/rules/lookup bindings; parse with `comment="#"`.

Governing rules: `/workspace/rules-keeper/RULES-R0-R61-20261110.md`, SHA256 `{RULES_SHA}`, R59(2') **L236–248**, especially L244 (both triggers), L245 (gross), L246–248 (floor row/pre-D pin/mismatch STOP). Original R59(2) is L235.
- Amendment `/workspace/boss-reviewed-cards/BOARD-RULING-R59-2-AMEND-cT-20261110.md`, SHA256 `{AMEND_SHA}`, L4–9: same model, table, floor row, continuing kill and gross basis.
- Addendum `/workspace/boss-reviewed-cards/BOARD-RULING-R59-2-AMEND-cT-ADDENDUM-20261110.md`, SHA256 `{ADDENDUM_SHA}`, L3–6: BOTH triggers, gross fee/funding/slippage zero, table metadata, floor row/c365 beyond364 and pre-D hash STOP.
- RK `/workspace/rules-keeper/rulings/RK-C1B-CT-PIN-R59-COMPAT-RULING-20261110.md`, SHA256 `84689d97820eeeeb24091155ca6e75b3b1583bc6cc607bf9be871816535b3e6e`, L6–10: same b2 and stricter row.
- Superseded draft `/workspace/boss-reviewed-cards/BOARD-RULING-B2-DD-cT-20261110.md`, SHA256 `cf53ea4d2a97b135ded073bacdd36061dd75f432df518a9332fa46a22598fd5d`, is history, NOT interpolation authority. Parent steering superseded interpolation explicitly. **NO interpolation is used or offered as binding.**

## 1. Identified model — not an invented substitute

Primary source `/workspace/boss-reviewed-cards/hm-r59-foldin-review-20261110.md`, SHA256 `001ae365869da8eba3b962de47e77e7874a7aa2913731ba59b1fb9485f4955d5`:
- L64–72: **X(t)=mu*t+B(t), mu=2/sqrt(365)={MU:.17g} per day**, daily sigma=1, initial equity/peak0. Fixed annual S2 (365-day convention), NOT 2/sqrt(T) or winner Sharpe.
- L68–70: **M_T=max_(0<=u<=v<=T)(X(u)-X(v)); c_T=q_0.95(M_T)**.
- L72–82: reflected diffusion generator .5*d2/dy2-mu*d/dy, reflecting0/absorbing a, survival=P(M_T<=a), c365 grids and finite-difference recipe.
- L80: c365 is **continuous-monitoring** q95, NOT the exact daily-sampled quantile. Preserving this is necessary to reproduce22.958.
- L88–98: daily-sigma normalization/daily executable MTM. Old LOSS-at-W and horizontal-envelope language L90/L94–96 is superseded by R61/R59(2'), NOT reintroduced.

Analytic documentary source `/workspace/grok-strategist/scratch/power-20261110/stagef/k1prime_analytic.py`, SHA256 `6594566b3efb83ee87732be9b9960eaf89dd852f42d9c11d9fe23598c3bd5dbd`, L1–8/L14–35 spectral law and L39–60 Laplace/driftless checks. Read only; its writing main was NOT run/imported. New self-contained script independently cross-checks documented law with prior HM finite differences.

Stage F r25 `/workspace/grok-strategist/reports/RUN-CARD-STAGE-F-FORWARD-PAPER-20261110-DRAFT.md` was read at SHA256 `a174dca4001c94876eb6d235648d830f9dbefa72eaa0b201218bf02fc8ec0677`: L266 no sqrt-rescale; L269 spectral/Laplace law; L299–306 sigma/g/marks; L317–319 record(i). Its horizontal decision barrier is superseded. K1-prime22.94285 is DISTINCT from b2 c36522.958.

**Daily step/marking:** time unit=one day; daily increments N(mu,1). Analytic law has no Euler/MC step. Reference q95 includes continuous peaks, while operational DD=max_(0<=u<=v<=d)(E_u-E_v) over chronological sealed DAILY executable MTM, includes E0=0 and open positions, no peak reset. Inherited day boundary midnight UTC =11:00 AEDT/10:00 AEST Sydney, not fixed Sydney wall time across DST. sigma_hat_D is frozen pre-forward sample DAILY sigma (ddof1) on the pre-D risk-equity path; g=1; no retuning/re-estimation/inflation. Actual path/sigma implementation needs separate frozen proof.

**Gross basis:** reference fee=0/funding=0/slippage=0 under R61, no W. Operational gate/kill DD uses unchanged fill-model **record(i)**, not ratio-only record(ii). Gross reference calibration does not remove actual fill slippage from paper decisions or change the engine.

## 2. Mandatory c365 reproduction BEFORE table

New analytic c365=**{c365:.12f}**. Absolute error versus22.958=**{abs(c365-22.958):.12g}**, within0.01. Gate assertion passed before other horizons/output writing. Difference from prior HM 2400-cell22.957843386 is {abs(c365-22.957843386):.12g}.
Independent finite-difference roots:600 cells **{fd_roots[600]:.12f}**,1200 cells **{fd_roots[1200]:.12f}**; each within0.0001 of analytic result and corroborating prior HM source table.

CSV **c_T** is binding. Row365 keeps fixed operational **22.958000000000**; separate **c_T_analytic** records {c365:.12f}. Weekly rows solve at their own T, never c365*sqrt(T/365). Daily-only MC was NOT substituted.

## 3. Method, precision, sanity

Analytic reflected-Brownian spectral series, survival root=.95. b=mu*a; real modes z*cot(z)=b, hyperbolic h*coth(h)=b if b>1, continuous zero mode at b=1. Coefficients use exact spatial integrals, no fitted distribution.80 modes, every horizon independently recomputed at40/120. Max root disagreement **{max_conv:.3g}**. Independent Fourier/Bromwich inversion of hitting-time Laplace transform gives max CDF error **{max_inv:.3g}**. Analytic omitted-real-mode tail bound log10 <=**{max_tail:.3f}**; floating arithmetic dominates. Independent driftless q95 uses 4/pi*sum_j(-1)^j/(2j+1)*exp(-(2j+1)^2*pi^2*T/(8a^2)), checked against zero-drift spectral roots.

**Seed={SEED}; simulation_count=0; Monte Carlo SE=NOT APPLICABLE (analytic; no RNG or paths).** Exact analytic law exists and was used as requested, with no fabricated MC count/SE. Deterministic numerical convergence/PDE/CDF checks are much tighter than the requested0.5% MC precision target. Rigorous floating-point interval certification NOT TESTED.

Primary c_T and driftless q95 monotone non-decreasing:PASS. Each positive-drift c_T below driftless q95:PASS. Survival root residual<1e-10; inverse-Laplace error<2e-8:PASS.50 horizons (49 weekly28..364 plus365):PASS. Lookup boundaries27.999/28/34.999/35/364.999/365/999:PASS. Python {platform.python_version()}, NumPy {np.__version__}, SciPy {scipy.__version__}; one BLAS/OMP thread. Command is in script docstring. Pure bounded CPU math, no market/search/GPU task.

## 4. PRIMARY frozen lookup and retained triggers

1. d=floor(whole elapsed days), finite/nonnegative; d<28 => **NO DECISION**. >=100 countable trades/other R59 bars separately necessary.
2.28<=d<=364 => **largest tabled T<=d**, its **c_T**. **NO interpolation, NO sqrt-rescale**. d34 uses28, d35 uses35.
3.d>364 => **c365=22.958**, no extrapolation. Floor fractional time FIRST:364.999 uses364,365 uses365.
4.Decision **DD_(i)(d)<=c_T*sigma_hat_D**, g=1,daily executable MTM.
5.**DD_365d<=22.958*sigma_hat_D stays continuing paper kill**. K1-prime/other kills unchanged.
6.**BOTH review triggers apply**:1.5*backtest_DD AND1.5*c_T*sigma_hat_D; either strict exceedance opens review. Neither trigger alone is a kill. Do not conflate triggers with b2 gate/continuing kill.
7.**CSV/code/rules hashes SHA-PINNED BEFORE ANY Stage-D byte** in Stage F r26/freeze pack; mismatch=STOP. Artifact ready for pin; actual pre-D ordering/installation/enforcement NOT TESTED. Computation is not freeze PASS.

## 5. Table (daily-sigma units)

| T days | binding c_T | driftless q95 comparison |
|---:|---:|---:|
{table}

## 6. Output hashes

- Code `{Path(__file__).resolve()}`, SHA256 **`{code_sha}`**, {Path(__file__).stat().st_size} bytes.
- CSV `{csv_path}`, SHA256 **`{csv_sha}`**, {csv_path.stat().st_size} bytes. Comments internally pin code SHA, S2 drift, seed, zero simulation count, gross basis, rules/amendment/addendum and lookup.
- Receipt `{receipt_path}`. Full SHA printed externally after write (no impossible embedded self-hash). Same code/environment regenerates deterministic CSV hash.

## 7. NOT TESTED / limitations

Real market/forward performance, selection, real daily marks, frozen estimated sigma, dependence/non-Gaussian tails/jumps/vol shifts, missing marks, real source data, laptop/runtime/CI/engine, pre-D execution ordering NOT TESTED. Model assumes continuous iid Brownian motion, known daily sigma. Daily-only maxima are smaller than continuous at known sigma; selected/estimated sigma and markets invalidate a universal5% guarantee. Fixed-horizon q95 is NOT a simultaneous95% boundary across repeated looks. Weekly floor row may be stricter than exact-day q95 between rows; no exact5% off-grid or adaptive-look claim. Family calibration, live eligibility, DONE/run/deploy/arm permission NOT inferred. Live OFF.
'''
    receipt_path.write_text(receipt,encoding='utf-8')
    print(f'CSV={csv_path}\nCSV_SHA256={csv_sha}\nCODE_SHA256={code_sha}')
    print(f'RECEIPT={receipt_path}\nRECEIPT_SHA256={sha256(receipt_path)}')
    for T in (28,91,182,364,365):
        r=next(r for r in rows if r['T_days']==T)
        print(f'T={T} c_T={r["c_T"]:.12f} analytic={r["c_T_analytic"]:.12f} driftless={r["driftless_q95"]:.12f}')
    print(f'max_mode_diff={max_conv:.3g} max_Laplace_error={max_inv:.3g} FD={fd_roots}')
    print(f'elapsed_seconds={time.perf_counter()-started:.3f}')

if __name__=='__main__':main()
