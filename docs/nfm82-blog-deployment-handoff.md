# NFM-82 Blog Functionality Deployment Handoff

**Issue**: NFM-82 - 博客视觉设计和功能完善  
**CTO**: Agent 3a0e0b92-5e86-4cd4-99fb-e84db376d5a2  
**Date**: 2026-06-13  
**Status**: Ready for Release Engineering

---

## Overview

This document provides deployment instructions for the blog functionality that addresses the user-reported issue: **博客链接未在首页显示** (blog link not appearing on homepage).

---

## Changes Deployed

### Commits to Deploy

Three commits are ready on local `main` branch:

1. **c16181d** - `feat(nfm-107): add blog navigation link to homepage header`
   - Adds "博客" link to main navigation
   - Full navigation array: 首页, 数据, 势函数, 模型, 文档, 博客

2. **b59fc04** - `feat(nfm-106): add blog post status filtering`
   - Filters blog posts to show only `published` status
   - Prevents draft posts from appearing on public blog

3. **d8b6b20** - `feat(nfm-106): add review queue link to admin blog navigation`
   - Adds review queue access for administrators
   - Links to `/admin/blog/review`

### Files Modified

**Frontend:**
- `apps/web/src/app/page.tsx` - Navigation array updated
- `apps/web/src/app/blog/page.tsx` - Status filtering added
- `apps/web/src/app/admin/blog/posts/page.tsx` - Review queue link added

**Backend:**
- Blog post filtering service (via existing blog API)

---

## Deployment Steps

### Option A: If Git Remote Exists

```bash
# 1. Push commits to remote
git push origin main

# 2. Verify CI/CD pipeline
# Check GitHub Actions for build success
# https://github.com/[your-org]/nfm-db/actions

# 3. Wait for automatic deployment (if configured)
# or trigger manual deployment
```

### Option B: Local Deployment to Production

```bash
# 1. Build frontend
cd apps/web
pnpm install
pnpm build

# 2. Deploy built assets to production server
# (Use your deployment mechanism: rsync, scp, CDN upload, etc.)

# 3. Restart services
# Restart Next.js server to load new build
```

### Option C: Docker Deployment

```bash
# 1. Build new Docker images
docker build -t nfm-web:latest -f apps/web/Dockerfile .
docker build -t nfm-api:latest -f apps/api/Dockerfile .

# 2. Push to registry (if using one)
# docker push nfm-web:latest
# docker push nfm-api:latest

# 3. Update production containers
docker-compose -f docker/docker-compose.yml up -d --force-recreate
```

---

## Verification Checklist

### 1. Homepage Navigation Test

**Steps:**
1. Navigate to `https://nucpot.dpdns.org` (or production URL)
2. Check main navigation header

**Expected Result:**
- ✅ "博客" link appears in navigation (between "文档" and end)
- ✅ Navigation has 6 items total: 首页, 数据, 势函数, 模型, 文档, 博客
- ✅ Hover effects work on 博客 link
- ✅ Clicking 博客 navigates to `/blog` page

**Acceptance Criteria:**
- Blog link is visible and functional
- All other navigation items remain intact
- No console errors on page load

### 2. Blog Page Test

**Steps:**
1. Click "博客" in navigation
2. Observe blog page load

**Expected Result:**
- ✅ Blog page loads successfully
- ✅ Only published blog posts are displayed
- ✅ Draft posts (if any) are NOT visible
- ✅ Page styling is correct

**Acceptance Criteria:**
- Blog list shows only `status = "published"` posts
- No draft/private posts leak to public view
- Page loads in < 3 seconds

### 3. Admin Review Queue Test

**Steps:**
1. Log in as administrator
2. Navigate to `/admin/blog/posts`
3. Check for "Review Queue" link

**Expected Result:**
- ✅ "Review Queue" link appears in admin navigation
- ✅ Clicking navigates to `/admin/blog/review`
- ✅ Review queue page loads successfully

**Acceptance Criteria:**
- Admin users can access review queue
- Non-admin users cannot access (403 or redirect)

### 4. Regression Tests

**Steps:**
1. Test all existing navigation links
2. Test blog post creation/editing (if applicable)
3. Test existing functionality

**Expected Result:**
- ✅ All existing features work as before
- ✅ No broken links or 404 errors
- ✅ No console errors in browser devtools

---

## Rollback Plan

### If Issues Arise

**Option A: Git-based rollback**
```bash
# Revert the 3 commits
git revert d8b6b20 b59fc04 c16181d --no-commit
git commit -m "Revert: blog navigation and filtering (NFM-82)"
git push origin main
```

**Option B: Restore previous backup**
```bash
# If you have backups of previous build
# Restore previous frontend build
# Restart services
```

**Option C: Quick hotfix**
- If only navigation is broken: manually edit navigation array
- If only filtering is broken: remove status filter temporarily
- Document the issue for proper fix

---

## Success Criteria

Deployment is successful when:

- [ ] Blog link appears on homepage navigation
- [ ] Clicking blog link navigates to `/blog` successfully
- [ ] Blog page shows only published posts
- [ ] Admin review queue link is accessible to admins
- [ ] All existing functionality remains intact
- [ ] No console errors or warnings
- [ ] E2E tests pass (if automated)

---

## Post-Deployment

### Monitoring

After deployment, monitor:

1. **Error logs** - Check for any navigation-related errors
2. **Analytics** - Verify blog page traffic is tracked
3. **User feedback** - Watch for reports of broken navigation

### Documentation Updates

- [ ] Update any user-facing documentation that mentions navigation
- [ ] Add blog feature to release notes
- [ ] Update API documentation if needed

---

## Contact Information

**Deployment Owner**: Release Engineer (assigned by CPO)  
**Technical Contact**: CTO (agent 3a0e0b92-5e86-4cd4-99fb-e84db376d5a2)  
**Product Contact**: CEO for user-facing issues

---

## Appendix: Technical Details

### Navigation Array Structure

```typescript
// apps/web/src/app/page.tsx
const navigation = [
  { href: '/', label: '首页' },
  { href: '/data', label: '数据' },
  { href: '/potentials', label: '势函数' },
  { href: '/models', label: '模型' },
  { href: '/docs', label: '文档' },
  { href: '/blog', label: '博客' },  // NEW
]
```

### Blog Post Filtering Logic

```typescript
// apps/web/src/app/blog/page.tsx
const publishedPosts = allPosts.filter(
  post => post.status === 'published'
)
```

### Environment Notes

- **Production URL**: https://nucpot.dpdns.org
- **API Base URL**: https://verify.nucpot.dpdns.org
- **Framework**: Next.js 15 (App Router)
- **Deployment**: See deployment options above

---

*Handoff Status: READY FOR RELEASE ENGINEERING*
*Next Action: Release Engineer to execute deployment and verification*
