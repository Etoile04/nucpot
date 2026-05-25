# NucPot Testing & Fix Plan

## Bug Fix (Critical)
- [x] profile PATCH API: `.single()` → `.maybeSingle()` + admin client + auto-create

## Test Infrastructure
- Install vitest + @testing-library/react + msw (mock Service Worker)
- Configure vitest for Next.js

## Test Matrix

### API Routes (11 endpoints)

| # | Endpoint | Method | Tests |
|---|----------|--------|-------|
| 1 | `/api/stats` | GET | Returns stats object |
| 2 | `/api/potentials` | GET | List with pagination, type filter, element filter, search |
| 3 | `/api/potentials/[id]` | GET | Returns single potential, 404 for missing |
| 4 | `/api/potentials/upload` | POST | Auth required, validation, creates potential |
| 5 | `/api/auth/login` | POST | Valid credentials, invalid credentials |
| 6 | `/api/auth/register` | POST | Valid registration, duplicate username |
| 7 | `/api/auth/profile` | GET | Returns profile, 401 without auth |
| 8 | `/api/auth/profile` | PATCH | Updates fields, rejects invalid fields, 401 |
| 9 | `/api/auth/template` | GET | zh/en templates, auto-fills data |
| 10 | `/api/auth/upload-proof` | POST | File upload, auth required |
| 11 | `/api/admin/contributions` | GET | Auth + admin required |
| 12 | `/api/admin/stats` | GET | Auth + admin required |

### Frontend Pages (9 pages)

| # | Page | Tests |
|---|------|-------|
| 1 | `/` (Home) | Renders stats, links work |
| 2 | `/browse` | Lists potentials, pagination, filters |
| 3 | `/search` | Search input, results display |
| 4 | `/potential/[id]` | Displays detail, 404 state |
| 5 | `/login` | Login form, error handling |
| 6 | `/profile` | Auth guard, edit mode, save |
| 7 | `/upload` | Auth guard, form fields, auto-save, validation |
| 8 | `/admin` | Auth + admin guard, contribution list |
| 9 | `/about` | Static content renders |

### Critical User Flows (E2E-like)
1. Register → Login → Profile Edit → Save
2. Login → Upload Potential → Submit
3. Admin: Login → View Contributions → Approve
4. Upload form: Draft auto-save → refresh → data persists

## Execution Order
1. Fix profile API bug (done)
2. Install test dependencies + configure vitest
3. Write API route tests (most impactful, catch regressions)
4. Write key frontend component tests
5. Build verification
6. Deploy
