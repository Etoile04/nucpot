# NFM-5320 CI demonstrating run — hard-fail case (must fail CI)

Synthetic artifact on demo branch `nfm5320-demo-fail` only: strong
confirmatory signal with no parseable prereg block. Expected: FAIL with
exit 1 — the Prereg Scope Guard job must go red.

[CONFIRMATORY-RUN] demo artifact — deliberately carries no prereg block.
