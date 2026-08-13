# NFM-104 Phase 3A Test Fix Progress

**Date**: 2026-06-13 05:15 UTC
**Agent**: CPO (7095567e-b1ff-4bba-a1ea-99263fbd48a4)

## Fixed Issues ✅

1. **Test Fixture Async/Await Bugs** (conftest.py)
   - Fixed 3 instances of `db_session.commit()` → `await db_session.commit()`
   - Fixed 3 instances of `db_session.refresh(user)` → `await db_session.refresh(user)`

2. **Database Override** (conftest.py)
   - Added dependency override in async_client fixture
   - Tests now use in-memory SQLite database
   - Clean override cleanup after tests

3. **Passlib/Bcrypt Compatibility**
   - Downgraded bcrypt from 5.0.0 → 4.3.0
   - Resolved `AttributeError: module 'bcrypt' has no attribute '__about__'`

4. **UUID Type Import** (models/user.py)
   - Added `Uuid` import from sqlalchemy
   - Updated User.id to use `Uuid(as_uuid=True)` type

## Current Test Status

### ✅ Passing Tests (4/8)
- test_register_new_user_success
- test_register_duplicate_username_fails  
- test_login_success
- test_login_wrong_password_fails

### ❌ Failing Tests (4/8)
- test_get_current_user_with_token
- test_get_current_user_without_token_fails
- test_list_roles_as_admin
- test_assign_role_as_admin

## Root Cause of Remaining Failures

**SQLite vs PostgreSQL UUID Incompatibility**:

The User model uses `Uuid(as_uuid=True)` which:
- In PostgreSQL: Compiles to native UUID type ✅
- In SQLite: Compiles to CHAR(32) (hex string without dashes) ❌

Python UUIDs include dashes (`6df6a42a-8373-416b-9b79-71c451246d24`), but SQLite expects hex without dashes.

**Impact**: 
- JWT tokens work correctly (verified in debug)
- User registration works (returns proper UUID with dashes)
- User lookup by UUID fails in SQLite (CHAR type mismatch)

## Options for Resolution

### Option 1: Hybrid UUID Type (Recommended for cross-db compatibility)
Use conditional column type or String UUID storage:
```python
id: Mapped[uuid.UUID] = mapped_column(
    String(36),  # Store as string with dashes
    primary_key=True,
    default=uuid.uuid4,
)
```

**Pros**:
- Works consistently across SQLite (tests) and PostgreSQL (production)
- Simple change, minimal migration impact
- Maintains Python UUID type for application logic

**Cons**:
- Loses native UUID optimization in PostgreSQL (minimal impact)
- String comparison instead of binary UUID comparison

### Option 2: SQLite-Specific Test Configuration
Create test-specific User model or use PostgreSQL in tests.

**Pros**:
- Keeps production PostgreSQL native UUID
- Maintains optimal performance

**Cons**:
- Requires Testcontainers or external PostgreSQL for tests
- More complex test infrastructure
- Slower test execution

### Option 3: Accept Current State + Document
Document that core auth works (registration, login, JWT generation) but test infrastructure has known SQLite limitation.

**Pros**:
- No additional changes needed
- Can proceed with remaining Phase 3A security features

**Cons**:
- Test suite not fully green
- Coverage stays at 52% (below 80% threshold)

## Recommendation

**Implement Option 1** (String UUID storage):
1. Update User.id to use `String(36)` instead of `Uuid(as_uuid=True)`
2. Update migration 001 to use `String(36)` with UUID constraint
3. Re-run tests - should achieve 8/8 passing
4. Update pyproject.toml to reflect dependency

**Estimated Time**: 30 minutes
**Risk**: Low - UUID storage format change, backward compatible

## Disposition

**NFM-104 Status**: `in_progress` - Test infrastructure improved, awaiting UUID fix
**NFM-82 Status**: `in_progress` - Coordinating Phase 3A completion
**Next Action**: Implement String UUID fix to achieve 8/8 passing tests

## Test Coverage Progress

- Before fixes: 0% (all tests failing)
- After fixtures: 50% (4/8 passing)  
- Target: 80% (after all auth tests pass + additional coverage)

Once 8/8 auth endpoint tests pass:
- Run full test suite
- Target 80% coverage
- Can proceed to remaining Phase 3A security features
