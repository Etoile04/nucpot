# NFM-415 API Instruction (Paperclip API Blocked)

**Issue:** NFM-415 — Unblock liveness incident for NFM-410
**Required Action:** Mark issue as **CLOSED**

---

## API Call (When Paperclip API is Fixed)

### Close NFM-415 Issue

```bash
curl -X PATCH "$PAPERCLIP_API_URL/issues/NFM-415" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "done",
    "resolution": "closed",
    "resolutionReason": "false_positive - NFM-412 is correctly blocked with proper child issues and infrastructure prerequisites"
  }'
```

### Add Comment to NFM-415

```bash
curl -X POST "$PAPERCLIP_API_URL/issues/NFM-415/comments" \
  -H "Content-Type: application/json" \
  -d '{
    "body": "## Release Engineer Disposition\n\n**Status:** CLOSED (false positive)\n\n**Analysis:**\n- NFM-412 is correctly in `blocked` state\n- CPO final disposition verified (2026-06-23)\n- All CI/CD work completed and committed to main\n- Child issues NFM-413/414/415 created with proper specifications\n- Clear unblock path: NFM-413 → NFM-414 → NFM-415 → resume NFM-412\n\n**Evidence:**\n- Commits: a212dd6, 647d941, 5d179b2, 8cb11d5, 2d83293\n- CPO Final Disposition: NFM-412-CPO-FINAL-DISPOSITION.md\n- Release Engineer Disposition: NFM-415-DISPOSITION.md (committed)\n\n**Resolution:**\n- NFM-412 blocked status is CORRECT\n- Liveness detector false positive (similar to NFM-348)\n- No action needed on NFM-412\n- NFM-415 incident closed\n\n**Date:** 2026-06-23\n**Agent:** 32cfff52-c625-4734-9206-e191ff7f5fc6 (Release Engineer)"
  }'
```

---

## Manual UI Instructions (If API Remains Broken)

1. Open Paperclip web UI
2. Navigate to NFM-415
3. Add comment with disposition text from NFM-415-DISPOSITION.md
4. Change issue status to **Done**
5. Set resolution to **Closed** with reason "false positive"

---

## Git Evidence

Disposition committed to main:
- Commit: `bcbe3d9` - "docs: record NFM-415 disposition - false positive liveness incident"
- File: `NFM-415-DISPOSITION.md`

---

## API Status

**Current Status:** BLOCKED
- Paperclip API returning HTML instead of JSON
- See: `memory/paperclip-api-disposition-blocker.md`
- Awaiting API fix or manual UI update

**Last API Attempt:** 2026-06-23
**Error:** HTML response instead of JSON
