# Lighthouse a11y audit — 2026-08-28T08:27:42.855Z

- Base URL: `http://localhost:3301`
- Min score: **90**
- Report dir: `/Users/lwj04/.paperclip/instances/default/workspaces/98fc3168-be45-4673-808e-22238b366352/nucpot/.worktrees/NFM-3794-lighthouse-a11y/apps/web/qa-artifacts/lighthouse/2026-08-28T08-26-53-361Z`

| Route | Score | Pass | Failing audits |
| --- | --- | --- | --- |
| `/admin/ontology` | 100 | ✅ | — |
| `/admin/ontology/sample-type-id` | 100 | ✅ | — |
| `/admin/ontology/sample-type-id/edit` | 100 | ✅ | — |

## Re-run history (in this PR)

| Run timestamp | Score (List / Detail / Edit) | Failing audits |
| --- | --- | --- |
| 2026-08-28T06:34:28Z (initial, on `88769adc`) | 95 / 95 / 95 | `target-size` on all 3 + `color-contrast` on the desktop /login button |
| 2026-08-28T08:27:42Z (final, on `3d1eaf3a`) | 100 / 100 / 100 | — |

## What changed between runs

Two latent bugs in the 88769adc fix surfaced once the report was re-run against the
final tree per the CTO's request on NFM-3791 (2026-08-28 17:30Z):

1. **`py-1.5` was a no-op.** Tailwind v4 in this repo only emits integer `py-*`
   classes from the JIT scan, so `py-1.5` was never added to the stylesheet. The
   inline `<a>` stayed at the default ~17px clickable height — failing target-size.
   Fixed in `3d1eaf3a` by switching to `py-2` and wrapping with `inline-flex
   items-center` so vertical padding actually expands the layout box.
2. **The desktop `/login` button had its colors reset by antd.** antd's runtime
   `:where(.css-plsjn) a` rule overrides both `text-white` and `bg-blue-600` to
   colorLink `#1668dc` on transparent, giving 3.42:1 on the `#101828` header.
   Fixed in `3d1eaf3a` by pinning `!bg-blue-600` and `!text-white` (Tailwind `!`
   important modifier). The mobile `/login` button got the same treatment to
   prevent a re-run regression when the mobile menu becomes the audit viewport.

After these two corrections, the report now agrees with the source: zero failing
audits and 100/100 on all 3 ontology routes. AC threshold is ≥90, so NFM-3794 is
closed at 100.