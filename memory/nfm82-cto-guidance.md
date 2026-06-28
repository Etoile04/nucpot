# NFM-82 CTO Architectural Guidance

**Date**: 2026-06-13
**Agent**: CTO
**Issue**: NFM-82

## Authentication System Architecture (NFM-104)

### Core Requirements

The authentication system must provide:

1. **User Model with Role-Based Access Control**
   ```python
   class User(SQLAlchemyBase):
       id: int
       username: str (unique, indexed)
       email: str (unique, indexed)
       password_hash: str (bcrypt)
       role: Enum['admin', 'editor', 'reviewer']
       created_at: datetime
       updated_at: datetime
       is_active: bool
   ```

2. **JWT Token-Based Authentication**
   - Access tokens: 15-minute expiration
   - Refresh tokens: 7-day expiration (stored in HTTP-only cookies)
   - Secret key: Environment variable (`JWT_SECRET_KEY`)
   - Algorithm: HS256

3. **Password Security**
   - Hashing: bcrypt with cost factor 12
   - Validation: Min 8 characters, require uppercase, lowercase, number
   - No password reuse (store hash history)

4. **API Endpoints**
   ```
   POST   /api/v1/auth/register       - Public (admin-only initially)
   POST   /api/v1/auth/login          - Public
   POST   /api/v1/auth/logout         - Protected
   POST   /api/v1/auth/refresh        - Public (refresh token required)
   GET    /api/v1/auth/me             - Protected
   PATCH /api/v1/auth/users/:id       - Protected (admin only)
   ```

5. **Middleware**
   ```python
   @router.get("/api/v1/protected")
   def protected_route(current_user: User = Depends(get_current_user)):
       # current_user is injected if token valid
   ```

### Security Architecture

**Session Management**
- Refresh tokens stored in HTTP-only, secure, SameSite cookies
- Access tokens returned in response body (not localStorage)
- CSRF protection via SameSite=Strict
- Token rotation on refresh

**Rate Limiting**
- Login endpoint: 5 attempts per 15 minutes per IP
- Registration: 3 attempts per hour per IP
- Use redis or in-memory storage

**Password Recovery** (Future phase)
- Secure token-based reset flow
- Email verification required
- Token expiration: 1 hour

### Database Schema Changes

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'reviewer',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);

-- Refresh tokens table
CREATE TABLE refresh_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_tokens_expires ON refresh_tokens(expires_at);
```

### Integration Points

**1. Existing API Protection**
Mark existing endpoints as protected:
```python
@router.get("/reference-values")
def get_reference_values(current_user: User = Depends(get_current_user)):
    # Existing logic
```

**2. Admin Interface (NFM-105)**
- `/admin` route requires `role='admin'` or `role='editor'`
- JWT-based authentication
- Role-based UI rendering

**3. Review Workflow (NFM-106)**
- Blog CRUD requires `role='editor'` or higher
- Publish requires `role='admin'`
- Review comments require authentication

### Implementation Sequence

1. **Phase 3A: NFM-104** (Current)
   - Database migration (users table)
   - Authentication endpoints
   - JWT utilities
   - Protected route middleware

2. **Phase 3B: NFM-105** (After NFM-104)
   - Admin UI with login/logout
   - User management (admin only)
   - Blog upload/edit interface

3. **Phase 3C: NFM-106** (After NFM-105)
   - Content approval workflow
   - Role-based permissions

4. **Phase 3D: NFM-107** (After NFM-104)
   - Homepage blog link
   - Public blog listing
   - Individual blog post pages

### Environment Variables

```bash
# JWT Configuration
JWT_SECRET_KEY=<generate-secure-random-32-bytes>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# Password Security
PASSWORD_MIN_LENGTH=8
PASSWORD_BCRYPT_ROUNDS=12

# Rate Limiting
RATE_LIMIT_LOGIN_ATTEMPTS=5
RATE_LIMIT_LOGIN_WINDOW=15
RATE_LIMIT_REGISTRATION_ATTEMPTS=3
RATE_LIMIT_REGISTRATION_WINDOW=60
```

### Testing Requirements

**Unit Tests**
- Password hashing and verification
- JWT token generation and validation
- User CRUD operations
- Role-based access control

**Integration Tests**
- Login/logout flow
- Protected route access
- Token refresh flow
- Rate limiting

**E2E Tests**
- Full authentication cycle
- Admin interface access
- Blog CRUD with authentication

## Next Steps

1. **Monitor NFM-104 Progress**: Authentication implementation is in progress
2. **Prepare NFM-105**: Admin interface planning can begin in parallel
3. **Design NFM-106**: Review workflow specification can be drafted
4. **Coordinate NFM-107**: Homepage integration planning

## Dependencies

- **NFM-104** (Authentication): Active implementation
- **NFM-105** (Admin Interface): Blocked by NFM-104
- **NFM-106** (Review Workflow): Blocked by NFM-104, NFM-105
- **NFM-107** (Homepage Integration): Blocked by NFM-104

## Architectural Decisions

**ADR-001: JWT-Based Authentication**
- **Decision**: Use JWT tokens with refresh token rotation
- **Rationale**: Stateless, scalable, industry-standard
- **Alternatives Considered**: Session-based auth, OAuth2
- **Consequences**: Requires secure secret management, token revocation strategy

**ADR-002: Role-Based Access Control**
- **Decision**: Three-tier role system (admin, editor, reviewer)
- **Rationale**: Simple, sufficient for blog workflow needs
- **Alternatives Considered**: Permission-based system, groups
- **Consequences**: Easy to understand, may need extension later

## Disposition

**NFM-82 Status**: `in_progress`
**Reason**: Child issues properly structured and blocked. NFM-104 authentication implementation is active and critical path.
**Liveness**: Run 61d84a31 owns NFM-104. Monitor for completion.
