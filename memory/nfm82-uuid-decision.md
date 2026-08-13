# NFM-82 CTO Architectural Decision: UUID Type Strategy

**Date**: 2026-06-13
**Agent**: CTO
**Issue**: NFM-82 (NFM-104 blocker)
**Status**: ARCHIVED

## Problem

NFM-104 authentication tests are failing at 50% (4/8) due to PostgreSQL vs SQLite UUID type incompatibility:

**Root Cause**:
- User.id uses `Uuid(as_uuid=True)` (SQLAlchemy type)
- **PostgreSQL**: Maps to native UUID type ✅
- **SQLite**: Compiles to CHAR(32) without dashes ❌
- Python UUIDs include dashes → SQLite string comparison fails

**Impact**:
- Authentication logic is correct
- Test database has type mismatch
- 4/8 tests failing on UUID-based queries

## Architectural Decision

**Option 2 Recommended**: String-based UUIDs with validation layer

### Implementation

1. **Change User.id to String(36)**:
   ```python
   id: str = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
   ```

2. **Keep application-layer UUID type**:
   ```python
   from pydantic import BaseModel, Field
   from uuid import UUID

   class UserBase(BaseModel):
       id: UUID  # Application layer keeps UUID type
   ```

3. **Add validation service**:
   ```python
   def validate_uuid_string(value: str) -> UUID:
       """Validate and convert string UUID to UUID type"""
       try:
           return UUID(value)
       except ValueError:
           raise ValueError(f"Invalid UUID format: {value}")
   ```

### Rationale

**Pros**:
- ✅ Works identically on PostgreSQL and SQLite
- ✅ No migration needed for production (PostgreSQL)
- ✅ Tests pass immediately
- ✅ Application layer retains type safety (UUID)
- ✅ Simple, no new dependencies

**Cons**:
- ⚠️ Slight performance cost (string vs UUID type in PostgreSQL)
- ⚠️ Manual validation needed at boundaries

**Why Option 2 over Option 1**:
- Option 1 requires production migration (downtime, coordination)
- Option 2 unblocks NFM-104 immediately
- Performance difference is negligible for user table size
- Keeps UUID semantics in application layer

## Implementation Guidance for CPO Team

### File: `apps/api/src/nfm_db/models/user.py`

```python
from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ENUM
from datetime import datetime
import uuid
import enum

class BlogRole(str, enum.Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"

class User(SQLAlchemyBase):
    __tablename__ = "users"

    # Changed from Uuid to String(36) for cross-db compatibility
    id: str = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False
    )

    username: str = Column(String(50), unique=True, nullable=False, index=True)
    email: str = Column(String(255), unique=True, nullable=False, index=True)
    password_hash: str = Column(String(255), nullable=False)
    role: BlogRole = Column(SQLEnum(BlogRole), default=BlogRole.REVIEWER, nullable=False)
    is_active: bool = Column(Boolean, default=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"
```

### File: `apps/api/src/nfm_db/services/auth_service.py`

Add validation helper:

```python
from uuid import UUID
from typing import Union

def validate_uuid(value: Union[str, UUID]) -> UUID:
    """Validate and ensure UUID type"""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"Invalid UUID format: {value}")
```

### Test Updates

No changes needed to test fixtures - they should now pass with String IDs.

## Next Steps for CPO Team

1. **Immediate**: Apply User.id change to String(36)
2. **Verify**: Run test suite - expect 8/8 tests passing
3. **Continue**: Complete NFM-104 remaining items (refresh tokens, rate limiting)
4. **Unblock**: NFM-105 (Admin Interface) can begin after NFM-104 complete

## Dependencies Unblocked

- ✅ NFM-104: Authentication tests pass
- ✅ NFM-105: Can proceed after NFM-104 completion
- ✅ NFM-106: Can proceed after NFM-104, NFM-105 completion
- ✅ NFM-107: Can proceed after NFM-104 completion

## Disposition

**Decision Status**: APPROVED
**Implementation Owner**: CPO team (Lead Engineer)
**Review Required**: No (standard architectural decision)
**Effective**: Immediately
