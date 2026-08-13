# Routine template interpolation — determination (NFM-2071)

**Answer: YES.** The Paperclip routine engine interpolates `title` and `description`
at issue-creation time. The syntax is **`{{name}}`** (double brace). Single-brace
`{name}` — what revision 3 of the OKR Weekly Standup routine uses — is not a
template syntax and is emitted literally.

## Implementation (white-box)

Renderer: `@paperclipai/shared/dist/routine-variables.js`

```js
const ROUTINE_VARIABLE_MATCHER = /\{\{\s*([A-Za-z](?:\\_|[A-Za-z0-9_])*)\s*\}\}/g;
```

Call site: `@paperclipai/server/dist/services/routines.js`, `dispatchRoutineRun()`
(lines ~1144-1152):

```js
const allVariables = { ...getBuiltinRoutineVariableValues(), ...automaticVariables, ...resolvedVariables };
const title           = interpolateRoutineTemplate(input.routine.title, allVariables) ?? input.routine.title;
const baseDescription = interpolateRoutineTemplate(input.routine.description, allVariables);
```

This is not Handlebars. It is a bespoke single-pass regex replace — no helpers,
no conditionals, no loops, no dotted paths. `{{issue.identifier}}`-style dotted
names do **not** match the `[A-Za-z][A-Za-z0-9_]*` name pattern; the
`branchTemplate` lead in the issue description was a false lead (different
subsystem, different renderer).

### Name grammar

- Must match `/^[A-Za-z][A-Za-z0-9_]*$/` — leading letter, then letters/digits/underscore.
- Inner whitespace tolerated: `{{ name }}` === `{{name}}`.
- Markdown-escaped underscores tolerated: `{{iso\_week}}` === `{{iso_week}}`.
- A name ending in `Date` (e.g. `weekStartDate`) is auto-typed `date` and its
  value is validated as `YYYY-MM-DD` (`isRoutineDateVariableName`).

### Built-ins

Only two, from `BUILTIN_ROUTINE_VARIABLE_NAMES`:

| Name | Value | Example |
|---|---|---|
| `{{date}}` | current date, **UTC**, `YYYY-MM-DD` | `2026-07-29` |
| `{{timestamp}}` | human-readable, **UTC** | `July 29, 2026 at 6:05 PM UTC` |

There is **no** `{{now}}`, no ISO-week built-in, no date arithmetic, and no
timezone control. Both built-ins are UTC-only.

### Resolution precedence

`resolveRoutineVariableValues()` — highest wins:

1. `automaticVariables` (workspace-derived, e.g. branch name) — not overridable
2. provided values — `POST /run` `variables` object, then `payload.variables`,
   then top-level `payload` (webhook source only)
3. the declared variable's `defaultValue`

Then `{ ...builtins, ...automatic, ...resolved }` — so **a declared variable
named `date` or `timestamp` shadows the built-in.**

### Failure modes (important)

- A **required** variable that resolves empty throws `422 Missing routine
  variables: <names>` and **no issue is created**. A scheduled run that hits
  this produces nothing at all — silent skip from the board's perspective.
- `assertScheduleCompatibleVariables()` blocks attaching a schedule trigger
  when any required variable lacks a default, which guards the common case.
- An **unmatched** placeholder is left verbatim in the output (see
  `optionalVar` below) — it does not error, it just leaks braces into the issue.
- `PATCH /api/routines/{id}` runs `syncRoutineVariablesWithTemplate()`: the
  variables array is **rebuilt from the template text** on every write. New
  `{{names}}` are auto-added as `required: true, defaultValue: null`; names no
  longer present are dropped. Adding a placeholder without also supplying a
  default therefore makes the routine un-runnable.

## Empirical confirmation (black-box)

Throwaway routine `08e5b6a4-4563-4177-96f1-301644f4b0dc` (now **archived**),
run `70d8df22-c8e3-4522-8c8f-c91db2e519f9`, generated issue **NFM-2075**
(`4bba27b0-30ce-4c48-9d3d-2866c68692ff`).

First run attempt returned `{"error":"Missing routine variables: now"}` —
proving `{{now}}` is not a built-in but was auto-registered as a required
variable by the template sync.

Second run supplied `now` and `overrideVar`. Generated issue:

```
TITLE: PROBE 30 2026-07-29          <- from "PROBE {{iso_week_num}} {{date}}"

PROBE-BEGIN
single_brace={iso_week}                        <- NOT interpolated
double_brace=DOUBLE_OK                         <- from defaultValue
dollar_brace=${dollarVar}                      <- NOT interpolated
spaced_inner=SPACED_OK                         <- {{ spacedVar }} works
builtin_date=2026-07-29                        <- built-in
builtin_timestamp=July 29, 2026 at 6:05 PM UTC <- built-in
builtin_now=NOT_A_BUILTIN                      <- supplied, not a built-in
optional_no_default={{optionalVar}}            <- unmatched, left verbatim
underscore_name=30                             <- underscores in names OK
date_typed_name=2026-07-27                     <- *Date auto-typed date
override_me=OVERRIDDEN_BY_RUN                  <- POST /run beat defaultValue
PROBE-END
```

Title interpolation confirmed alongside description.

## Consequence for the OKR Weekly Standup routine

Interpolation cannot compute the ISO week. There is no date arithmetic and no
week built-in, so `{{iso_week}}` has no engine-side source of truth — a
scheduled run can only ever substitute a **static `defaultValue`**, which would
be stale the following Monday.

What revision 4 can genuinely automate:

- `{{date}}` — the run date (UTC). Note the routine fires 09:00 Asia/Shanghai
  = 01:00 UTC the same day, so the UTC date matches the intended Monday.
- `{{timestamp}}` — replaces the `{generation_timestamp}` placeholder outright.

Everything week-derived (`iso_week`, `iso_year`, `mon`, `sun`, `fri_date`)
still requires either an agent computing it, or an external scheduler firing
`POST /run` with a `variables` object. The engine alone cannot do it.
