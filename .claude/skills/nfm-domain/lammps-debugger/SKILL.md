---
name: lammps-debugger
description: LAMMPS failure analysis and debugging: error pattern recognition, fix suggestions based on error type, solution validation. Use when LAMMPS simulations fail with NaN, energy instability, non-convergence, lost atoms, or other runtime errors.
---

<objective>
Provides systematic LAMMPS failure diagnosis by recognizing error patterns, suggesting targeted fixes, and validating proposed solutions before production use.
</objective>

<diagnostic_approach>
For LAMMPS error diagnosis and troubleshooting, see:
- [references/error-patterns.md](references/error-patterns.md) - Classified LAMMPS errors with fix strategies
- [references/diagnostic-workflows.md](references/diagnostic-workflows.md) - Step-by-step troubleshooting procedures
- [references/fix-validation.md](references/fix-validation.md) - Testing proposed solutions
</diagnostic_approach>

<quick_diagnosis>
**Common Error Fingerprints**:

**NaN Errors**:
- Symptom: NaN in thermo output, simulation crashes
- Fingerprint: Energy/forces go NaN → all values NaN
- Cause: Energy overflow, atoms too close, bad potential
- Quick Fix: Reduce timestep to 0.0001, check pair_style

**Energy Drift (NVE)**:
- Symptom: Total energy increases monotonically
- Fingerprint: Energy change >1% over 10 ps
- Cause: Poor energy conservation, integration issue
- Quick Fix: Reduce timestep, check pair_style cutoff

**Lost Atoms**:
- Symptom: "Lost atoms" error, system size decreases
- Fingerprint: Atom count changes, atoms leave box
- Cause: Atoms ejected by high forces, wrong boundary
- Quick Fix: Check `boundary` setting, use `fix npt`

**Minimization Fails**:
- Symptom: Minimization does not converge
- Fingerprint: Max force remains > threshold
- Cause: Bad geometry, conflicting constraints
- Quick Fix: Relax constraints, try different `min_style`

**Potential File Errors**:
- Symptom: "Cannot open potential file"
- Fingerprint: File not found or wrong format
- Cause: Missing file, wrong `pair_style`
- Quick Fix: Check file path, verify `pair_coeff` format
</quick_diagnosis>

<workflow>
When debugging LAMMPS failures:

1. **Identify the error type**
   - Read error message from log file
   - Classify using [references/error-patterns.md](references/error-patterns.md)
   - Note when error occurs (initialization, minimization, dynamics)

2. **Analyze root causes**
   - Check input script for common mistakes
   - Verify initial state (geometry, forces)
   - Examine log file for warnings before crash
   - Use diagnostic workflow from [references/diagnostic-workflows.md](references/diagnostic-workflows.md)

3. **Propose targeted fixes**
   - Based on error pattern, suggest specific changes
   - Prioritize fixes: (1) timestep, (2) potential, (3) geometry
   - Provide rationale for each suggested fix
   - Include code examples for implementation

4. **Validate proposed solution**
   - Use validation checks from [references/fix-validation.md](references/fix-validation.md)
   - Test fix on minimal system first
   - Verify energy conservation (NVE) or stability (NVT)
   - Check that symptoms are resolved

5. **Document outcome**
   - Summary of error and root cause
   - Fix applied and verification result
   - Confidence score (70%+ required for autonomous fix)
   - If confidence <70%, escalate to human expert
</workflow>

<error_classification>
**Class A: Configuration Errors** (Fix in script)
- Wrong `pair_style` for potential format
- Missing or incorrect `pair_coeff`
- `boundary` mismatch with simulation type
- `timestep` too large for system
- Thermostat/barostat parameters wrong

**Class B: Geometry Errors** (Fix with minimization)
- Atoms too close (overlapping positions)
- Bad lattice parameters
- Missing basis atoms
- Incorrect atomic positions
- Wrong crystal structure

**Class C: Potential Errors** (Replace potential)
- Potential outside calibrated range
- Missing cross-potentials for alloys
- Potential parameters physically unreasonable
- Potential file corrupted or incomplete

**Class D: Integration Errors** (Adjust integration)
- Timestep too large for dynamics
- Wrong `fix` selection for ensemble
- RESPA parameters unstable
- SHAKE/RATTLE tolerance too tight

**Class E: System Errors** (Resystem or escalate)
- Fundamental incompatibility (material + potential)
- Phase transition not handled
- Requires domain knowledge beyond script
- Unknown error pattern
</error_classification>

<output_format>
Structure LAMMPS debugging responses as:

**Error Type**: [classification: Class A/B/C/D/E]
**Symptom**: [what went wrong]
**Root Cause**: [why it went wrong]
**Suggested Fixes**: [ranked by priority]
1. [Fix #1 with code example]
2. [Fix #2 with code example]
3. [Fix #3 if needed]
**Validation Steps**: [how to verify fix works]
**Confidence Score**: [0-100%]
**Recommendation**: [Apply fix / Use with caution / Escalate]

For unknown patterns:
**Error Type**: Unknown/Class E
**Symptom**: [description]
**Analysis**: [what has been tried]
**Escalation Required**: Yes/No
**Information Needed**: [what data would help diagnosis]
</output_format>

<fix_priority>
When multiple fixes are possible, prioritize:

1. **Timestep reduction** (fastest to test)
   - Always try: 0.5× current timestep
   - For metals: 0.0001 ps (1 fs)
   - For oxides: 0.0005 ps (0.5 fs)
   - Test: Run NVE for 10 ps, monitor energy drift

2. **Potential verification** (most common root cause)
   - Check potential calibrated for system + temperature
   - Verify all elements have parameters
   - Test with simpler potential if available
   - Consult nuclear-materials-knowledge skill

3. **Geometry correction** (for bad initial structures)
   - Minimize with tight convergence
   - Check lattice parameters against literature
   - Verify no overlapping atoms
   - Use `delete_atoms overlap` if needed

4. **Integration adjustment** (for ensemble issues)
   - Try different thermostat (`fix nvt` vs `fix langevin`)
   - Adjust damping parameters
   - Use `fix npt` for pressure control
   - Check ensemble matches intended simulation

5. **System redesign** (last resort, requires escalation)
   - Different potential type
   - Different simulation approach
   - Domain expert consultation
   - Consider computational method (DFT vs MD)
</fix_priority>

<validation>
A complete LAMMPS debugging response includes:
- Correct error classification (A/B/C/D/E)
- Identified root cause with evidence from log file
- At least one concrete fix with code example
- Validation procedure to test the fix
- Confidence score ≥70% (or escalation to human)
- Clear statement of what to try next

Never:
- Suggest fixes without understanding root cause
- Recommend reinstalling LAMMPS (rarely the issue)
- Propose changes without testing them first
- Ignore warnings that preceded the error
- Apply Tier 4 fixes without documentation
- Suggest fixes for Class E errors without escalation
</validation>

<integration_notes>
This skill works with other nuclear domain skills:
- **nuclear-materials-knowledge** - Provides domain context for errors (phase transitions, material properties)
- **literature-search** - Validates potential parameters against literature
- **quality-audit** - Checks if simulation outputs meet quality standards
- **template-selector** - Validates generated input before execution

Common workflow: lammps-debugger diagnoses error → literature-search verifies potential → nuclear-materials-knowledge validates parameters → template-selector regenerates input
</integration_notes>

<confidence_scoring>
**High Confidence (≥90%)**:
- Known error pattern with established fix
- Fix has been validated on similar system
- Root cause clearly identified
- Low-risk, reversible change

**Medium Confidence (70-89%)**:
- Error pattern recognized but root cause uncertain
- Fix likely to work but not guaranteed
- Requires testing after implementation
- Some risk but reversible

**Low Confidence (<70%)**:
- Unknown error pattern
- Root cause unclear after analysis
- Fix is speculative
- High risk or irreversible change
- **Action**: Escalate to human expert before applying
</confidence_scoring>

<common_pitfalls>
**Avoid These Mistakes**:

1. **Treating symptoms instead of root causes**
   - Bad: "Reduce timestep" for every error
   - Good: Diagnose why timestep is too large

2. **Ignoring warnings before crash**
   - Always check log for warnings preceding error
   - Warnings often indicate real issues

3. **Applying fixes blindly**
   - Test each fix individually
   - Don't apply multiple fixes simultaneously
   - Verify which fix actually worked

4. **Not validating fixes**
   - Always test after applying fix
   - Run minimization → dynamics
   - Verify energy conservation

5. **Escalating too early or too late**
   - Escalate for Class E errors (unknown patterns)
   - Don't escalate for simple configuration errors
   - Use confidence score to guide escalation

6. **Forgetting domain knowledge**
   - Use nuclear-materials-knowledge for material-specific context
   - Consult literature-search for potential validation
   - Consider phase transitions and material properties
</common_pitfalls>
