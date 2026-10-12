# NFM-5474 — first gated confirmatory run (Zr-only NSGA-II live-fire) — 2026-10-12

```prereg
ref: NFM-5474
```

**[CONFIRMATORY-RUN]** executed 2026-10-12 under prereg NFM-5474 revision `44cdc406`
(NDE `[PREREG-APPROVED]` marker comment `74a2b4e7`, 2026-10-12T03:25Z).
Acceptance criteria (1)/(3)/(4)/(5) PASS; criterion (2) HV plateau **NOT MET** —
filed deviation, NDE disposition requested (NFM-5480). Run NOT self-certified as accepted.
Harness: `experiments/nfm5474_confirmatory_zr_only.py` (prereg block embedded verbatim).

## Gate lineage (dual-check gate, NFM-5312 / NFM-5320)

- **Check 1 (process):** `[PREREG-APPROVED]` marker comment `74a2b4e7` authored by the
  NDE agent identity (`fe09f6ec…`) on the NFM-5474 thread over prereg document revision
  `44cdc406`. Strict approver match verified locally via `--verify --approver`.
- **Check 2 (artifact):** full 8-key prereg block embedded verbatim in the harness;
  local `check_prereg.py --strict` PASS over harness + this document. CI live-fire =
  `prereg-gate.yml` on the push carrying this file (run URL recorded in the
  `[CONFIRMATORY]` results comment on NFM-5474). This document carries the
  short-form citing block above.
- **v3.3 sentinel (working rule 6):** Petrov v3.3 promotion `cb8b292d3` NOT on
  origin/main at execution time (origin/main head `bc4f48217`; checked pre-prereg and
  re-checked immediately pre-execution) — optimizer-wrap tests not re-run, per rule.

## Run configuration

| field | value |
| --- | --- |
| label | `[CONFIRMATORY-RUN]` |
| carrier | NFM-5474 (prereg revision `44cdc406`) |
| boundary | Zr-only (NFM-4846 NDE ruling): Mo/Nb/V/Ti/Cr bounds collapsed to 0; U ∈ [0.60, 0.90] at.%, Zr ≤ 0.20 (g1–g3) |
| surrogate | origin/main v0.1 artifacts (`phase_classifier_v01` + `temp_predictor_v01`), lazy-loaded |
| surrogate active | **true** (self-verified — silent synthetic fallback would have fired a stop-rule) |
| algorithm | NSGA-II (pymoo 0.6.2): pop 200 × 100 gens, SBX(prob 0.9, eta 15.0), PM(eta 20.0), LHS, eliminate_duplicates — operator config identical to the NFM-5058 S2 golden harness |
| seed | 42 |
| environment | Python 3.14.2, pymoo 0.6.2, numpy 2.4.2, macOS-27.0.1-arm64-arm-64bit-Mach-O |
| wall-clock | 1.129 s vs 60.0 s target (§5.3); 20000 evaluations |

## Headline results

- Pareto front size: **200 / 200** — entire final population non-dominated
  (expected for a 1-D collapsed decision space: every point trades along the curve)
- Per-axis front spans:
  ρ_U [16.307346, 17.665779] g/cm³ ·
  T_stable [662.720840, 672.585528] °C ·
  fabricability [0.307149, 0.338110]
- Hypervolume (per-axis min–max normalized vs final population, ref point 1.1³):
  gen 1 = 0.696719 → final = 0.699101 (total gain 0.002381);
  gen-1 HV is already 99.66% of final
- Plateau: tail gain (final 10 gens) 0.000137 = **5.74% of total gain** (threshold < 1%) → criterion NOT met
- Generational distance (gen-90 front vs final front, normalized): 0.003820
- Stop-rules fired: **none** (front < 10 · T_stable-axis degenerate · early plateau < gen 20 ·
  degenerate total gain · fabricability pinned — all silent)

## TOP-3 (max per objective, surrogate prediction details)

| criterion | composition (at.frac.) | ρ_U (g/cm³) | T_stable (°C) | fabricability | phase prediction |
| --- | --- | --- | --- | --- | --- |
| max_rho_U | U 0.9000 / Zr 0.1000 | 17.665779 | 662.720840 | 0.307149 | [0.99968901] |
| max_T_stable | U 0.8000 / Zr 0.2000 | 16.307346 | 672.585528 | 0.338110 | [0.97549516] |
| max_fabricability | U 0.8000 / Zr 0.2000 | 16.307346 | 672.585528 | 0.338110 | [0.97549516] |

Max-T_stable and max-fabricability coincide at the Zr upper bound (U 0.80 / Zr 0.20) —
expected: with one free solute variable the T_stable and fabricability gradients align
along the curve; ρ_U anchors the opposite end at the U ceiling.

## HV trajectory (every 10 generations)

[0.698697, 0.698768, 0.699195, 0.699426, 0.699192, 0.698981, 0.698888, 0.69863, 0.698964, 0.699101]

Non-monotone in the tail (±8e-4 oscillation across gens 10–100) — the residual gain is an
asymptotic crawl, not an ongoing convergence event.

## Acceptance scorecard (prereg acceptance block)

| # | criterion | verdict |
| --- | --- | --- |
| 1 | Pareto front ≥ 10 non-dominated | **PASS** (200) |
| 2 | HV plateau criterion met | **NOT MET** (tail fraction 5.74% > 1%) |
| 3 | No degeneracy stop-rule fired | **PASS** (all 4 silent) |
| 4 | Wall-clock vs 60 s target | **PASS** (1.129 s) |
| 5 | TOP-3 with surrogate details | **PASS** |

Overall: **4/5 criteria met**; criterion (2) filed as a deviation. The run is not
self-certified as accepted — disposition is routed to NDE (NFM-5480).

## Deviation analysis — criterion (2)

- LHS sampling of 200 points on the 1-D collapsed Zr-only decision space covers the trade
  curve almost completely before evolution begins: gen-1 HV = 99.66% of final.
- The preregistered **relative** criterion (final-10-gen gain < 1% of **total** gain) is
  ill-conditioned when total gain is near-degenerate (total gain = 0.002381,
  0.179% of the 1.331 normalized reference volume): it compares an asymptotic crawl against
  itself, and the ±8e-4 trajectory oscillation is of the same order as the tail gain.
  *(Volume-fraction correction per NDE verdict `fe595b7d`, NFM-5480; the originally filed
  "~0.24%" was the fraction of unit volume.)*
- Supplementary convergence evidence (reported for NDE's disposition, not as a
  self-clearing substitute): absolute tail gain 0.000137 ≈ 0.010%
  of normalized volume; GD(gen-90, final) = 0.003820; full-range spans
  on all three axes; phase predictions at both TOP-3 anchors high (0.9755 at U-20Zr,
  0.9997 at U-10Zr).
- Per prereg protocol the criterion is not revised post-hoc on the run owner's authority:
  disposition options (accept filed deviation vs amend criterion + re-run) are with NDE
  in NFM-5480.

## Follow-ups

- **NFM-5480** — NDE disposition on criterion (2). NFM-5474 is blocked on this issue.
- **NFM-5481** — role-spec floor deltas (g5–g7 + U-window alignment), the binding
  condition of the prereg approval; must land in code before any promotion or
  dispatch-candidate run cites this work.

## Reproduction

```bash
PYTHONPATH=apps/api/src python3 experiments/nfm5474_confirmatory_zr_only.py
```

Deterministic (seed 42, pinned operator config); surrogate artifacts
from origin/main. Full machine-readable JSON digest is mirrored in the `[CONFIRMATORY]`
results comment on NFM-5474.

## Appendix — full Pareto front (200 rows, 6 dp)

```
     rho_U    T_stable   fabricability
 16.307346   672.585528   0.338110
 17.665779   662.720840   0.307149
 16.957771   670.121854   0.326270
 16.936650   670.275201   0.326733
 17.118173   668.777659   0.322566
 16.778725   671.247852   0.330019
 17.075460   669.165954   0.323585
 17.322719   666.659517   0.317337
 17.555519   663.972133   0.310632
 17.311271   666.787251   0.317645
 17.218268   667.789169   0.320081
 17.478194   664.870675   0.312953
 16.638399   671.869956   0.332689
 17.106080   668.889740   0.322857
 17.494452   664.681147   0.312473
 17.658111   662.806210   0.307398
 17.135745   668.611856   0.322140
 17.403428   665.739864   0.315107
 16.767102   671.307677   0.330249
 17.470920   664.955482   0.313167
 17.086567   669.067033   0.323323
 17.225238   667.716594   0.319902
 16.873796   670.698693   0.328078
 17.171912   668.259939   0.321249
 17.210632   667.868155   0.320275
 16.825689   670.990064   0.329074
 17.609145   663.357531   0.308964
 16.902727   670.509848   0.327465
 17.461256   665.068135   0.313449
 17.632502   663.093241   0.308222
 17.596879   663.497209   0.309350
 16.421202   672.445858   0.336380
 16.734033   671.469461   0.330895
 17.243217   667.527359   0.319440
 16.544153   672.173269   0.334355
 16.358953   672.535051   0.337343
 17.448216   665.220057   0.313827
 17.541604   664.133052   0.311057
 16.790639   671.184903   0.329782
 16.511755   672.258075   0.334905
 16.432116   672.426943   0.336207
 17.126970   668.695087   0.322353
 16.500266   672.285862   0.335097
 17.028673   669.566389   0.324674
 16.439881   672.412877   0.336083
 16.718076   671.543145   0.331201
 17.206669   667.908935   0.320376
 17.187575   668.103263   0.320858
 17.563094   663.884741   0.310399
 16.533497   672.202222   0.334537
 17.485495   664.785553   0.312738
 17.006878   669.743788   0.325172
 17.331238   666.563958   0.317106
 17.298863   666.924730   0.317978
 16.450298   672.393207   0.335916
 17.549470   664.042029   0.310817
 16.973125   670.006882   0.325930
 17.384483   665.958207   0.315640
 17.163390   668.344122   0.321460
 17.570390   663.800734   0.310174
 17.366868   666.160044   0.316130
 17.654117   662.850774   0.307528
 16.474337   672.344277   0.335526
 16.652151   671.818317   0.332438
 16.980911   669.947454   0.325756
 16.402489   672.475991   0.336674
 16.990929   669.869876   0.325532
 16.868577   670.731662   0.328187
 16.998771   669.808274   0.325355
 17.520562   664.377226   0.311693
 17.456341   665.125414   0.313592
 17.500662   664.608802   0.312289
 17.649783   662.899226   0.307668
 17.579249   663.698956   0.309900
 16.710935   671.575211   0.331338
 17.513226   664.462526   0.311914
 17.089016   669.045031   0.323265
 17.602622   663.431730   0.309170
 17.416245   665.591552   0.314744
 16.691415   671.660025   0.331707
 16.983929   669.924211   0.325689
 17.337346   666.495184   0.316940
 17.410416   665.659062   0.314910
 17.269898   667.241424   0.318744
 17.200581   667.971279   0.320530
 16.893876   670.568720   0.327653
 17.125042   668.713259   0.322400
 17.488725   664.747892   0.312643
 16.750864   671.388666   0.330568
 17.372111   666.100104   0.315984
 16.883397   670.637165   0.327875
 17.148223   668.492031   0.321834
 17.472682   664.934933   0.313115
 17.427982   665.455383   0.314410
 17.291669   667.003953   0.318169
 17.068261   669.229287   0.323755
 16.742532   671.429059   0.330730
 16.462922   672.368131   0.335712
 17.179236   668.186984   0.321066
 17.151465   668.460627   0.321755
 17.637236   663.039966   0.308071
 17.198103   667.996561   0.320593
 17.181861   668.160709   0.321001
 17.256924   667.381193   0.319084
 16.684716   671.688188   0.331833
 17.621253   663.220240   0.308581
 17.096087   668.981095   0.323096
 16.833848   670.942620   0.328907
 16.931471   670.311950   0.326845
 17.584329   663.640710   0.309742
 16.390620   672.493613   0.336859
 17.390872   665.884703   0.315461
 17.432387   665.404209   0.314283
 17.646102   662.940446   0.307786
 17.604792   663.407030   0.309101
 17.284342   667.084270   0.318363
 16.411507   672.461830   0.336533
 16.961949   670.090865   0.326178
 17.291111   667.010084   0.318184
 17.615090   663.290045   0.308776
 17.052021   669.369873   0.324134
 16.842498   670.891443   0.328729
 17.036018   669.505285   0.324505
 17.378379   666.028286   0.315810
 17.361512   666.221159   0.316278
 17.585894   663.622785   0.309693
 16.527743   672.217423   0.334635
 17.508086   664.522345   0.312067
 16.371052   672.520171   0.337159
 16.589822   672.037088   0.333560
 16.849838   670.847308   0.328577
 16.625265   671.917470   0.332927
 17.566115   663.849937   0.310306
 16.918081   670.405419   0.327135
 17.425074   665.489145   0.314493
 17.627062   663.154592   0.308396
 16.862293   670.770918   0.328318
 17.574604   663.752290   0.310044
 17.526596   664.307118   0.311512
 17.157118   668.405581   0.321615
 17.590581   663.569150   0.309547
 17.274467   667.191884   0.318624
 16.484927   672.321132   0.335351
 17.626611   663.159685   0.308410
 16.818661   671.030298   0.329217
 17.339379   666.472249   0.316885
 17.238026   667.582282   0.319574
 17.398557   665.796113   0.315245
 17.349156   666.361635   0.316617
 16.600001   672.004002   0.333380
 17.344500   666.414371   0.316745
 17.265041   667.293911   0.318871
 16.726697   671.503686   0.331036
 17.438714   665.330638   0.314102
 16.662084   671.779804   0.332255
 16.309894   672.583525   0.338073
 16.558659   672.132154   0.334105
 17.248900   667.466955   0.319292
 16.479269   672.333619   0.335445
 16.924565   670.360438   0.326995
 16.705158   671.600745   0.331448
 16.725373   671.509801   0.331062
 16.606966   671.980780   0.333256
 16.666646   671.761772   0.332170
 16.908087   670.473726   0.327350
 16.745144   671.416483   0.330679
 16.517958   672.242576   0.334801
 17.536383   664.193557   0.311215
 16.657518   671.797636   0.332339
 16.677127   671.719513   0.331975
 16.761320   671.336856   0.330363
 16.756423   671.361277   0.330459
 17.252138   667.432409   0.319208
 17.396112   665.824319   0.315314
 17.279526   667.136845   0.318491
 17.545880   664.083546   0.310927
 16.521786   672.232841   0.334736
 17.328141   666.598745   0.317190
 16.966259   670.058660   0.326082
 17.307284   666.831539   0.317752
 17.325758   666.625482   0.317255
 17.592510   663.547101   0.309487
 16.613513   671.958512   0.333139
 17.230080   667.665909   0.319778
 16.925444   670.354297   0.326976
 17.391322   665.879523   0.315448
 17.047233   669.410713   0.324246
 16.380260   672.508059   0.337018
 17.192846   668.049972   0.320725
 17.260179   667.346254   0.318999
 17.351608   666.333815   0.316550
 17.419968   665.548393   0.314638
 17.343372   666.427127   0.316776
 17.143146   668.540995   0.321959
 16.339980   672.556042   0.337628
 16.817171   671.038756   0.329247
 16.319561   672.575465   0.337931
 16.561519   672.123816   0.334056
 17.062559   669.279008   0.323888
 17.411992   665.640808   0.314865
```
