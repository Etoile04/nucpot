---
name: nfm82-blog-admin-guidance
description: Blog admin interface architectural guidance and CTO handoff for NFM-82
metadata:
  type: project
---

# NFM-82 Blog Admin Interface - CTO Architectural Guidance

## Issue Status
**Status**: CTO role complete, ready for CPO handoff
**Date**: 2025-06-13
**Phase**: 2 (Visual Redesign) Complete, Phase 3 (Admin Interface) Ready for Implementation

---

## Strategic Decisions Received

### User Requirements:
1. **Admin Access**: Role-based access control (RBAC)
2. **Approval Workflow**: Review required before publishing
3. **Homepage Integration**: Add [博客] link to top-right navigation row [浏览] [高级检索] [对比] [反馈] [关于] [博客]
4. **Content Resources**: NFMD administrators and agents will create documentation content

---

## Architectural Deliverables

### 1. Architectural Guidance Document
**File**: `docs/nfm82-architectural-guidance.md`

**Contents**:
- **Authentication & Authorization**: RBAC implementation with admin/editor/reviewer roles
- **API Endpoints**: Complete specification for admin operations (CRUD, review workflow)
- **Database Schema**: SQL migrations for roles, workflow states, indexes
- **Security Considerations**: CSRF, input validation, file upload validation, SQL injection prevention
- **Testing Requirements**: Unit, integration, and E2E test specifications

### 2. Team Delegation Plan
**File**: `docs/nfm82-team-delegation.md`

**Contents**:
- Phase breakdown with timelines (3A: 3-4h, 3B: 4-5h, 3C: 2-3h, 3D: 1h)
- Consultation points for UXDesigner, Creative Director, Quality Auditor
- Success criteria and acceptance standards

---

## Implementation Phases

### Phase 3A: Authentication & Authorization (Priority: HIGH)
**Timeline**: 3-4 hours
**Owner**: Lead Engineer (via CPO)

- Implement role-based access control
- Add authorization middleware
- Create role assignment interface
- Test authentication flows

### Phase 3B: Admin Interface Implementation (Priority: HIGH)
**Timeline**: 4-5 hours
**Owner**: Lead Engineer (via CPO)

- Build admin dashboard layout
- Implement post list view with filters
- Create post editor interface
- Add file upload for Markdown/images
- Implement preview functionality

### Phase 3C: Review Workflow (Priority: MEDIUM)
**Timeline**: 2-3 hours
**Owner**: Lead Engineer (via CPO)

- Implement state machine (draft → under_review → approved → published)
- Create review queue UI
- Add approve/reject actions
- Test review workflow

### Phase 3D: Homepage Integration (Priority: LOW)
**Timeline**: 1 hour
**Owner**: Lead Engineer (via CPO)

- Add blog link to navigation
- Test responsive behavior
- Verify accessibility

---

## Technical Specifications

### Role-Based Access Control
```typescript
interface BlogRole {
  role: 'admin' | 'editor' | 'reviewer'
  permissions: Permission[]
}

type Permission = 
  | 'create_post'
  | 'edit_post'
  | 'delete_post'
  | 'publish_post'
  | 'review_post'
```

### Content State Machine
```typescript
type PostStatus = 'draft' | 'under_review' | 'approved' | 'published' | 'rejected'

// State transitions
const stateTransitions = {
  draft: ['under_review', 'deleted'],
  under_review: ['approved', 'rejected', 'draft'],
  approved: ['published', 'draft'],
  published: ['archived'],
  rejected: ['draft', 'deleted']
}
```

### Database Schema Changes
```sql
-- Add blog roles to users table
ALTER TABLE users ADD COLUMN blog_role VARCHAR(20) DEFAULT NULL;
ALTER TABLE users ADD CONSTRAINT check_blog_role 
  CHECK (blog_role IN ('admin', 'editor', 'reviewer'));

-- Add review workflow to blog_posts table
ALTER TABLE blog_posts ADD COLUMN status VARCHAR(20) DEFAULT 'draft';
ALTER TABLE blog_posts ADD COLUMN reviewer_id UUID REFERENCES users(id);
ALTER TABLE blog_posts ADD COLUMN reviewed_at TIMESTAMP;
ALTER TABLE blog_posts ADD COLUMN rejection_reason TEXT;
```

---

## API Endpoints Required

### Authentication & Authorization
- `GET /api/auth/roles` - List available roles (admin only)
- `PUT /api/auth/users/:id/role` - Assign role to user (admin only)
- `GET /api/auth/me` - Get current user with roles

### Blog Admin
- `GET /api/admin/blog/posts` - List all posts with filters (admin/reviewer)
- `GET /api/admin/blog/posts/:id` - Get single post for editing
- `POST /api/admin/blog/posts` - Create new post (editor/admin)
- `PUT /api/admin/blog/posts/:id` - Update post (editor/admin)
- `DELETE /api/admin/blog/posts/:id` - Delete post (admin only)
- `POST /api/admin/blog/posts/:id/submit` - Submit for review (editor)
- `POST /api/admin/blog/posts/:id/approve` - Approve post (reviewer)
- `POST /api/admin/blog/posts/:id/reject` - Reject post (reviewer)
- `POST /api/admin/blog/posts/:id/publish` - Publish approved post (admin)

---

## Security Requirements

1. **Authorization**: Every admin endpoint must verify user role
2. **CSRF Protection**: All state-changing endpoints need CSRF tokens
3. **Input Validation**: Sanitize all user inputs (title, content, metadata)
4. **File Upload**: Validate file types, size limits for Markdown/images
5. **SQL Injection**: Use parameterized queries for all DB operations
6. **Session Security**: Implement secure session management with timeouts

---

## Success Criteria

Phase 3 is complete when:
- ✅ Role-based access control working (admin, editor, reviewer)
- ✅ Admin interface allows creating/editing posts
- ✅ Review workflow (draft → review → approve → publish) functional
- ✅ Homepage displays [博客] link in top-right navigation
- ✅ All tests passing (80%+ coverage)
- ✅ Accessibility audit passed
- ✅ Security review passed

---

## Consultation Points

### For UXDesigner
- Optimal admin dashboard layout for role-based workflows
- Post editor UX for non-technical users
- Review queue interface design

### For Creative Director
- Admin interface visual design (functional vs. blog style)
- Navigation link styling consistency
- Error message and notification design

### For Quality Auditor
- Accessibility compliance (WCAG 2.1 AA)
- Cross-browser testing checklist
- Mobile responsiveness validation

---

## CTO Role Complete ✅

### Deliverables:
- ✅ Strategic questions asked and answered
- ✅ Architectural guidance documented
- ✅ Technical specifications created
- ✅ Security requirements defined
- ✅ Implementation phases defined
- ✅ Success criteria established

### Handoff:
Implementation coordination, task breakdown, and team management are now owned by the CPO team.

### CTO Availability:
I remain available for:
- Architecture decisions
- Technical guidance
- Resolving blockers

---

**Estimated Total Implementation Time**: 10-13 hours
**Priority**: Medium
**Dependencies**: UXDesigner consultation (admin workflows)
