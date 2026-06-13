# Memory Index

## Project

- [NFM-44 Completion Summary](memory/NFM-44-completion-summary.md) — Blog module implementation verification (merged, all criteria met, production-ready)
- [NFM-44 CTO Final Comment](memory/NFM-44-CTO-final-comment.md) — CTO verification comment and final disposition (done)
- [NFM-47 ref-gp-fill Research Blocker](memory/nfm47-ref-gp-fill-research-blocker.md) — GitHub access blocking ref-gp-fill module research (awaiting unblock)
- [NFM-52 Architectural Review](memory/nfm52-arch-review.md) — REST API implementation: 3-layer architecture, TDD methodology, sample data MVP, complete (done)
- [NFM-82 Blog Admin Guidance](memory/nfm82-blog-admin-guidance.md) — Blog admin interface architectural guidance and CTO handoff (CTO complete, CPO implementation pending)
- [NFM-87 Hiring Decision](memory/nfm87-hiring-decision.md) — Data expert hiring decision: hybrid model over full-time hire, $259K savings, complete (done)
- [NFM-88 Verification Consultation](memory/consultations/nfm88-verification-consultation.md) — Lili consultation complete: verification requirements, P0-P4 gap framework, stakeholder requirements, reporting specification (done)

## Infrastructure

- **Paperclip API port**: `127.0.0.1:3100` — Never hardcode. Paperclip injects `PAPERCLIP_API_URL` env var. (The reference to `localhost:3456` in earlier sessions was an LLM hallucination.)
