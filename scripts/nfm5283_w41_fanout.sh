#!/bin/bash
# NFM-5283 — OKR Weekly Standup W41 fan-out (ISO week 41: 2026-10-05 → 2026-10-11)
# Steps 2–5 of the routine: parent title/description, 15 children, roster snapshot.
set -uo pipefail

BASE="${PAPERCLIP_API_URL%/}"; BASE="${BASE%/api}"
API="$BASE/api"
AUTH="Authorization: Bearer $PAPERCLIP_API_KEY"
RID="X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID"
CT="Content-Type: application/json"
PARENT="d1cdcfa2-0314-46eb-ada5-8b881749ebd6"
S="${PAPERCLIP_RUN_SCRATCH_DIR%/}/nfm5283"
mkdir -p "$S"
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "== Phase 1: PATCH parent title + description =="

cat > "$S/parent_desc.md" <<'MD'
## OKR Weekly Standup — Week 41 (ISO 2026)

> Reporting window: 2026-10-05 09:30 → 2026-10-09 17:00 (Asia/Shanghai)
> Generated: GEN_TS by routine `ef3be54d-…` (routine revision 7, doc revision 4)
> Numbering scheme: ISO 8601 (Mon–Sun, week 1 = week containing first Thursday)
> Compute: `datetime.date(monday).isocalendar()` → `(iso_year, iso_week, 1)`

### Roster (15 reporters expected)

- [ ] **Lisa** (CEO)
- [ ] **CTO**
- [ ] **CPO**
- [ ] **Nuclear Domain Expert** (Research Director)
- [ ] **Lead Engineer** (LSE)
- [ ] **Skills Architect**
- [ ] **Workflow Designer**
- [ ] **SRE Monitor**
- [ ] **Release Engineer**
- [ ] **Code Reviewer**
- [ ] **E2E QA Tester**
- [ ] **UXDesigner**
- [ ] **Research Lead**
- [ ] **Dr. Ingrid Novak** (Optimization)
- [ ] **Dr. Alexander Petrov** (ML)

Each reporter's individual filing task is in a child issue assigned to them.
MD
# substitute the timestamp line (heredoc is quoted, so do it after)
python3 - "$S/parent_desc.md" "$TS" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); ts = sys.argv[2]
p.write_text(p.read_text().replace("GEN_TS", ts))
PY

jq -n \
  --arg title "OKR Weekly Standup — Week 41 (2026-10-05 → 2026-10-11)" \
  --rawfile desc "$S/parent_desc.md" \
  '{title: $title, description: $desc}' > "$S/parent_patch.json"

code=$(curl -s -o "$S/parent_resp.json" -w '%{http_code}' -X PATCH \
  "$API/issues/$PARENT" -H "$AUTH" -H "$RID" -H "$CT" -d @"$S/parent_patch.json")
echo "PATCH http=$code"
jq -r '"echoed title: " + (.title // "MISSING") + " | status: " + (.status // "?") + " | desc len: " + ((.description // "")|length|tostring)' "$S/parent_resp.json"
if [ "$code" != "200" ] || ! jq -e '.title' "$S/parent_resp.json" >/dev/null; then
  echo "FATAL: parent PATCH failed"; jq . "$S/parent_resp.json" | head -30; exit 1
fi

echo
echo "== Phase 2: create 15 children =="

# name <TAB> department <TAB> agent uuid  (Responder Roster, routine revision 7)
cat > "$S/roster.tsv" <<'TSV'
Lisa	Executive (CEO)	0cdd447e-af66-4edf-9ab1-3fc13666fdff
CTO	Engineering (CTO)	3a0e0b92-5e86-4cd4-99fb-e84db376d5a2
CPO	Product (CPO)	7095567e-b1ff-4bba-a1ea-99263fbd48a4
Nuclear Domain Expert	Research (Nuclear)	fe09f6ec-1998-46a0-96af-f0b26e79abdf
Lead Engineer	Engineering (LSE)	98fc3168-be45-4673-808e-22238b366352
Skills Architect	AI Efficacy & Tooling	07090cce-93ea-484d-b032-5f6bb98d8996
Workflow Designer	Workflow & CI/CD	6fbeddcd-177b-49a5-8b35-9a7b10d09248
SRE Monitor	SRE	2ee2415b-e43e-4806-888f-c231e60facaf
Release Engineer	Release Engineering	32cfff52-c625-4734-9206-e191ff7f5fc6
Code Reviewer	Code Quality	aed30220-8c92-4106-ae33-c11b4d15b5f5
E2E QA Tester	E2E QA	b1c4ddfb-9270-46e9-8072-ef3c01eab129
UXDesigner	Design	89823413-84e8-47fb-9748-da8fa3c26592
Research Lead	Research	d258630f-75ee-45b1-8d6a-e7ded7343ab3
Dr. Ingrid Novak	Research (Optimization)	70e02e50-19ed-47ea-88a2-6078ad856815
Dr. Alexander Petrov	Research (ML)	2d421ed2-f0ae-49da-a79a-f2959c95fb25
TSV

: > "$S/children_created.tsv"
: > "$S/children_failed.tsv"
n=0
while IFS=$'\t' read -r name dept agent; do
  n=$((n+1))
  desc="## ${name} — Standup Week 41

> Filing window: Mon 2026-10-05 09:30 → Fri 2026-10-09 17:00 (Asia/Shanghai).
> Replace this template body with your filled report (keep the section headers). A description ≤1000 chars counts as a hollow filing (NFM-2454) and will not be counted.

### OKR Progress
- **KR-{ROLE}-{NUMBER}**: {current_value} / {target} — {🟢 Green | 🟡 Yellow | 🔴 Red}
- …repeat per active KR

### Completed This Week
- [NFM-XXX](https://paperclip/NFM/issues/NFM-XXX) — {one-line summary}
- …repeat

### Planned Next Week
- {key items}

### Blockers
- {if any}"
  jq -n --arg name "$name" --arg dept "$dept" --arg agent "$agent" --arg desc "$desc" \
    '{title: ("[Standup Week 41] " + $name + " — " + $dept), description: $desc, assigneeAgentId: $agent, priority: "medium"}' \
    > "$S/child_$n.json"

  ok=0
  for attempt in 1 2; do
    code=$(curl -s -o "$S/child_$n.resp.json" -w '%{http_code}' -X POST \
      "$API/issues/$PARENT/children" -H "$AUTH" -H "$RID" -H "$CT" -d @"$S/child_$n.json")
    if [ "$code" = "200" ] || [ "$code" = "201" ]; then
      if jq -e '.id' "$S/child_$n.resp.json" >/dev/null 2>&1; then
        ident=$(jq -r '.identifier // "? "' "$S/child_$n.resp.json")
        cuid=$(jq -r '.id' "$S/child_$n.resp.json")
        aagn=$(jq -r '.assigneeAgentId // "? "' "$S/child_$n.resp.json")
        printf '%s\t%s\t%s\t%s\n' "$name" "$ident" "$cuid" "$aagn" >> "$S/children_created.tsv"
        echo "[$n/15] OK  $name -> $ident (assignee=$aagn)"
        ok=1; break
      fi
    fi
    echo "[$n/15] attempt $attempt failed for $name http=$code"
    sleep 2
  done
  [ "$ok" = "1" ] || printf '%s\t%s\n' "$name" "http=$code $(head -c 200 "$S/child_$n.resp.json")" >> "$S/children_failed.tsv"
done < "$S/roster.tsv"

created=$(wc -l < "$S/children_created.tsv" | tr -d ' ')
failed=$(wc -l < "$S/children_failed.tsv" | tr -d ' ')
echo "created=$created failed=$failed"
[ "$created" != "15" ] && { echo "WARNING: not all children created"; cat "$S/children_failed.tsv"; }

echo
echo "== Phase 3: roster snapshot comment =="

{
  echo "## Week 41 fan-out — roster snapshot (audit trail)"
  echo
  echo "15 child issues created ${TS}. Reporting window: 2026-10-05 09:30 → 2026-10-09 17:00 Asia/Shanghai. Target participation ≥12/15 filed by Friday 17:00."
  echo
  echo "| # | Reporter | Child issue | Agent UUID |"
  echo "|---|----------|-------------|------------|"
  i=0
  while IFS=$'\t' read -r name ident cuid aagn; do
    i=$((i+1))
    echo "| $i | ${name} | [${ident}](/NFM/issues/${ident}) | \`${aagn}\` |"
  done < "$S/children_created.tsv"
  echo
  echo "Next: Wed 2026-10-07 14:00 +08 reminder for children not yet started; synthesis + close-out Fri 2026-10-09 17:00 +08 (hard stop Sat 00:00). Hollow-filing gate (≤1000 chars desc ≠ filed, [NFM-2454](/NFM/issues/NFM-2454)) applies at close-out."
} > "$S/snapshot.md"

jq -n --rawfile body "$S/snapshot.md" '{body: $body}' > "$S/snapshot.json"
code=$(curl -s -o "$S/snapshot_resp.json" -w '%{http_code}' -X POST \
  "$API/issues/$PARENT/comments" -H "$AUTH" -H "$RID" -H "$CT" -d @"$S/snapshot.json")
echo "snapshot comment http=$code"
jq -r '"comment id: " + (.id // .error // "?")' "$S/snapshot_resp.json"
echo "DONE created=$created failed=$failed"
