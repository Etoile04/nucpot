# NFM-82 Architectural Guidance

## Status: Strategic Decisions Received ✅

**Date**: 2025-06-13
**CTO**: Architectural guidance for CPO implementation team

---

## Executive Summary

Based on user decisions, this document provides architectural guidance for implementing the blog admin interface and homepage integration. The CPO team will implement these features following the technical specifications below.

---

## Strategic Decisions → Technical Requirements

### 1. Role-Based Admin Access

**Decision**: Role-based access control for blog admin interface

**Architecture Requirements**:
- **Authentication Integration**: Use existing auth system (if any) or implement role-based permissions
- **Role Definitions**:
  - `admin`: Full CRUD + publish rights
  - `editor`: Create/edit drafts, cannot publish
  - `reviewer`: Review drafts, approve/reject for publishing
- **Authorization Middleware**: Protect `/admin/blog` routes with role checks
- **Session Management**: Secure session handling with timeout

**Technical Spec**:
```typescript
// Role-based access control pattern
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

// Authorization middleware
const requireBlogRole = (allowedRoles: BlogRole['role'][]) => {
  return async (req: Request, res: Response, next: NextFunction) => {
    const user = await getCurrentUser(req)
    if (!user || !allowedRoles.includes(user.blogRole)) {
      return res.status(403).json({ error: 'Forbidden' })
    }
    next()
  }
}
```

**CPO Implementation Tasks**:
1. Audit existing authentication system
2. Define role schema in database
3. Implement authorization middleware
4. Add role assignment interface (for admins)
5. Test role-based access patterns

---

### 2. Content Approval Workflow

**Decision**: Review required before publishing

**Architecture Requirements**:
- **Content State Machine**: Draft → Under Review → Approved → Published
- **Review Queue**: Interface for reviewers to see pending posts
- **Approval Actions**: Approve/Reject with comments
- **Version Control**: Keep draft versions separate from published

**Technical Spec**:
```typescript
// Blog post state machine
type PostStatus = 'draft' | 'under_review' | 'approved' | 'published' | 'rejected'

interface BlogPost {
  id: string
  title: string
  content: string
  status: PostStatus
  authorId: string
  reviewerId?: string
  reviewedAt?: Date
  publishedAt?: Date
  rejectionReason?: string
}

// State transitions
const stateTransitions = {
  draft: ['under_review', 'deleted'],
  under_review: ['approved', 'rejected', 'draft'],
  approved: ['published', 'draft'],
  published: ['archived'],
  rejected: ['draft', 'deleted']
}
```

**CPO Implementation Tasks**:
1. Implement post state machine in backend
2. Create review queue UI for reviewers
3. Add approve/reject actions with comments
4. Implement notification system for status changes
5. Test workflow end-to-end

---

### 3. Homepage Integration

**Decision**: Add [博客] link to top-right navigation row

**Architecture Requirements**:
- **Navigation Component**: Modify main site navigation
- **Placement**: After [关于] link in the row: [浏览] [高级检索] [对比] [反馈] [关于] [博客]
- **Styling**: Match existing nav link style
- **Responsive**: Ensure mobile compatibility

**Technical Spec**:
```typescript
// Navigation data structure
interface NavItem {
  label: string
  href: string
  ariaLabel?: string
}

const mainNavigation: NavItem[] = [
  { label: '浏览', href: '/browse' },
  { label: '高级检索', href: '/search' },
  { label: '对比', href: '/compare' },
  { label: '反馈', href: '/feedback' },
  { label: '关于', href: '/about' },
  { label: '博客', href: '/blog' }  // NEW
]
```

**CPO Implementation Tasks**:
1. Locate navigation component in web app
2. Add blog link to nav array
3. Test responsive behavior
4. Verify link routing to blog index
5. Accessibility audit (screen readers, keyboard nav)

---

## Implementation Phases

### Phase 3A: Authentication & Authorization (Priority: HIGH)
**Timeline**: 3-4 hours
**Owner**: Lead Engineer (via CPO)

1. Implement role-based access control
2. Add authorization middleware
3. Create role assignment interface
4. Test authentication flows

### Phase 3B: Admin Interface Implementation (Priority: HIGH)
**Timeline**: 4-5 hours
**Owner**: Lead Engineer (via CPO)

1. Build admin dashboard layout
2. Implement post list view with filters
3. Create post editor interface
4. Add file upload for Markdown/images
5. Implement preview functionality

### Phase 3C: Review Workflow (Priority: MEDIUM)
**Timeline**: 2-3 hours
**Owner**: Lead Engineer (via CPO)

1. Implement state machine
2. Create review queue UI
3. Add approve/reject actions
4. Test review workflow

### Phase 3D: Homepage Integration (Priority: LOW)
**Timeline**: 1 hour
**Owner**: Lead Engineer (via CPO)

1. Add blog link to navigation
2. Test responsive behavior
3. Verify accessibility

---

## Database Schema Changes

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

-- Create indexes for review queue
CREATE INDEX idx_blog_posts_status ON blog_posts(status);
CREATE INDEX idx_blog_posts_reviewer ON blog_posts(reviewer_id);
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

## Security Considerations

1. **Authorization**: Every admin endpoint must verify user role
2. **CSRF Protection**: All state-changing endpoints need CSRF tokens
3. **Input Validation**: Sanitize all user inputs (title, content, metadata)
4. **File Upload**: Validate file types, size limits for Markdown/images
5. **SQL Injection**: Use parameterized queries for all DB operations
6. **Session Security**: Implement secure session management with timeouts

---

## Testing Requirements

### Unit Tests
- Role-based authorization logic
- State machine transitions
- Input validation functions
- File upload validation

### Integration Tests
- Admin CRUD operations
- Review workflow end-to-end
- Authorization enforcement
- Navigation routing

### E2E Tests
- Admin login and dashboard access
- Create → Submit → Review → Publish workflow
- Role-based access control
- Homepage navigation link

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

## CPO Handoff Checklist

- [ ] Review this architectural guidance
- [ ] Consult with UXDesigner on admin workflows
- [ ] Consult with Creative Director on admin visual design
- [ ] Create implementation tasks for Lead Engineer
- [ ] Coordinate with Quality Auditor on testing strategy
- [ ] Estimate timeline and confirm with CEO
- [ ] Begin Phase 3A implementation

---

**CTO Note**: I remain available for architecture decisions, technical guidance, and resolving blockers. Implementation details and task coordination are owned by the CPO team.
