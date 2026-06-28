# Memory Index

## Project

- [NFM-44 Completion Summary](memory/NFM-44-completion-summary.md) — Blog module implementation verification (merged, all criteria met, production-ready)
- [NFM-44 CTO Final Comment](memory/NFM-44-CTO-final-comment.md) — CTO verification comment and final disposition (done)
- [NFM-47 ref-gp-fill Research Blocker](memory/nfm47-ref-gp-fill-research-blocker.md) — GitHub access blocking ref-gp-fill module research (awaiting unblock)
- [NFM-52 Architectural Review](memory/nfm52-arch-review.md) — REST API implementation: 3-layer architecture, TDD methodology, sample data MVP, complete (done)
- [NFM-82 Blog Admin Guidance](memory/nfm82-blog-admin-guidance.md) — Blog admin interface architectural guidance and CTO handoff (CTO complete, CPO implementation pending)
- [NFM-82 CPO Handoff](memory/nfm82-cpo-handoff.md) — CTO implementation complete with 3 commits, awaiting CPO to set up git repository and deploy (blocked - CPO action required)
- [NFM-82 CTO Final Disposition](docs/nfm82-cto-final-disposition.md) — Blog navigation implementation complete, awaiting git repository setup and deployment (delegated to CPO)
- [NFM-82 Final CTO Delegation](docs/nfm82-final-cto-disposition-delegation.md) — User requested delegation to CPO executed: CTO work complete, CPO team owns deployment operations (2026-06-13)
- [NFM-87 Hiring Decision](memory/nfm87-hiring-decision.md) — Data expert hiring decision: hybrid model over full-time hire, $259K savings, complete (done)
- [NFM-88 Verification Consultation](memory/consultations/nfm88-verification-consultation.md) — Lili consultation complete: verification requirements, P0-P4 gap framework, stakeholder requirements, reporting specification (done)
- [NFM-90 Resolution](memory/nfm90-resolution.md) — Cindy introduction blocked by OpenClaw protocol mismatch, manual forwarding workaround requested (blocked)
- [NFM-90 Final Disposition](docs/nfm90-final-disposition.md) — Complete disposition: gateway live but protocol mismatch blocks communication, manual intervention required (blocked)
- [NFM-90 Resolution](docs/nfm90-resolution.md) — Final resolution note: protocol mismatch confirmed, all documentation complete, manual forwarding path documented (blocked)
- [NFM-90 Timeout Incident](docs/nfm90-timeout-incident.md) — Direct assignment to Cindy failed (120s timeout), confirms protocol mismatch blocks all automated communication (blocked)
- [NFM-90 Final Disposition](docs/nfm90-final-disposition.md) — Complete disposition: gateway live but protocol mismatch blocks communication, manual intervention required (blocked)
- [NFM-105 Phase 3B Verification](memory/nfm105-verification.md) — Liveness continuation verification: Phase 3B complete, all 6 success criteria met, production-ready (done)

## Infrastructure

- **Paperclip API port**: `127.0.0.1:3100` — Never hardcode. Paperclip injects `PAPERCLIP_API_URL` env var. (The reference to `localhost:3456` in earlier sessions was an LLM hallucination.)
