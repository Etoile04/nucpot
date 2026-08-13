# Blog Authentication & Authorization - Phase 3A

## Overview

This document describes the implementation of role-based access control (RBAC) for the blog admin interface as specified in NFM-104 and NFM-82 architectural guidance.

## Implementation Status: ✅ COMPLETE

All acceptance criteria have been met:
- ✅ Database schema updated with blog roles
- ✅ Authorization middleware protects admin routes
- ✅ Role assignment interface functional
- ✅ Tests implemented for auth system

## Architecture

### Database Schema

The `users` table includes the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Unique user identifier |
| `username` | VARCHAR(100) | Unique username |
| `email` | VARCHAR(255) | Unique email address |
| `full_name` | VARCHAR(255) | Optional full name |
| `hashed_password` | VARCHAR(255) | Bcrypt hashed password |
| `blog_role` | ENUM | admin, editor, reviewer, or NULL |
| `is_active` | BOOLEAN | User activation status |
| `last_login` | TIMESTAMPTZ | Last login timestamp |
| `created_at` | TIMESTAMPTZ | Account creation time |
| `updated_at` | TIMESTAMPTZ | Last update time |

### Role Definitions

| Role | Permissions |
|------|-------------|
| **admin** | Full CRUD + publish + review + assign_roles |
| **editor** | Create posts, edit posts |
| **reviewer** | Review posts, approve/reject |

### Permission Model

```python
class Permission(str, enum.Enum):
    CREATE_POST = "create_post"
    EDIT_POST = "edit_post"
    DELETE_POST = "delete_post"
    PUBLISH_POST = "publish_post"
    REVIEW_POST = "review_post"
    ASSIGN_ROLES = "assign_roles"
```

## API Endpoints

### Authentication

- `POST /api/v1/auth/register` - Register new user (initial setup)
- `POST /api/v1/auth/login` - Authenticate and get JWT token
- `GET /api/v1/auth/me` - Get current user info

### Role Management (Admin Only)

- `GET /api/v1/auth/roles` - List all available roles with permissions
- `PUT /api/v1/auth/users/{user_id}/role` - Assign/remove user role

## Usage Examples

### 1. Login to Get Access Token

```bash
curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=your_password"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

### 2. Access Protected Endpoint

```bash
curl -X GET "http://localhost:8000/api/v1/auth/me" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 3. Assign Role (Admin Only)

```bash
curl -X PUT "http://localhost:8000/api/v1/auth/users/{user_id}/role" \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
```

## Authorization Middleware

### Protecting Routes

```python
from nfm_db.api.v1.auth import require_admin, require_editor, require_reviewer

@router.get("/admin/posts")
async def list_posts(
    user: User = Depends(require_admin),
):
    # Only admins can access
    pass

@router.post("/admin/posts")
async def create_post(
    user: User = Depends(require_editor),
):
    # Admins and editors can access
    pass
```

### Permission-Based Authorization

```python
from nfm_db.models.user import Permission
from nfm_db.api.v1.auth import require_permission

@router.delete("/admin/posts/{post_id}")
async def delete_post(
    user: User = Depends(require_permission(Permission.DELETE_POST)),
):
    # Only users with delete permission can access
    pass
```

## Migration

To create the users table, run the migration:

```bash
cd apps/api
.venv/bin/alembic upgrade head
```

## Testing

### Unit Tests

```bash
pytest tests/test_auth_service.py -v
pytest tests/test_auth_middleware.py -v
pytest tests/test_user_model.py -v
```

### Integration Tests

```bash
pytest tests/test_auth_endpoints.py -v
```

### All Tests

```bash
pytest tests/ -v --cov=src
```

## Security Considerations

1. **Password Storage**: All passwords are hashed using bcrypt with salt
2. **JWT Tokens**: Tokens expire after 30 minutes (configurable)
3. **HTTPS Required**: In production, always use HTTPS
4. **Secret Key**: Change `SECRET_KEY` environment variable in production
5. **Role Enforcement**: All admin endpoints verify user roles
6. **CSRF Protection**: To be implemented in Phase 3B

## Configuration

Environment variables (prefix with `NFM_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | CHANGE_THIS | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 30 | Token lifetime in minutes |

## Next Steps

### Phase 3B: Admin Interface Implementation
- Build admin dashboard layout
- Implement post list view with filters
- Create post editor interface
- Add file upload for Markdown/images
- Implement preview functionality

### Phase 3C: Review Workflow
- Implement post state machine
- Create review queue UI
- Add approve/reject actions
- Test review workflow

### Phase 3D: Homepage Integration
- Add [博客] link to main navigation
- Test responsive behavior
- Verify accessibility

## Files Created

### Models
- `src/nfm_db/models/user.py` - User model with RBAC

### Services
- `src/nfm_db/services/auth_service.py` - Password hashing and JWT

### API
- `src/nfm_db/api/v1/auth.py` - Authorization dependencies
- `src/nfm_db/api/v1/auth_endpoints.py` - Auth and role management endpoints

### Schemas
- `src/nfm_db/schemas/auth.py` - Request/response schemas

### Configuration
- `src/nfm_db/config.py` - Updated with JWT settings

### Migration
- `migrations/versions/001_create_users_table.py` - Users table migration

### Tests
- `tests/test_auth_service.py` - Password and JWT tests
- `tests/test_auth_middleware.py` - Authorization middleware tests
- `tests/test_user_model.py` - Permission system tests
- `tests/test_auth_endpoints.py` - API integration tests

## Dependencies Added

```toml
dependencies = [
    "passlib[bcrypt]>=1.7.4",      # Password hashing
    "python-jose[cryptography]>=3.3.0",  # JWT handling
]
```

## Troubleshooting

### Migration fails with database connection error
Ensure PostgreSQL is running and `DATABASE_URL` is set correctly.

### JWT decode errors
Check that `SECRET_KEY` is consistent between token creation and validation.

### Permission denied errors
Verify user has the required `blog_role` assigned in the database.
