
#### Tile A (H9) cap 10 - exit comparison (base entry `BASE_OFFSET_0.15_w234_s25_i180_TTL3600`; best design per exit family among the top-40 by in-sample EV)

| Exit | Family | Fills | Win % | EV bp/fill | EV bp/signal | 1h-cluster 95% CI | Gave back % | Max DD $ | Fixed-rule test days bp/signal |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| `BASE_TIME_60M_HARD40` (deployed rule) | BASE | 337 | 49.3 | 3.05 | 1.29 | [-4.66, 11.21] | 40.1 | -3.002 | 1.8 |
| `CUT-9BP_10M_T60` | CUT | 407 | 35.6 | 4.49 | 2.3 | [-1.31, 12.42] | 41.3 | -3.013 | 1.98 |
| `TIME_90M_HARD40` | TIME | 296 | 47.6 | 4.69 | 1.75 | [-5.05, 15.59] | 43.9 | -3.355 | 2.64 |
| `ATR_STOP_2.0_T60` | ATR_STOP | 373 | 42.9 | 3.57 | 1.67 | [-3.31, 10.55] | 39.7 | -1.887 | 2.69 |
| `BE_ARM12BP_FLOOR+2_T60` | BE | 386 | 55.2 | 3.0 | 1.46 | [-3.26, 9.97] | 31.6 | -2.739 | 1.87 |
| `ATR_TRAIL_1.5_ARM0.5_T60` | ATR_TRAIL | 432 | 41.7 | 2.66 | 1.45 | [-1.96, 7.56] | 44.0 | -1.365 | 1.9 |
| `NOPROGRESS_30M_MFE<2_T60` | NOPROGRESS | 346 | 47.1 | 2.99 | 1.3 | [-4.31, 10.86] | 37.9 | -2.554 | 1.72 |
| `LADDER_8>2_15>8_25>15_T60` | LADDER | 430 | 63.7 | 1.99 | 1.08 | [-2.78, 7.13] | 24.9 | -2.098 | 1.4 |
| `GIVEBACK_KEEP30_ARM10BP_T60` | GIVEBACK | 412 | 66.5 | 2.01 | 1.04 | [-3.38, 7.71] | 22.6 | -2.454 | 1.3 |
| `TP_2.5ATR_T60` | TP | 388 | 56.2 | 2.03 | 0.99 | [-3.79, 7.82] | 33.8 | -2.736 | 1.63 |
| `PARTIAL50_AT1ATR_THEN_BE+1_T60` | PARTIAL | 403 | 72.0 | 1.89 | 0.96 | [-2.75, 6.47] | 17.4 | -1.464 | 1.48 |
| `COMPOSITE_ATR_STOP1.5_BE1ATR+1_CHAND1.5_ARM1_T60` | COMPOSITE | 459 | 44.0 | 1.31 | 0.76 | [-2.47, 5.61] | 33.6 | -1.505 | 1.36 |

Nested walk-forward (exit chosen on prior days, scored on the next day; 83 designs, deflated Sharpe {'expected_max_sharpe_under_null': 0.157, 'deflated_sharpe_prob': 0.3017}): selected exits per fold = ['CUT-8BP_5M_T60', 'CUT-8BP_10M_T60', 'CUT-1.0ATR_10M_T60']; nested OOS 290 fills, 2.49 bp/fill, 1.23 bp/signal, CI [-2.58, 8.29] vs deployed rule on the same days 232 fills, 4.54 bp/fill, 1.8 bp/signal.

Early-cut cost/benefit (`cut_cost_benefit`): `{"CUT-6BP_2M_T60": {"cut_trades": 203, "would_have_recovered": 100, "losses_saved": 103, "cost_bp_total": 4224.6, "benefit_bp_total": 2796.1, "net_bp_total": -1428.6}, "CUT-6BP_5M_T60": {"cut_trades": 265, "would_have_recovered": 132, "losses_saved": 133, "cost_bp_total": 4986.1, "benefit_bp_total": 3350.4, "net_bp_total": -1635.7}, "CUT-6BP_10M_T60": {"cut_trades": 347, "would_have_recovered": 183, "losses_saved": 164, "cost_bp_total": 6271.5, "benefit_bp_total": 4175.7, "net_bp_total": -2095.8}, "CUT-6BP_30M_T60": {"cut_trades": 432, "would_have_recovered": 238, "losses_saved": 194, "cost_bp_total": 8230.0, "benefit_bp_total": 4630.7, "net_bp_total": -3599.3}, "CUT-8BP_2M_T60": {"cut_trades": 145, "would_have_recovered": 64, "losses_saved": 81, "cost_bp_total": 2888.4, "benefit_bp_total": 2149.7, "net_bp_total": -738.7}, "CUT-8BP_5M_T60": {"cut_trades": 195, "would_have_recovered": 94,`

Missed opportunity (unfilled signals): `{"BASE_OFFSET_0.15_w234_s25_i180_TTL3600": {"signals": 795, "unfilled": 176, "fill_rate_pct": 77.9, "unfilled_taker_ev_bp": 34.4, "missed_bp_total": 6054.7}, "TAKER_AT_SIGNAL": {"signals": 795, "unfilled": 1, "fill_rate_pct": 99.9, "unfilled_taker_ev_bp": null, "missed_bp_total": 0.0}, "OFFSET_0.05_w234_s25_i180_TTL3600": {"signals": 795, "unfilled": 69, "fill_rate_pct": 91.3, "unfilled_taker_ev_bp": 42.6, "missed_bp_total": 2939.7}, "OFFSET_0.10_w234_s25_i180_TTL3600": {"signals": 795, "unfilled": 124, "fill_rate_pct": 84.4, "unfilled_taker_ev_bp": 35.76, "missed_bp_total": 4433.8}, "OFFSET_0`

#### CFM / Tile C cap 3 - exit comparison (base entry `BASE_OFFSET_0.10_no_chase_TTL1800`; best design per exit family among the top-40 by in-sample EV)

| Exit | Family | Fills | Win % | EV bp/fill | EV bp/signal | 1h-cluster 95% CI | Gave back % | Max DD $ | Fixed-rule test days bp/signal |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| `BASE_TIME_90M_HARD40` (deployed rule) | BASE | 71 | 54.9 | 9.3 | 1.25 | [-5.21, 27.8] | 35.2 | -0.824 | 0.55 |
| `NOPROGRESS_20M_MFE<2_T90` | NOPROGRESS | 72 | 55.6 | 10.12 | 1.38 | [-4.46, 28.9] | 34.7 | -0.824 | 0.55 |
| `BE_ARM1.0ATR_FLOOR+1_T90` | BE | 101 | 54.5 | 6.63 | 1.27 | [-3.8, 22.65] | 40.6 | -0.734 | 0.34 |
| `BASE_TIME_90M_HARD40` | TIME | 71 | 54.9 | 9.3 | 1.25 | [-5.21, 27.8] | 35.2 | -0.824 | 0.55 |
| `CUT-6BP_2M_T90` | CUT | 97 | 36.1 | 6.73 | 1.24 | [-2.65, 20.14] | 36.1 | -0.754 | 0.22 |
| `ATR_STOP_2.0_T90` | ATR_STOP | 79 | 41.8 | 7.87 | 1.18 | [-7.19, 27.81] | 49.4 | -0.714 | 0.7 |
| `PARTIAL50_AT1ATR_THEN_BE+1_T90` | PARTIAL | 105 | 82.9 | 2.92 | 0.58 | [-3.35, 11.03] | 11.4 | -0.627 | -0.04 |
| `TP_1.5ATR_T90` | TP | 107 | 74.8 | 2.7 | 0.55 | [-2.31, 7.72] | 15.0 | -0.603 | 0.54 |
| `ATR_TRAIL_1.0_ARM1.0_T90` | ATR_TRAIL | 109 | 71.6 | 2.56 | 0.53 | [-3.71, 10.26] | 18.3 | -0.634 | 0.31 |

Nested walk-forward (exit chosen on prior days, scored on the next day; 83 designs, deflated Sharpe {'expected_max_sharpe_under_null': 0.318, 'deflated_sharpe_prob': 0.032}): selected exits per fold = ['GIVEBACK_KEEP30_ARM10BP_T90', 'CUT-6BP_2M_T90', 'NOPROGRESS_20M_MFE<2_T90']; nested OOS 74 fills, -1.94 bp/fill, -0.37 bp/signal, CI [-8.79, 6.96] vs deployed rule on the same days 49 fills, 4.38 bp/fill, 0.55 bp/signal.

Early-cut cost/benefit (`cut_cost_benefit`): `{"CUT-6BP_2M_T90": {"cut_trades": 128, "would_have_recovered": 75, "losses_saved": 53, "cost_bp_total": 4399.4, "benefit_bp_total": 1794.9, "net_bp_total": -2604.5}, "CUT-6BP_5M_T90": {"cut_trades": 168, "would_have_recovered": 97, "losses_saved": 71, "cost_bp_total": 5735.1, "benefit_bp_total": 2434.1, "net_bp_total": -3301.0}, "CUT-6BP_10M_T90": {"cut_trades": 183, "would_have_recovered": 107, "losses_saved": 76, "cost_bp_total": 6037.9, "benefit_bp_total": 2597.4, "net_bp_total": -3440.5}, "CUT-6BP_30M_T90": {"cut_trades": 228, "would_have_recovered": 141, "losses_saved": 87, "cost_bp_total": 7109.2, "benefit_bp_total": 2824.1, "net_bp_total": -4285.1}, "CUT-8BP_2M_T90": {"cut_trades": 95, "would_have_recovered": 56, "losses_saved": 39, "cost_bp_total": 3503.5, "benefit_bp_total": 1241.2, "net_bp_total": -2262.3}, "CUT-8BP_5M_T90": {"cut_trades": 146, "would_have_recovered": 84, "loss`

Missed opportunity (unfilled signals): `{"BASE_OFFSET_0.10_no_chase_TTL1800": {"signals": 528, "unfilled": 243, "fill_rate_pct": 54.0, "unfilled_taker_ev_bp": 19.09, "missed_bp_total": 4638.4}, "TAKER_AT_SIGNAL": {"signals": 528, "unfilled": 2, "fill_rate_pct": 99.6, "unfilled_taker_ev_bp": null, "missed_bp_total": 0.0}, "OFFSET_0.05_no_chase_TTL1800": {"signals": 528, "unfilled": 143, "fill_rate_pct": 72.9, "unfilled_taker_ev_bp": 23.64, "missed_bp_total": 3381.2}, "OFFSET_0.15_no_chase_TTL1800": {"signals": 528, "unfilled": 316, "fill_rate_pct": 40.2, "unfilled_taker_ev_bp": 16.92, "missed_bp_total": 5348.2}, "OFFSET_0.25_no_chase`

#### Tile B (H10) cap 3, 8.93 s latency - exit comparison (base entry `TAKER_AT_SIGNAL`; best design per exit family among the top-40 by in-sample EV)

| Exit | Family | Fills | Win % | EV bp/fill | EV bp/signal | 1h-cluster 95% CI | Gave back % | Max DD $ | Fixed-rule test days bp/signal |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|
| `BASE_TIME_60M_HARD40` (deployed rule) | BASE | 93 | 50.5 | 2.96 | 0.02 | [-7.31, 13.54] | 36.6 | -1.1 | 0.01 |
| `TP_10BP_T60` | TP | 304 | 78.9 | 2.11 | 0.04 | [-1.96, 4.59] | 11.5 | -0.785 | 0.02 |
| `PARTIAL50_AT12BP_THEN_BE+1_T60` | PARTIAL | 137 | 70.1 | 3.26 | 0.03 | [-0.97, 8.56] | 19.0 | -0.408 | -0.02 |
| `TIME_120M_HARD40` | TIME | 52 | 57.7 | 7.65 | 0.02 | [-7.5, 27.07] | 28.8 | -0.518 | 0.04 |
| `BE_ARM8BP_FLOOR+1_T60` | BE | 149 | 51.0 | 2.24 | 0.02 | [-4.07, 11.22] | 32.9 | -0.853 | 0.02 |
| `NOPROGRESS_20M_MFE<2_T60` | NOPROGRESS | 102 | 46.1 | 2.52 | 0.02 | [-6.85, 12.23] | 32.4 | -1.1 | 0.02 |
| `CUT-6BP_10M_T60` | CUT | 167 | 29.9 | 1.51 | 0.02 | [-1.94, 6.89] | 35.3 | -0.892 | 0.01 |
| `COMPOSITE_BP_STOP25_P50@15BP_BE+1_GB50_CUT9BP5M_T60` | COMPOSITE | 269 | 48.0 | -0.15 | -0.0 | [-2.74, 3.31] | 18.6 | -0.872 | 0.03 |
| `GIVEBACK_KEEP30_ARM10BP_T60` | GIVEBACK | 167 | 67.1 | -0.48 | -0.0 | [-4.5, 4.02] | 16.2 | -0.988 | -0.01 |
| `ATR_STOP_1.5_T60` | ATR_STOP | 156 | 30.8 | 0.21 | 0.0 | [-4.97, 6.8] | 26.9 | -0.769 | -0.01 |
| `ATR_TRAIL_1.0_ARM1.0_T60` | ATR_TRAIL | 199 | 66.8 | -1.34 | -0.02 | [-5.06, 2.22] | 16.1 | -1.15 | -0.01 |
| `BE8+2+GB50ARM10_T60` | BE8+2+GB | 207 | 65.7 | -1.44 | -0.02 | [-4.66, 1.96] | 16.9 | -1.352 | -0.01 |

Nested walk-forward (exit chosen on prior days, scored on the next day; 83 designs, deflated Sharpe {'expected_max_sharpe_under_null': 0.2527, 'deflated_sharpe_prob': 0.4421}): selected exits per fold = ['TP_5BP_T60']; nested OOS 47 fills, 1.34 bp/fill, 0.03 bp/signal, CI [-1.17, 3.06] vs deployed rule on the same days 36 fills, 0.61 bp/fill, 0.01 bp/signal.

Early-cut cost/benefit (`cut_cost_benefit`): `{"CUT-6BP_2M_T60": {"cut_trades": 1171, "would_have_recovered": 512, "losses_saved": 659, "cost_bp_total": 22995.3, "benefit_bp_total": 19544.5, "net_bp_total": -3450.8}, "CUT-6BP_5M_T60": {"cut_trades": 1720, "would_have_recovered": 758, "losses_saved": 962, "cost_bp_total": 31297.2, "benefit_bp_total": 28202.3, "net_bp_total": -3094.9}, "CUT-6BP_10M_T60": {"cut_trades": 2139, "would_have_recovered": 959, "losses_saved": 1180, "cost_bp_total": 39017.7, "benefit_bp_total": 33872.9, "net_bp_total": -5144.9}, "CUT-6BP_30M_T60": {"cut_trades": 2616, "would_have_recovered": 1216, "losses_saved": 1400, "cost_bp_total": 43265.8, "benefit_bp_total": 37619.8, "net_bp_total": -5646.0}, "CUT-8BP_2M_T60": {"cut_trades": 830, "would_have_recovered": 345, "losses_saved": 485, "cost_bp_total": 16570.5, "benefit_bp_total": 14230.8, "net_bp_total": -2339.7}, "CUT-8BP_5M_T60": {"cut_trades": 1336, "would`