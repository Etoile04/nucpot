# NucPot Phase 4: 基础设施强化 + 自动化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立自动化 CI/CD 管线、验证服务进程管理、扩展参考值数据，防止之前的"本地成功但线上构建失败"问题再次发生

**Architecture:** GitHub Actions CI 检查每次 PR 的 build + lint + test；ThinkStation 验证服务通过 systemd 管理，Named Tunnel 通过 cloudflared service 自动启动；参考值从硬编码迁移到 Supabase 配置表

**Tech Stack:** GitHub Actions, systemd, cloudflared, Supabase SQL, vitest, Next.js build

**Pre-requisites:**
- ThinkStation SSH: `z203@100.70.30.21`
- 验证服务路径: `/home/z203/nucpot-autovc/` (或 `~/nucpot-autovc/`)
- Cloudflare Tunnel token: 需从现有配置中获取
- Supabase Cloud 访问权限: 已有 service_role key

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `.github/workflows/ci.yml` | CI 管线 (build + lint + test) |
| Create | `.github/workflows/deploy-check.yml` | Vercel 构建验证 (可选) |
| Modify | `package.json` | 添加 pre-push hook |
| Create | `scripts/setup-systemd.sh` | ThinkStation systemd 服务安装脚本 |
| Create | `deploy/nucpot-autovc.service` | 验证服务 systemd unit 文件 |
| Create | `deploy/cloudflared-nucpot.service` | Named Tunnel systemd unit 文件 |
| Create | `supabase/migrations/XX_reference_values.sql` | 参考值配置表迁移 |
| Create | `docs/phase4-setup-guide.md` | 部署运维指南 |

---

## Task 1: GitHub Actions CI 管线

**Priority:** P0 — 防止 TS 构建错误再次阻塞部署

**Files:**
- Create: `.github/workflows/ci.yml`
- Reference: `package.json` (scripts), `vitest.config.ts`

- [ ] **Step 1: Create CI workflow file**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Lint
        run: npm run lint

      - name: Type check
        run: npx tsc --noEmit

      - name: Build
        run: npm run build
        env:
          NEXT_PUBLIC_SUPABASE_URL: https://placeholder.supabase.co
          NEXT_PUBLIC_SUPABASE_ANON_KEY: placeholder
          NEXT_PUBLIC_AUTOCV_API_URL: https://verify.nucpot.dpdns.org

      - name: Test
        run: npm test
```

- [ ] **Step 2: Commit CI workflow**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions CI pipeline (build + lint + test)"
```

- [ ] **Step 3: Push and verify workflow runs**

```bash
git push origin main
# Check https://github.com/Etoile04/nucpot/actions
```

**Verification:** GitHub Actions 页面显示绿色 ✅

---

## Task 2: 本地 pre-push Hook (双重保险)

**Priority:** P0 — 防止在 CI 之前推送破坏性代码

**Files:**
- Create: `scripts/pre-push.sh`
- Modify: `package.json` (add husky or simple hook)

- [ ] **Step 1: Create pre-push hook script**

```bash
#!/bin/bash
# scripts/pre-push.sh — Run type check before push
echo "🔍 Running type check..."
npx tsc --noEmit
if [ $? -ne 0 ]; then
  echo "❌ Type check failed. Fix errors before pushing."
  exit 1
fi
echo "✅ Type check passed."
exit 0
```

- [ ] **Step 2: Add hook setup to package.json**

Add to package.json scripts:
```json
{
  "scripts": {
    "prepare": "cp scripts/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push"
  }
}
```

- [ ] **Step 3: Run prepare and verify**

```bash
npm run prepare
# Test: manually run .git/hooks/pre-push && echo "hook works"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/pre-push.sh package.json
git commit -m "feat: add pre-push type-check hook to prevent broken builds"
```

**Verification:** 故意引入一个 TS 错误，确认 `git push` 被阻止

---

## Task 3: 验证服务 systemd 配置

**Priority:** P1 — 确保验证服务重启后自动恢复

**Files:**
- Create: `deploy/nucpot-autovc.service`
- Create: `deploy/cloudflared-nucpot.service`
- Create: `scripts/setup-systemd.sh`

**Context:** ThinkStation SSH: `z203@100.70.30.21`，验证服务运行于 port 8001

- [ ] **Step 1: Create systemd unit for verification service**

```ini
# deploy/nucpot-autovc.service
[Unit]
Description=NucPot AutoVC Verification Service
After=network.target

[Service]
Type=simple
User=z203
WorkingDirectory=/home/z203/nucpot-autovc
ExecStart=/home/z203/anaconda3/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create systemd unit for cloudflared tunnel**

```ini
# deploy/cloudflared-nucpot.service
[Unit]
Description=Cloudflare Tunnel for NucPot Verify
After=network.target

[Service]
Type=simple
User=z203
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate run nucpot-verify
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create setup script**

```bash
#!/bin/bash
# scripts/setup-systemd.sh — Install systemd services on ThinkStation
set -e

echo "Installing NucPot services on ThinkStation..."

# Copy service files
scp deploy/nucpot-autovc.service z203@100.70.30.21:/tmp/
scp deploy/cloudflared-nucpot.service z203@100.70.30.21:/tmp/

# Install and enable services
ssh z203@100.70.30.21 << 'REMOTE'
sudo mv /tmp/nucpot-autovc.service /etc/systemd/system/
sudo mv /tmp/cloudflared-nucpot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nucpot-autovc cloudflared-nucpot
sudo systemctl start nucpot-autovc cloudflared-nucpot
echo "Services installed and started:"
systemctl status nucpot-autovc --no-pager
systemctl status cloudflared-nucpot --no-pager
REMOTE

echo "Done!"
```

- [ ] **Step 4: Test on ThinkStation**

```bash
# SSH to ThinkStation, check current service status
ssh z203@100.70.30.21 "ps aux | grep -E 'uvicorn|cloudflared' | grep -v grep"
```

- [ ] **Step 5: Commit**

```bash
git add deploy/ scripts/setup-systemd.sh
git commit -m "feat: add systemd service configs for verification service and cloudflare tunnel"
```

**Note:** 实际安装需用户确认后执行 `scripts/setup-systemd.sh`

---

## Task 4: 参考值配置表 (Supabase 迁移)

**Priority:** P1 — 将硬编码参考值迁移到数据库

**Files:**
- Create: `supabase/migrations/20260529_reference_values.sql`
- Reference: `verify-service/` 中的 REFERENCE_VALUES 字典

**Context:** 当前验证服务 `REFERENCE_VALUES` 硬编码了几个元素的参考值。需要迁移到 Supabase 表，使管理员可通过前端添加/修改。

- [ ] **Step 1: Read current REFERENCE_VALUES from verify-service**

```bash
grep -r "REFERENCE_VALUES" verify-service/ -A 30
```

- [ ] **Step 2: Create Supabase migration SQL**

```sql
-- supabase/migrations/20260529_reference_values.sql
-- Reference values for property verification

CREATE TABLE IF NOT EXISTS reference_values (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  element TEXT NOT NULL,            -- e.g., 'W', 'Ta', 'V', 'Cr', 'Fe'
  property TEXT NOT NULL,           -- e.g., 'lattice_constant', 'cohesive_energy'
  value DOUBLE PRECISION NOT NULL,  -- reference value
  unit TEXT NOT NULL DEFAULT '',    -- e.g., 'Å', 'eV/atom', 'GPa'
  crystal_structure TEXT,           -- e.g., 'BCC', 'FCC'
  temperature_k DOUBLE PRECISION DEFAULT 0,  -- temperature in Kelvin
  source TEXT,                      -- literature reference
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(element, property, crystal_structure, temperature_k)
);

-- Enable RLS
ALTER TABLE reference_values ENABLE ROW LEVEL SECURITY;

-- Anyone can read
CREATE POLICY "Reference values are publicly readable"
  ON reference_values FOR SELECT
  USING (true);

-- Only admins can write
CREATE POLICY "Admins can manage reference values"
  ON reference_values FOR ALL
  USING (auth.jwt()->>'role' = 'admin');

-- Seed data from current hardcoded values
INSERT INTO reference_values (element, property, value, unit, crystal_structure, source) VALUES
  ('W', 'lattice_constant', 3.165, 'Å', 'BCC', 'Villars 2016'),
  ('W', 'cohesive_energy', -8.90, 'eV/atom', 'BCC', 'Kittel 2005'),
  ('W', 'bulk_modulus', 310.0, 'GPa', 'BCC', 'Simunek 2003'),
  ('Ta', 'lattice_constant', 3.303, 'Å', 'BCC', 'Villars 2016'),
  ('Ta', 'cohesive_energy', -8.10, 'eV/atom', 'BCC', 'Kittel 2005'),
  ('V', 'lattice_constant', 3.024, 'Å', 'BCC', 'Villars 2016'),
  ('V', 'cohesive_energy', -7.09, 'eV/atom', 'BCC', 'Kittel 2005'),
  ('Cr', 'lattice_constant', 2.884, 'Å', 'BCC', 'Villars 2016'),
  ('Cr', 'cohesive_energy', -6.68, 'eV/atom', 'BCC', 'Kittel 2005'),
  ('Fe', 'lattice_constant', 2.870, 'Å', 'BCC', 'Villars 2016'),
  ('Fe', 'cohesive_energy', -7.09, 'eV/atom', 'BCC', 'Kittel 2005')
ON CONFLICT (element, property, crystal_structure, temperature_k) DO NOTHING;
```

- [ ] **Step 3: Execute migration on Supabase Cloud**

使用 Supabase Dashboard SQL Editor 执行，或通过 API：
```bash
# Apply via psql or Supabase Dashboard
```

- [ ] **Step 4: Update verify-service to read from Supabase**

在 `supabase_client.py` 中添加:
```python
async def get_reference_values(element: str) -> dict:
    """Fetch reference values for an element from Supabase."""
    resp = await httpx_client.get(
        f"{SUPABASE_URL}/rest/v1/reference_values",
        params={"element": f"eq.{element}", "select": "property,value,unit,crystal_structure,source"},
        headers=safe_headers
    )
    resp.raise_for_status()
    return {r["property"]: r for r in resp.json()}
```

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/ reference_values
git commit -m "feat: add reference_values table migration with seed data"
```

**Verification:** `curl https://verify.nucpot.dpdns.org/health` 确认服务正常

---

## Task 5: 扩展前端 API (参考值管理)

**Priority:** P2 — 让管理员通过 UI 管理参考值

**Files:**
- Create: `src/app/api/admin/reference-values/route.ts`
- Create: `src/app/admin/reference-values/page.tsx` (可选)

- [ ] **Step 1: Create API route for reference values**

```typescript
// src/app/api/admin/reference-values/route.ts
// GET: list all reference values
// POST: add new reference value
// PATCH: update existing
// DELETE: remove
```

- [ ] **Step 2: Add tests**

```typescript
// tests/api/reference-values.test.ts
```

- [ ] **Step 3: Commit**

```bash
git add src/app/api/admin/reference-values/ tests/api/reference-values.test.ts
git commit -m "feat: add reference values management API"
```

---

## Task 6: 部署运维文档

**Priority:** P2 — 团队知识传承

**Files:**
- Create: `docs/phase4-setup-guide.md`

- [ ] **Step 1: Write deployment guide**

内容覆盖:
- ThinkStation 验证服务部署步骤
- systemd 服务管理命令
- Cloudflare Tunnel 配置
- Supabase 参考值表管理
- 常见故障排查

- [ ] **Step 2: Commit**

```bash
git add docs/phase4-setup-guide.md
git commit -m "docs: add Phase 4 deployment and operations guide"
```

---

## Execution Notes

**Task 依赖关系:**
- Task 1, 2 可并行（都是 CI/CD 保护）
- Task 3 独立（ThinkStation 配置）
- Task 4 → Task 5 有依赖（先有表再有 API）
- Task 6 最后（汇总文档）

**需要用户协助的步骤:**
- Task 1: 需要在 GitHub 添加 Supabase secrets (如果 CI 需要)
- Task 3: 需要确认 ThinkStation sudo 权限
- Task 4: 需要 Supabase Dashboard 执行 SQL

**风险控制:**
- 所有 ThinkStation 操作先 dry-run
- Supabase migration 先在 local 测试
- systemd 配置不直接执行，提供脚本让用户确认后运行
