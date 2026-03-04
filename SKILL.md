# codex-register

## 用途
用于 Codex 账号池自动化的基础设施与后续流程脚本集合（初始化数据库、配置、分阶段导入与统计）。

## 核心脚本
- `scripts/db_init.py`：初始化 `codex_pool.db`，创建 `staging`、`register_log`、`pool_snapshot` 表及索引（幂等）。

## 常用命令
```bash
# 初始化数据库
python3 scripts/db_init.py

# 查看已创建表
sqlite3 /home/heye/.openclaw/data/codex_pool.db ".tables"
```
