# NFM-5320 CI demonstrating run — WARN case (must NOT fail CI)

Synthetic artifact on demo branch `nfm5320-demo-warn` only: two weak
signals (pinned-seed, pareto-front-dump), no strong signal, no prereg
block. Expected: WARN with exit 0 — the Prereg Scope Guard job stays green;
NDE sign-off is procedural and happens in the Paperclip carrier thread.

seed: 1337
pareto front snapshot: demo
