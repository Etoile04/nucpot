# NFM-5618 — second gated confirmatory run (full 7-element NSGA-II) — 2026-10-19

```prereg
ref: NFM-5618
```

Second gated confirmatory optimization run and the **first on the full
7-element problem** (NFM-5474 was Zr-only per NFM-4846). Criterion (2) is
evaluated under the **NFM-5480 condition (a) floored-relative plateau rule**
`HV(N) - HV(N-10) < max(0.01 x (HV(N) - HV(1)), 1e-3 abs)`, which replaces
the ill-conditioned relative rule whose failure mode is documented in
[2026-10-12-nfm5474-first-gated-confirmatory-run.md](2026-10-12-nfm5474-first-gated-confirmatory-run.md)
§Deviation.

## Gate lineage (dual-check gate, NFM-5312 / NFM-5320)

- Prereg document revision `31e1bcb0-d423-4cc7-a98e-8920764ebf50` on the
  NFM-5618 thread (8-key block: carrier, label, objectives, constraints,
  algorithm, seed, convergence, acceptance).
- NDE approval: `[PREREG-APPROVED]` marker comment `315b141d-0152-4495-8182-f7e33a4d0f47`
  posted from NDE own identity (`fe09f6ec-1998-46a0-96af-f0b26e79abdf`),
  2026-10-19T01:37Z, ruling on all four review axes (full-problem scope,
  fixed-N floored rule, stop-rule set, live sentinel). Review gate issue
  NFM-5622 closed done 2026-10-19.
- Local strict verify, pre-push:
  `python3 scripts/check_prereg.py --strict --verify --approver fe09f6ec-1998-46a0-96af-f0b26e79abdf experiments/nfm5618_confirmatory_full7.py`
  → **PASS (1 pass / 0 warn / 0 fail**; signals: confirmatory-label,
  promotion-claim, pinned-seed, pareto-front-dump, convergence-metrics).
- CI live-fire: prereg-gate workflow on push of this commit — see
  §Reproduction for the run link (green). The **first** push
  (run 39187309081) failed by design: this results document carried the
  literal `[CONFIRMATORY-RUN]` label with no prereg block, tripping the
  NFM-5312 hard-fail boundary. Fixed with the short-form citing block at the
  top of this document (the NFM-5474 pattern); the gate caught a real
  labeling violation on its own carrier — recorded here as live-fire
  evidence, not papered over.
- Sentinel (prereg algorithm key, axis 4): Petrov v3.3 `cb8b292d3` **not**
  on origin/main at run time (main @ `c79dc7409`; the three commits between
  `04edec97c` and `c79dc7409` are ops/docs — mirror-health alarm, NFM-5472
  research doc, SRE runbook — none touch the optimization surface or
  surrogate artifacts). Wrap sentinels `apps/api/tests/test_optimizer_wrap.py`
  last green 2026-10-19 (16 passed) on this unchanged surface; no rerun due.

## Run configuration

| Parameter | Value |
|-----------|-------|
| Algorithm | NSGA-II (pymoo 0.6.2) |
| Population x generations | 200 x 100 (fixed N = 100) |
| Seed | 42 |
| Crossover | SBX(prob=0.9, eta=15.0) |
| Mutation | PM(eta=20.0) |
| Sampling | LHS, eliminate_duplicates=True |
| Boundary | **Full 7-element**: Mo/Nb/V/Ti/Zr/Cr free, xl=0.0, xu=0.20 each; x_U = 1 − Σ solutes |
| Constraints | g1–g7 exactly as `nsga2_problem._compute_constraints` on main (PR #1471 = `f58c9e527`, NFM-5481) |
| Surrogate | origin/main v0.1 artifacts (phase_classifier_v01 + temp_predictor_v01); **active** (post-run self-check) |
| Environment | Python 3.14.2, numpy 2.4.2, macOS-27.0.1 arm64 |

Operator config identical to NFM-5474 and the NFM-5058 S2 golden harness for
comparability.

## Headline results

- **20,000 evaluations, wall-clock 1.407 s** (§5.3 target 60 s — met at 2.3%
  of budget; surrogate batch path).
- **Pareto front: 200/200 non-dominated solutions** (entire final population;
  expected for a 3-objective pop-200 NSGA-II with diversity preservation, and
  not among the preregistered stop-rules).
- Axis spans on the front: ρ_U 16.413202–18.713200 g/cm³, T_stable
  579.521495–672.098207 °C, fabricability 0.282899–0.743165.
- Role-spec floors respected everywhere on the front (g5/g6): min ρ_U
  16.413 ≥ 16.0; min T_stable 579.52 ≥ 500.
- HV(1) = 0.107001 → HV(100) = 0.745767; **total gain 0.638766** —
  well-conditioned, in direct contrast to NFM-5474's near-degenerate total
  gain 0.002381 that motivated the floored rule.
- Surrogate-active self-check (evaluated AFTER the run, per the NDE-approved
  protocol): `evaluator._use_ml_surrogate and evaluator._temp_model is not None`
  → **true**; no `surrogate_fell_back_to_synthetic` stop-rule.

## TOP-3 (max per objective, surrogate prediction details)

| Criterion | U | Mo | Nb | V | Ti | Zr | Cr | n>0.005 | ρ_U (g/cm³) | T_stable (°C) | Fab | phase prob |
|-----------|------|------|------|------|------|------|------|------|--------|--------|------|------|
| max ρ_U | 0.949999 | 0.044111 | 0.000179 | 0.000148 | 0.000248 | 0.000697 | 0.004618 | 2 | 18.713200 | 629.081994 | 0.388305 | 0.71244831 |
| max T_stable | 0.816157 | 0.000074 | 0.000729 | 0.000008 | 0.000043 | 0.182968 | 0.000021 | 2 | 16.528407 | 672.098207 | 0.337455 | 0.97299516 |
| max fabricability | 0.800408 | 0.089561 | 0.030588 | 0.027897 | 0.012475 | 0.002890 | 0.036181 | 6 | 17.414475 | 605.884352 | 0.743165 | 0.98040706 |

Phase probability is the `phase_classifier_v01` output as returned by
`predict_phase_batch` (verbatim). Physical sanity cross-check: the max-T_stable
pick is Zr-dominated (18.3 at.% Zr), consistent with the NFM-5474 Zr-only
front (T_stable 662.7–672.6 °C on the same v0.1 surface); the max-ρ_U pick
pushes U to the 0.95 ceiling (g2 active); element counts sit inside the g7
window [2, 6] for all three picks.

## HV trajectory (every 10 generations)

| Gen | 1 | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 | 100 |
|-----|------|------|------|------|------|------|------|------|------|------|------|
| HV | 0.107001 | 0.650957 | 0.712559 | 0.732659 | 0.738535 | 0.738406 | 0.746099 | 0.741143 | 0.750044 | 0.749585 | 0.745767 |

Normalized per-axis from the final population, reference point 1.1x per-axis
worst (identical normalization to NFM-5474). Note the plateau region is
**non-monotone**: 0.750044 (gen 80) > 0.749585 (gen 90) > 0.745767 (gen 100) —
see §Deviation analysis.

## Acceptance scorecard (prereg acceptance block)

| # | Criterion (prereg) | Observed | Verdict |
|---|--------------------|----------|---------|
| 1 | Pareto front ≥ 10 | 200 | **PASS** |
| 2 | Floored-relative plateau `HV(100)−HV(90) < max(0.01×(HV(100)−HV(1)), 1e-3)` | −0.003818 < 0.006388 (total gain 0.638766; threshold set by the 1% term, floor inactive) | **PASS** |
| 2s | Secondary: GD(front@gen-90, final) < 0.01 | **0.036996** | **UNMET — deviation, routed to NDE** (see below; never self-adjudicated per condition (c)) |
| 3 | No degeneracy stop-rule fired | `stop_rules_fired: []` (front size, T_stable span = 1.0 normalized, no early plateau: gen-20 gain 0.6056 < 0.99×0.6388, no fabricability pinning, surrogate active) | **PASS** |
| 4 | Wall-clock vs 60 s §5.3 target | 1.407 s | **PASS** |
| 5 | TOP-3 with surrogate prediction details | table above | **PASS** |

Scope guards (prereg acceptance key): this is the first gated confirmatory
run on the full 7-element problem — it does **not** claim U88.2Mo8.4Ti0.6V2.8
reproduction and is **not** a promotion or dispatch-candidate run; floors
g5/g6 still gate promotion (NFM-5480 condition (d)).

## Deviation analysis — criterion (2) secondary (GD)

**Observed:** GD(front@gen-90, final) = 0.036996 in normalized objective
space, vs the preregistered secondary threshold 0.01. The primary
floored-relative rule passes with margin (tail gain is negative). This
deviation is **routed to NDE for adjudication** per NFM-5480 condition (c) —
not self-adjudicated here.

Facts for the adjudication:

- The primary and secondary metrics disagree in this plateau regime. HV is
  flat-to-slightly-declining from gen ~60 (0.7461, 0.7411, 0.7500, 0.7496,
  0.7458 at gens 60–100), while the gen-90 front point-set sits a
  mean-nearest-distance of 0.037 (normalized, RMS) from the final front.
- Working hypothesis (labeled as hypothesis, for NDE to rule on): GD as
  implemented measures **point-set displacement** between the gen-90
  population's non-dominated subset and the final generation's front. With
  pop 200 on a 3-objective surface and **no external archive**, the final
  "front" is the last generation's sample; NSGA-II continues to reshuffle
  points along the front (crowding-driven replacement) even after dominated
  volume stops improving. HV is robust to this resampling; GD is not.
  Supporting observation: the non-monotone HV in the plateau region (gen 80
  covers more volume than gen 100) shows the final sample is not the
  best-ever front, which inflates GD(front@90, final) independent of
  convergence.
- The NFM-5474 precedent: the GD secondary PASSED there (0.003820, Zr-only
  2-variable problem) — the miss here is specific to the higher-dimensional
  front's resampling behavior, not to non-convergence of the search
  (evaluations, wall-clock, and stop-rules are all clean).
- Condition (c) forbids grandfathering for **relative-tail failures**; this
  is not a relative-tail failure — the floored primary passes. Whether the
  secondary miss gates the run, requires an archived-front GD variant, or is
  accepted with the resampling explanation is NDE's ruling to make.

### Adjudication — terminal (2026-10-19, NFM-5625 verdict `48b3fe8c`)

**Option (c) ACCEPTED — the deviation does not gate the run; no
archived-front re-run required; the run is credited as converged.** Ruled by
NDE (own identity) on a fresh bit-identical seed-42 reproduction plus
discriminating statistics (evidence script
`experiments/nfm5625_nde_gd_adjudication.py`, commit `9602a467f`), not on
the filed narrative. Decisive findings:

- **Stationary churn, not drift**: adjacent-window GDs flat at 0.028–0.032
  across gens 70–100, displacement symmetric (reverse GD 0.0354).
- **Points never leave the discovered front**: gen-90 and final populations
  sit 0.0104 / 0.0122 from the 5,839-point union-archive front while sitting
  0.0370 from each other — the population's *sample* rotates (17/200
  verbatim survivors under rank-1 crowding replacement); the front does not
  move.
- **Threshold below the sampling noise floor**: GD/NN-RMS = 0.87 — the
  displacement expected between two independent samplings of one front. The
  0.01 absolute threshold was implicitly calibrated on NFM-5474's 1-D curve
  front (spacing ~N⁻¹ ≈ 0.005) and does not transfer to this 2-D front
  manifold (spacing ~N^(−1/2) ≈ 0.035) — dimensional miscalibration, the
  same ill-conditioning species NFM-5480 established is replaced in the
  rule, not enforced.

**Binding forward conditions (successor to NFM-5480 (a), for the next
prereg generation and any ≥3-objective gated run):** (a′) replace the raw
GD secondary with the resampling-null form (`GD(front@N−10, final) ≤ RMS
NN-spacing of the final front`; reads 0.0370 ≤ 0.0424 here) or an
archive-referenced form — state which in the convergence key; (b′) cite
NFM-5625 and this section; (c′) no grandfathering on the new form; (d′)
floors g5/g6 still gate promotion; run stays non-promotion/non-dispatch.

**KR-OPT-Q4 S3 (NFM-5312) credited 1.0** by the verdict: full 7-element
problem, own carrier, gate green end-to-end, sole deviation resolved through
the prereg's own sanctioned adjudication path — compliance, not
circumvention.

## Follow-ups

- ~~NDE adjudication of the GD-secondary deviation~~ — **RESOLVED
  2026-10-19**: NFM-5625 verdict `48b3fe8c`, Option (c) accepted, run
  credited as converged (see §Deviation analysis / Adjudication).
- KR-OPT-Q4 S3 (NFM-5312): **credited 1.0** per the NFM-5625 verdict — this
  run passed the dual-check gate end-to-end on its own carrier NFM-5618
  (strict local verify PASS + CI green, one by-design live-fire catch); the
  condition-(c) routing with a filed, evidence-backed adjudication is
  compliance, not circumvention.
- Forward: the next prereg generation adopts the NFM-5625 condition (a′)
  resampling-null or archive-referenced GD secondary (binding on any
  ≥3-objective gated run), citing NFM-5625 and this doc.
- Sentinel note (run-time state, superseded on any future surface change):
  Petrov v3.3 `cb8b292d3` remained off origin/main for the entire run
  window.

## Reproduction

From the repo root of worktree branch `NFM-5596-okr-weekly-standup` at the
commit carrying this file:

```bash
PYTHONPATH=apps/api/src python3 experiments/nfm5618_confirmatory_full7.py
```

Emits the machine-readable results JSON on stdout (label
`[CONFIRMATORY-RUN]`, carrier NFM-5618, prereg revision `31e1bcb0`, approval
marker `315b141d` embedded; the script refuses to run without the marker).
Deterministic: seed 42, fixed operator config, surrogate artifacts from
origin/main default paths. Gate evidence: strict local verify command above
(PASS) and the prereg-gate CI run triggered by this commit's push
(`.github/workflows/prereg-gate.yml`, fires on push and pull_request).

## Appendix — full Pareto front (200 rows, 6 dp)

Columns: rho_U (g/cm3), T_stable (degC), fabricability — maximization sense.

```
17.414475	605.884352	0.743165
16.528407	672.098207	0.337455
18.360651	654.247838	0.282899
18.003827	579.521495	0.652579
16.413202	665.710635	0.392972
18.7132	629.081994	0.388305
16.4303	663.333743	0.40117
17.92922	657.857488	0.310049
18.353525	651.553299	0.302541
17.169673	662.000116	0.357346
18.16723	591.528348	0.588572
18.420005	621.004548	0.470614
16.712792	658.620115	0.42404
17.198251	641.672554	0.528128
17.509836	603.904655	0.731156
17.31815	663.852277	0.347095
17.727262	659.961834	0.31844
16.47194	665.105113	0.396227
18.119107	587.486592	0.593256
18.689684	631.906221	0.385708
16.93162	661.653886	0.394692
16.589493	667.811822	0.36286
17.590632	603.139534	0.716339
17.827111	597.634024	0.672018
16.758204	642.626695	0.532406
17.548891	662.781055	0.325634
17.375744	642.467525	0.483577
17.346294	640.191981	0.486211
16.551534	666.592327	0.382565
17.137543	631.912696	0.609564
17.152423	635.828515	0.579498
17.814746	595.619157	0.680871
17.529779	611.572745	0.674331
17.717876	593.012102	0.698645
18.399473	605.129328	0.508782
17.397453	607.912711	0.737563
18.14027	621.677388	0.474279
18.525401	623.716065	0.450281
17.539696	661.306379	0.328838
18.499777	622.820361	0.455359
17.846317	594.665675	0.677068
17.964021	613.619734	0.564328
17.980892	581.588059	0.659806
17.798996	593.881987	0.691551
18.37698	608.713565	0.518548
16.577754	668.949815	0.364237
17.684776	612.224227	0.653536
16.751512	647.709706	0.505614
17.021955	634.0803	0.605877
17.63229	640.908519	0.467177
17.064884	653.118869	0.435713
17.747099	601.227695	0.685475
17.345152	624.093177	0.582263
18.47304	617.488121	0.477637
17.536002	603.122616	0.728001
18.663974	629.560041	0.404287
16.777761	643.572848	0.527064
17.273204	613.281612	0.710702
17.314299	666.496189	0.322
18.073912	588.907127	0.615153
18.445188	632.254181	0.414776
17.983176	581.627052	0.65917
16.79531	647.980379	0.499942
16.654002	649.154705	0.499766
16.531094	669.20007	0.364879
17.619343	615.602562	0.648543
18.152882	655.980946	0.298213
17.462506	645.19695	0.42034
16.679798	670.930924	0.340758
16.672971	670.221476	0.342969
17.896784	584.080413	0.667505
17.847383	590.634866	0.68151
18.011393	582.239653	0.649933
17.715341	609.444566	0.640063
16.683382	655.306663	0.445264
17.097874	652.262652	0.436804
17.08781	632.618356	0.614343
17.707497	625.482697	0.521921
17.441625	631.348228	0.541622
16.492459	666.079264	0.387554
18.039097	627.988121	0.45826
17.657751	660.521778	0.332144
17.335004	643.156429	0.464969
17.051026	637.188352	0.561384
17.242149	628.317467	0.600469
17.837158	602.163018	0.647881
18.302115	644.973647	0.353267
17.998887	589.741536	0.642832
18.093843	584.346379	0.619882
17.039137	636.312686	0.577926
18.015619	592.072811	0.621247
18.277793	596.766674	0.552851
16.790265	642.051748	0.529503
17.742	600.543076	0.691713
18.435127	625.231707	0.451025
18.168665	627.631222	0.464863
17.50325	659.639419	0.340485
18.556556	628.205875	0.418079
16.981311	640.365866	0.546395
18.607904	626.468763	0.426782
16.947207	639.201322	0.569463
17.57065	602.686516	0.717475
17.491841	605.76514	0.723435
16.840226	641.175853	0.537227
17.584955	614.906062	0.661364
17.47575	656.691017	0.377008
18.299713	653.941281	0.292506
17.762991	604.693123	0.663046
17.459921	611.104438	0.700577
16.902158	648.76356	0.453959
18.28706	653.692971	0.294197
18.53414	631.187853	0.410686
16.611907	666.511079	0.381529
17.6703	601.623153	0.705374
16.889749	638.947806	0.575862
18.368361	609.643374	0.512705
17.480508	657.429754	0.370315
17.867531	637.097804	0.407238
16.730637	665.536271	0.380718
18.481337	641.257825	0.334704
18.323879	620.148127	0.480436
16.518274	671.488367	0.342945
16.519118	667.615891	0.375031
18.657464	633.609849	0.383367
16.906482	645.491206	0.480057
17.287412	612.965213	0.707195
18.184137	609.084267	0.534774
17.439652	605.312692	0.740134
17.304954	639.852538	0.511038
17.943156	610.472706	0.590717
18.508543	619.719907	0.460959
16.664272	648.401727	0.498559
16.809834	648.671197	0.496382
16.66584	669.382142	0.349105
18.33447	605.756581	0.525764
18.440949	637.833072	0.368576
16.881024	639.733601	0.553365
17.495543	649.857522	0.398525
18.245668	655.263893	0.293161
18.589472	626.301745	0.430712
18.360388	653.589916	0.287195
17.635678	659.240323	0.34373
18.128729	656.811534	0.298193
17.234024	664.678334	0.35015
17.787047	635.098447	0.443669
17.804176	614.505322	0.604598
18.027651	586.200766	0.6321
17.251465	614.397914	0.672458
17.568849	616.545922	0.65077
17.123645	634.352639	0.594398
17.094948	654.885981	0.41314
18.286106	596.261773	0.549482
18.049922	583.885904	0.634973
16.858311	646.624538	0.488702
18.044461	657.172211	0.305816
18.565949	619.422888	0.440897
18.230174	597.990415	0.552149
17.911254	614.158695	0.572986
17.158228	636.48927	0.573595
17.86289	607.566147	0.623518
18.113486	590.811707	0.599161
18.025338	583.240777	0.63639
18.200405	599.152629	0.56765
16.829435	647.029102	0.510003
18.31268	624.361643	0.459503
17.216777	630.176344	0.616346
18.550325	632.703542	0.403915
17.804051	618.841561	0.57662
16.818015	645.628801	0.514616
18.409204	618.257122	0.476132
17.2767	650.68658	0.429943
17.34434	612.281221	0.719498
16.692203	662.296921	0.397141
17.693005	660.717549	0.318926
18.004812	586.794316	0.632335
17.891158	647.197037	0.386392
17.266369	644.487036	0.494883
16.991242	630.861307	0.623929
18.147378	586.975832	0.596935
16.518986	668.431383	0.371404
17.970464	622.51269	0.524075
16.512316	665.933359	0.388876
18.052254	595.415912	0.597977
17.754711	601.330525	0.683063
18.405049	616.909503	0.481615
16.961931	654.415019	0.429296
18.046683	611.516987	0.558755
18.49212	615.733701	0.476033
16.871045	651.135111	0.47131
17.739193	598.201162	0.694644
18.509467	626.122011	0.44085
16.69821	668.462952	0.353208
16.839294	656.098358	0.432623
18.055241	583.290508	0.630938
18.189647	598.576543	0.571444
18.48281	617.689077	0.475804
16.834961	646.382769	0.494567
17.342233	660.402928	0.352178
16.998278	646.582221	0.478502
18.118646	599.956084	0.570625
```
