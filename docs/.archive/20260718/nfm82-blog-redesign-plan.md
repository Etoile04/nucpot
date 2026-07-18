# NFM-82: Blog Visual Design and Feature Enhancement Plan

## Overview
Improve the blog module with better visual design, homepage integration, and admin interface for managing technical documentation.

## Current State
- ✅ Blog listing exists at `/blog` with basic Ant Design cards
- ✅ Admin module structure exists (reference-data admin)
- ❌ Homepage lacks blog entry link
- ❌ No blog admin interface for managing posts
- ❌ Visual design is generic (default Ant Design styling)

## Design Decisions

### 1. Content Strategy
**Focus**: Getting Started guides + API documentation
- Aligns with OpenKIM's documentation pattern
- Matches NFM's technical audience (researchers, engineers)
- Prioritizes practical "how to use" content

**Initial content structure**:
1. **Getting Started** - Quick start guide, basic concepts
2. **API Documentation** - REST API usage, examples
3. **User Guides** - Advanced features, workflows

### 2. Admin Interface Approach
**Hybrid approach**: Web UI for metadata + MD files for content

**Rationale**:
- **Web UI**: Manage metadata (title, tags, author, publish status, ordering)
- **MD files**: Store actual content in version control (Git-based workflow)
- **Benefits**: Technical team-friendly, audit trail, no database migration needed

**Admin features**:
- List all posts with publish/unpublish toggle
- Edit post metadata (title, tags, author, date)
- Upload new MD files (or edit existing ones)
- Preview rendered markdown
- Draft vs published state management

### 3. Visual Design Direction
**Style**: Scientific documentation (inspired by OpenKIM)

**Characteristics**:
- Clean, minimal layout with strong typography hierarchy
- Left sidebar navigation (for documentation sections)
- Content-focused reading experience
- Professional, academic aesthetic
- Code syntax highlighting for technical examples

**Design elements**:
- Clear section headers
- Generous whitespace for readability
- Consistent spacing and rhythm
- Subtle borders and backgrounds (no heavy shadows)
- Good color contrast for accessibility

## Implementation Tasks

### Phase 1: Homepage Integration
1. Add "技术博客" link to homepage navigation
2. Create call-to-action section: "查看技术文档"
3. Link to `/blog` route

### Phase 2: Blog Visual Redesign
1. **Typography system**:
   - Define heading hierarchy (H1-H6)
   - Set readable line heights and font sizes
   - Use system fonts or consistent web fonts

2. **Layout improvements**:
   - Remove generic Ant Design card styling
   - Create custom blog card component with better spacing
   - Add hover effects on cards
   - Improve tag presentation

3. **Navigation**:
   - Add sidebar navigation for documentation sections
   - Create breadcrumb navigation
   - Add table of contents for long posts

4. **Code highlighting**:
   - Integrate syntax highlighting for code blocks
   - Support multiple languages (Python, JavaScript, etc.)
   - Add copy-to-clipboard functionality

### Phase 3: Admin Interface
1. **Create blog admin module**:
   - `/admin/blog` layout with sidebar navigation
   - Post list view with actions (edit, publish/unpublish, delete)
   - Post editor (metadata form + MD file upload)

2. **Post management features**:
   - Upload MD files with frontmatter validation
   - Edit post metadata (title, tags, author, date, summary)
   - Toggle publish/draft status
   - Preview rendered markdown
   - Reorder posts

3. **API integration** (if needed):
   - Backend API to manage post metadata
   - File upload endpoint for MD files
   - List posts with filters (published/draft, tag, author)

### Phase 4: Documentation Migration
1. Create initial Getting Started guide
2. Document API endpoints
3. Add usage examples
4. Create contribution guidelines

## Technical Stack
- **Frontend**: Next.js (App Router), TypeScript, Ant Design (existing)
- **Styling**: CSS variables, custom components (move away from default Ant Design)
- **Markdown**: `react-markdown` or `next-mdx-remote` for rendering
- **Code highlighting**: `shiki` or `prism-react-renderer`
- **Admin**: Ant Design components (Form, Upload, Table, Switch)

## Success Criteria
- [ ] Homepage has prominent blog link
- [ ] Blog visual design matches OpenKIM's clean documentation style
- [ ] Admin interface allows creating/editing/publishing posts
- [ ] At least 3 initial documentation posts (Getting Started, API Overview, Contribution Guide)
- [ ] Code syntax highlighting works correctly
- [ ] Mobile responsive design

## Estimated Timeline
- Phase 1: 1-2 hours (homepage link)
- Phase 2: 4-6 hours (visual redesign)
- Phase 3: 6-8 hours (admin interface)
- Phase 4: 4-6 hours (content creation)
- **Total**: 15-22 hours

## Open Questions
1. Should we use a database for post metadata or keep everything in MD frontmatter?
   - **Recommendation**: MD frontmatter is sufficient for now. Add database later if querying complexity increases.

2. Should we support draft posts?
   - **Recommendation**: Yes, use `published: boolean` in frontmatter with admin toggle.

3. Should we use MDX for more complex content?
   - **Recommendation**: Start with standard Markdown. Upgrade to MDX if we need React components in posts.

## Next Steps
Await board approval of this plan, then begin implementation with Phase 1.
