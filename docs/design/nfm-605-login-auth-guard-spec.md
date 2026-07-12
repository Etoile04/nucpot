# NFM-605: Login Page + Auth Guard UX Design Spec

> **Scope**: Blog admin login (`/admin/login`) and auth guard for `/admin/blog/*`
> **Design Language**: Follow existing blog admin light theme (Ant Design sidebar, inline CSS)
> **Auth System**: JWT (backend API `/api/admin/auth/login`), NOT Supabase
> **UI Language**: Chinese (中文)

---

## 1. Design Token Reference (extracted from existing blog admin)

All tokens below are sourced from `apps/web/src/app/admin/blog/` — no new design language.

### Colors
| Token | Value | Usage |
|-------|-------|-------|
| `--primary` | `#1890ff` | Primary buttons, links, active states |
| `--primary-disabled` | `#bfbfbf` | Disabled button background |
| `--border` | `#d9d9d9` | Input borders, card borders |
| `--border-light` | `#f0f0f0` | Sidebar header border |
| `--bg-page` | `#fff` | Page background, content area |
| `--text-primary` | `#000` (default) | Headings |
| `--text-secondary` | `#666` | Metadata, hints |
| `--text-muted` | `#999` | Empty states |
| `--error-bg` | `#fff2f0` | Error alert background |
| `--error-border` | `#ffccc7` | Error alert border |
| `--error-text` | `#ff4d4f` | Error alert text |
| `--success-bg` | `#f6ffed` | Success alert background |
| `--success-border` | `#b7eb8f` | Success alert border |
| `--success-text` | `#52c41a` | Success alert text |
| `--danger` | `#ff4d4f` | Reject/destructive actions |
| `--success` | `#52c41a` | Approve/success actions |

### Spacing
| Token | Value | Usage |
|-------|-------|-------|
| `--space-page` | `2rem` | Page content padding |
| `--space-section` | `1.5rem` | Section/field margin-bottom |
| `--space-field` | `0.5rem` | Input padding, label margin-bottom |

### Typography
| Element | Size | Weight | Usage |
|---------|------|--------|-------|
| Page heading (`h1`) | `1.75rem` | 600 | Page titles |
| Form label | inherit | 500 | Input labels |
| Body / input text | `1rem` | 400 | Input values, paragraphs |
| Metadata | `0.875rem` | 400 | Dates, hints, secondary info |

### Border Radius
| Token | Value | Usage |
|-------|-------|-------|
| `--radius` | `4` (px) | Buttons, inputs, alerts, cards |

---

## 2. Component: Login Page (`/admin/login`)

### File: `apps/web/src/app/admin/login/page.tsx`

### Layout
- **No sidebar** — standalone page, full viewport
- **Centered card**, `maxWidth: 400`, vertically centered with flexbox
- **Background**: `#fff` (matches blog admin content area)
- **No `Sider`** — this is outside the blog admin layout

### Component Hierarchy
```
<LoginPage>
  <div> ← outer wrapper, flex center, min-height: 100vh, bg: #fff
    <div> ← card wrapper, maxWidth: 400, padding: 2.5rem, border: 1px solid #d9d9d9, borderRadius: 8
      <h1> ← "博客管理" (matches sidebar header text), fontSize: 1.25rem, fontWeight: 600, textAlign: center, marginBottom: 0.5rem
      <p> ← "请登录管理员账号", fontSize: 0.875rem, color: #666, textAlign: center, marginBottom: 1.5rem
      <ErrorAlert /> ← conditional, same pattern as new/post error
      <form> ← onSubmit = handleLogin
        <EmailField />
        <PasswordField />
        <SubmitButton />
      </form>
    </div>
  </div>
</LoginPage>
```

### Form Fields (reuse existing blog admin input pattern)

**Email field**:
```css
input {
  width: "100%",
  padding: "0.5rem",
  border: "1px solid #d9d9d9",
  borderRadius: 4,
  fontSize: "1rem",
}
```
- `type="email"`, `autoComplete="email"`, `placeholder="请输入邮箱"`
- `required`

**Password field**:
- Same styles as email
- `type="password"`, `autoComplete="current-password"`, `placeholder="请输入密码"`
- `required`

### Labels
```css
label {
  display: "block",
  marginBottom: "0.5rem",
  fontWeight: 500,
}
```
- Email label: `"邮箱"`
- Password label: `"密码"`

### Spacing
- Each field group `marginBottom: "1.5rem"` (same as existing form fields in `new/page.tsx`)
- Last field group `marginBottom: "0"` (button directly below)

### Submit Button
```css
button[type="submit"] {
  width: "100%",        /* full-width, distinct from new/post half-width */
  padding: "0.625rem 1.25rem",
  fontSize: "1rem",
  fontWeight: 500,
  color: "#fff",
  background: isSubmitting ? "#bfbfbf" : "#1890ff",
  border: "none",
  borderRadius: 4,
  cursor: isSubmitting ? "not-allowed" : "pointer",
}
```
- Text: `isSubmitting ? "登录中..." : "登录"`
- `disabled={isSubmitting}`

### Interaction States

| State | Visual | Behavior |
|-------|--------|----------|
| **Default** | Empty form, enabled button | User fills fields |
| **Submitting** | Button bg: `#bfbfbf`, text: "登录中...", `cursor: not-allowed`, `disabled` | POST to `/api/admin/auth/login`, await response |
| **Error — wrong credentials** | Error alert: bg `#fff2f0`, border `#ffccc7`, text `#ff4d4f`. Message: "邮箱或密码错误" | Alert shown above form, fields preserved |
| **Error — account inactive** | Error alert as above. Message: "账号已被禁用，请联系管理员" | Alert shown above form |
| **Error — network** | Error alert as above. Message: "网络错误，请稍后重试" | Alert shown above form |
| **Success** | No visible state on login page — immediate redirect | `router.push("/admin/blog")` after token stored |

### Post-Login Redirect
```
Success → store JWT in httpOnly cookie (or localStorage as fallback)
        → router.push("/admin/blog")
        → router.refresh()
```

---

## 3. Auth Guard Behavior

### Implementation Location
- **New component**: `apps/web/src/components/admin/BlogAuthGuard.tsx`
- **Used in**: `apps/web/src/app/admin/blog/layout.tsx` — wraps `{children}`

### Component: `<BlogAuthGuard>`

```tsx
// Pseudocode
<BlogAuthGuard>
  {loading ? <LoadingState /> : profile ? children : <Redirect />}
</BlogAuthGuard>
```

### Redirect Flow
```
User visits /admin/blog/* (any sub-route)
  → BlogAuthGuard checks for JWT token
  → If NO token: router.replace("/admin/login")
  → If token exists: validate via GET /api/admin/auth/me
    → If valid: render children (show admin UI)
    → If expired/invalid: clear token, redirect to /admin/login
```

**IMPORTANT**: Use `router.replace()` (not `push`) to prevent back-button bypass.

### Loading State
```css
div {
  minHeight: "100vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#fff",
}
p { color: "#999" }
```
- Text: `"加载中..."` (matches existing blog admin loading pattern)
- Full viewport, white background (matches blog admin content area)

---

## 4. Admin Header Modifications

### Location: `apps/web/src/app/admin/blog/layout.tsx`

### 4a. Role Badge

Add a role badge in the sidebar header area (below "博客管理" title, above menu):

```
┌─────────────────────┐
│      博客管理        │  ← existing, 64px header
│   [管理员] Admin     │  ← NEW: role badge, fontSize: 0.75rem
├─────────────────────┤
│ 📄 文章列表          │
│ ✅ 审核队列           │
│ ➕ 新建文章           │
└─────────────────────┘
```

**Role badge styles**:
```css
span {
  fontSize: "0.75rem",
  padding: "0.125rem 0.375rem",
  borderRadius: 2,
  background: "#e6f7ff",
  color: "#1890ff",
  border: "1px solid #91d5ff",
}
```

**Role label mapping** (Chinese):
| Role (API value) | Badge text |
|------------------|------------|
| `admin` | `"管理员"` |
| `editor` | `"编辑"` |
| `reviewer` | `"审核"` |

### 4b. Logout Action

Add logout link at the **bottom** of the sidebar:

```
┌─────────────────────┐
│      博客管理        │
│   [管理员] Admin     │
├─────────────────────┤
│ 📄 文章列表          │
│ ✅ 审核队列           │
│ ➕ 新建文章           │
├─────────────────────┤
│   退出登录           │  ← NEW: bottom of sidebar
└─────────────────────┘
```

**Logout styles**:
```css
div {                           // container
  position: "absolute",
  bottom: 0,
  width: "100%",
  borderTop: "1px solid #f0f0f0",
  padding: "0.75rem 1rem",
}
a {
  fontSize: "0.875rem",
  color: "#666",
  textDecoration: "none",
  cursor: "pointer",
}
a:hover {
  color: "#ff4d4f",
}
```

**Logout behavior**:
1. User clicks "退出登录"
2. Call `POST /api/admin/auth/logout` (or clear token client-side)
3. `router.replace("/admin/login")`

---

## 5. Review Queue Notification Badge

### Location: Sidebar menu item for "审核队列" in `layout.tsx`

### Badge Position
```
  ✅ 审核队列  (3)  ← badge after label text
```

**Badge styles** (matches Ant Design Menu badge pattern):
```css
span.badge {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minWidth: "16px",
  height: "16px",
  padding: "0 4px",
  fontSize: "0.6875rem",        // 11px
  lineHeight: "16px",
  borderRadius: "8px",
  background: "#ff4d4f",       // red/warning — attention-required
  color: "#fff",
  fontWeight: 600,
  marginLeft: "0.5rem",
}
```

### Data Source
- Fetch from `GET /api/admin/blog/posts?status=under_review&count_only=true` (or filter client-side from existing posts list)
- Store count in layout component state
- Poll on mount and refresh when navigating to review page
- Show badge only when `count > 0` — hide when `count === 0`

### Badge Visibility Rules
| Count | Display |
|-------|---------|
| `0` | Badge hidden |
| `1-9` | Show exact number |
| `10+` | Show `"9+"` (cap display, overflow handling) |

---

## 6. Interaction States Summary

| State | Location | Visual |
|-------|----------|--------|
| Loading (auth check) | Auth guard | Centered "加载中..." on white bg |
| Loading (form submit) | Login page | Button: bg `#bfbfbf`, text "登录中...", disabled |
| Error (credentials) | Login page | Alert: bg `#fff2f0`, border `#ffccc7`, text "邮箱或密码错误" |
| Error (inactive) | Login page | Alert: same container, text "账号已被禁用" |
| Error (network) | Login page | Alert: same container, text "网络错误，请稍后重试" |
| Authenticated | Blog admin | Normal admin UI + role badge + logout link |
| No session | Blog admin | Redirect to `/admin/login` |
| Badge (count > 0) | Sidebar menu | Red pill badge with count |
| Badge (count = 0) | Sidebar menu | No badge element |

---

## 7. Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `apps/web/src/app/admin/login/page.tsx` | **CREATE** | Login page (no sidebar, centered card, email+password) |
| `apps/web/src/components/admin/BlogAuthGuard.tsx` | **CREATE** | Auth guard component (JWT check + redirect) |
| `apps/web/src/app/admin/blog/layout.tsx` | **MODIFY** | Add `<BlogAuthGuard>` wrapper, role badge, logout link, notification badge |

### Files NOT to modify
- `apps/web/src/components/AuthProvider.tsx` — Supabase auth, separate system
- `apps/web/src/app/login/page.tsx` — Public-facing login, separate system
- `apps/web/src/app/admin/page.tsx` — NucPot admin, separate auth flow

---

## 8. API Contract (for reference — implemented by NFM-604)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/admin/auth/login` | POST | Login with `{email, password}` → `{token, profile}` |
| `/api/admin/auth/me` | GET | Validate JWT → `{profile: {role, ...}}` |
| `/api/admin/auth/logout` | POST | Invalidate token |

The frontend spec above is designed against this contract. Token storage mechanism (httpOnly cookie vs localStorage) is an engineering decision — the UX spec is agnostic.
