# NFM-82 Blog Redesign Implementation Strategy

**Created**: 2026-06-13  
**Status**: READY FOR IMPLEMENTATION  
**Priority**: MEDIUM  
**Owner**: CEO (Strategy) → CTO (Implementation)

## Executive Summary

Previous CTO attempt failed due to context window overflow when trying to implement entire blog redesign at once. This strategy breaks work into focused, achievable phases.

## Key Findings

### ✅ FALSE REPORT - Homepage Button Already Exists
The original issue report claimed "缺少点击链接进入的按钮" (missing homepage entry button). **This is incorrect.** The button already exists on the homepage (line 76-93 in `apps/web/src/app/page.tsx`).

### 🔧 REAL ISSUES IDENTIFIED

1. **Visual Design** (HIGH PRIORITY)
   - Current styling is very basic (gray borders, minimal design)
   - Needs professional design similar to OpenKIM.org documentation
   - Better typography, spacing, visual hierarchy

2. **Admin Functionality** (HIGH PRIORITY) 
   - New post form exists but doesn't actually save files (only shows alert)
   - Missing edit functionality for existing posts
   - No file upload capability
   - No delete functionality

## Phased Implementation Strategy

### Phase 1: Make Admin Actually Work (Week 1)
**Goal**: Make existing admin interface functional before improving design

**Tasks**:
1. Create API route for saving blog posts as Markdown files
2. Implement file upload functionality in new post form
3. Add edit functionality for existing posts
4. Add delete functionality
5. Test full admin workflow: create → edit → delete

**Success Criteria**: 
- Admin can create, edit, and delete blog posts without manual file editing
- All operations actually persist to filesystem

### Phase 2: Visual Design Foundation (Week 2)
**Goal**: Improve basic visual design without complete redesign

**Tasks**:
1. Establish proper CSS variables and design tokens
2. Improve typography and spacing
3. Add visual hierarchy and better layout
4. Implement responsive design improvements
5. Test on multiple devices

**Reference Design**: OpenKIM.org documentation interface
- Clean, professional layout
- Good typography hierarchy
- Effective use of whitespace
- Clear navigation

### Phase 3: Enhanced Features (Week 3)
**Goal**: Add advanced features for better user experience

**Tasks**:
1. Image upload and management
2. Preview functionality for posts
3. Better tag management
4. Search functionality
5. Improved navigation and user flows

## Technical Approach

### Architecture Decisions

1. **Keep Static Generation**: Maintain current Next.js static generation approach
2. **Add API Routes**: Create proper API endpoints for admin operations
3. **Improve Component Organization**: Better structure for maintainability
4. **Progressive Enhancement**: Make it work first, then make it beautiful

### File Structure Impact

```
apps/web/
├── src/
│   ├── app/
│   │   ├── blog/              # Keep existing, improve styling
│   │   ├── admin/
│   │   │   └── blog/          # Enhance with real functionality
│   │   └── api/
│   │       └── admin/         # NEW: Admin API routes
│   ├── components/
│   │   └── blog/              # Improve organization
│   ├── lib/
│   │   └── blog/              # Add admin utilities
│   └── styles/
│       └── blog/              # NEW: Proper CSS organization
```

## Risk Mitigation

### Context Window Management
- Work in focused phases (as outlined)
- Create separate issues for each phase
- Don't try to implement everything at once

### Design Quality
- Reference OpenKIM.org for proven patterns
- Test with real content during development
- Get user feedback on visual design

### Technical Complexity
- Keep existing architecture (static generation)
- Add API routes incrementally
- Test each phase thoroughly before moving to next

## Success Metrics

### Phase 1 Success
- ✅ Admin can create blog posts that actually save
- ✅ Admin can edit existing posts
- ✅ Admin can delete posts
- ✅ No manual file editing required

### Phase 2 Success  
- ✅ Visual design matches professional standards
- ✅ Responsive design works on all devices
- ✅ Typography and spacing are consistent
- ✅ Design meets accessibility standards

### Phase 3 Success
- ✅ Image upload and management works
- ✅ Preview functionality helps content creation
- ✅ Search and navigation improve user experience
- ✅ Overall user satisfaction is high

## Next Steps

1. **CEO**: Approve this strategy
2. **CTO**: Create detailed implementation plan for Phase 1
3. **Design**: If needed, hire UX Designer for visual design work in Phase 2
4. **Testing**: E2E tests for admin functionality

## Board Approval Required

This strategy requires approval before implementation:
- ✅ Phased approach (reduces context window risk)
- ✅ Focus on functionality first, design second
- ✅ Maintains existing architecture
- ❌ Hiring UX Designer (deferred decision until Phase 2)

**Decision Point**: Should we proceed with this phased approach, or modify the strategy?