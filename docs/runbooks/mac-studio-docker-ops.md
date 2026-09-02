# Mac Studio Docker 运维手册（本机经验固化）

> 适用范围：Mac Studio 生产主机（nucpot-prod / staging / autovc / supabase 全部容器）。
> 本文档固化 2026-08 以来实战踩坑结论，通用教程见官方文档；**本机特殊性优先于通用实践**。

## 1. 磁盘清理纪律（最重要）

### 原则：语义感知清理 > 通用 prune

**禁止** 在本机随手执行 `docker system prune -a` / `prune -a --volumes`：

- `-a` 会把**回滚用的 SHA tag 镜像**（上一稳定版 + candidate tag）当 unused 删掉——
  回滚操作依赖它们：`PROD_IMAGE_TAG=<prev-sha> docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d`（无重建、秒级）
- `--volumes` 会删**生产数据库卷**（nucpot-prod-db 数据、uploads、LightRAG 索引）——不可恢复

### 正确的清理顺序

```bash
docker system df                          # 先诊断，看 Reclaimable 分布
docker builder prune -af                  # 构建缓存，单条释放最多（曾一次回收 25GB）
bash tools/prod-tag-retention/prune.sh --repo nucpot-prod-api --keep 3   # 仓库自带 candidate 清理
docker image prune -f                     # 悬空层
# 历史完整 SHA tag 手动删，但必须保留：
#   - 当前运行版本（docker ps 看到的）
#   - 上一稳定版（回滚点）
#   - staging 锁定的 lightrag:0d901f48（.env.staging STAGING_IMAGE_TAG）
#   - 所有 :latest 标签（compose 默认 tag，动了破坏下次部署）
```

### 已知误报

`docker system df` 的 Reclaimable 把**共享层重复计算**（api 三版本共享基础层，
曾显示 7.9GB "reclaimable" 实际只能挤几百 MB 且会失去回滚点）。**看绝对值不信百分比**。

### 实测基线（2026-08-31）

清理前 65.37GB/53 镜像 → 清理后 20.23GB/21 镜像（-45GB）。正常稳态：
部署脚本自动保留每仓库最近 10 个 SHA tag + 3 个 candidate（~60-80GB 上限设计值）。

## 2. 部署链结构（脚本化，勿回退 heredoc）

部署体 = `scripts/deploy_prod.sh`（scp 到远端 /tmp 后 `bash` 执行，stdin 不是脚本载体）。
**历史教训：streamed heredoc 模式发生 5 次静默 cutover**（构建成功但容器没切换、job 仍绿），
已于 2026-08-31 根治（issue #1050 / PR #1052 #1053 #1055 #1056 #1059）。防护链：

1. 部署脚本内 NFM-3320 cutover 断言（before/after 快照比对）
2. heredoc **外**的 job 级 guard：断言 4 容器 `Config.Image == 本次 SHA`，不匹配 exit 71/72
3. 主机侧每日 drift watchdog（`~/.hermes/scripts/prod-drift-watchdog.sh`，09:00 cron，滞后才报警）

### 判断"部署是否真的生效"

**绿 job ≠ 已上线**。必须验证：

```bash
docker ps --format '{{.Names}}\t{{.Image}}' | grep nucpot-prod   # Image==最新 SHA?
docker inspect nucpot-prod-api --format '{{.State.StartedAt}}'     # 启动时间对吗?
```

外部 curl 200 **不能**证明新版上线（旧容器也健康也返回 200）。

## 3. 本机环境陷阱（每条都是实战代价）

| 陷阱 | 症状 | 规避 |
|---|---|---|
| `ssh host "cmd"` 非登录 zsh | `docker: command not found`（不读 .zprofile） | 命令前缀 `export PATH="/usr/local/bin:..."` |
| Docker keychain 锁死构建 | build 阶段 credential helper 卡死 | `DOCKER_CONFIG=/tmp/nfm848-no-cred-docker-config` + symlink compose 插件（部署脚本已内置） |
| `docker compose run < /dev/null` | 曾经吞掉 heredoc 后续所有命令 | 已根治（脚本化）；迁移类临时容器仍注意 stdin |
| 全局 gitconfig insteadOf | runner checkout 失败（某 agent 改全局配置污染所有 GHA job） | 自定义 git 配置一律 `--local` |
| GFW 网络抖动 | pip 清华源 setuptools 拉取失败、apt 53KB/s | Dockerfile 三级 pip 重试链可自愈；job 失败先 rerun 再排查 |
| IPv6 被阻断 | Docker Hub 认证超时 | 用缓存镜像/国内源重试 |
| 端口 8002 双占用 | prod-api 8002:8000 与 AutoVC 冲突 | 已在 compose 移除 prod 的 8002 映射 |
| 端口 8000 被 host 进程占用 | e2e compose 起不来 | 先 `lsof -i :8000`（曾是被 honcho dev server 占用） |

## 4. Dockerfile 构建要点（本机版）

- `--no-cache` 全量构建（NFM-2376：曾因 stale layer cache 出过事故）
- `DOCKER_BUILDKIT=0`（NFM-848：daemon 侧元数据解析，绕开 keychain）
- `.dockerignore` **必须**排除：`.paperclip`（35GB!）、`.worktrees`、`.venv`、`.gitnexus`——
  漏一个构建上下文就会卡死
- 构建网络 `network:host` + 国内镜像源（apt/pip 走直连不走代理）

## 5. 多套环境清单（防误操作）

| Compose 项目 | 容器前缀 | 用途 | 镜像策略 |
|---|---|---|---|
| nucpot-prod | nucpot-prod-* | **生产**（CF Tunnel 域名流量） | SHA tag，自动保留 |
| nucpot-staging | nucpot-staging-* | staging | **锁定 SHA**（.env.staging，勿用 latest——曾因此被 main push 滚动更新） |
| nucpot-autovc-repo | nucpot-autovc-repo-* | AutoVC 验证 | latest（独立栈） |
| supabase | supabase_db_nucpot | 本地 Supabase（云端未启用，配置是残留） | 固定版本 |
| nucpot-prod-linux（ThinkStation） | — | 已冻结的迁移实验残留，非生产 | frozen tag，勿动 |

**ThinkStation 上的 nucpot-prod-linux 不是生产**——真正生产在 Mac Studio。勿混淆。

## 6. 备份现状（风险提示）

4 个核心 Docker 卷（主库/uploads/LightRAG/Redis）**无任何备份**，主库未开 archive_mode，
RTO 为小时级。夸克网盘备份脚本已开发（分支 fix/supabase-rls-2026-08-16）但未做真实上传验证。这是当前最大数据风险。
