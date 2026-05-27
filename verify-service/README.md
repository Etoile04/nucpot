# NucPot Verify Service

势函数自动验证服务 — 使用 ASE (Atomic Simulation Environment) 计算材料性质并与参考值对比评分。

## 功能

- 晶格常数 (EOS 拟合)
- 弹性常数 (C11, C12, C44 via 应变-能量法)
- 体积模量
- 空位形成能
- A-F 评分系统

## 快速开始

```bash
# 1. 安装依赖
cd verify-service
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 SUPABASE_SERVICE_KEY

# 3. 启动服务
python -m verify
# 或
uvicorn verify.api.routes:app --reload --port 8000
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/verify` | POST | 提交验证任务 |
| `/api/verify/{job_id}` | GET | 查询验证结果 |
| `/api/reference-values/{element}/{structure}` | GET | 查询参考值 |

### 提交验证

```bash
curl -X POST http://localhost:8000/api/verify \
  -H "Content-Type: application/json" \
  -d '{
    "potential_id": "uuid-of-potential",
    "properties_to_compute": ["lattice_constant", "elastic_constants", "bulk_modulus", "vacancy_formation_energy"]
  }'
```

### 查询结果

```bash
curl http://localhost:8000/api/verify/{job_id}
```

## Docker

```bash
docker build -t nucpot-verify .
docker run -p 8000:8000 --env-file .env nucpot-verify
```

## 支持的势函数类型

| 类型 | 引擎 | 状态 |
|------|------|------|
| EAM | ASE (ase.calculators.eam) | ✅ 已支持 |
| EMT | ASE (测试用) | ✅ 已支持 |
| MEAM | LAMMPS subprocess | 🔜 计划中 |
| ML/RANN | LAMMPS subprocess | 🔜 计划中 |
| Buckingham | GULP/LAMMPS | 🔜 计划中 |

## 评分标准

| 等级 | 相对误差 |
|------|----------|
| A | ≤ 1% |
| B | ≤ 3% |
| C | ≤ 5% |
| D | ≤ 10% |
| F | > 10% |

## 项目结构

```
verify-service/
├── src/verify/
│   ├── api/          # FastAPI routes + schemas
│   ├── core/         # Calculator, grading, potential loader
│   ├── workers/      # Background task runner
│   ├── config.py     # Settings (env vars)
│   └── database.py   # Supabase REST client
├── pyproject.toml
├── Dockerfile
└── requirements.txt
```
