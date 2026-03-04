# Codex Pool Manager v2.0

Codex 账号池自动化管理系统 v2.0 - 完全重构版本

## 架构

```
register_manager.py  →  三路注册机调度 (kun775/cc/uk)
pool_manager.py      →  号池管理 + CPA 导入
db_init.py           →  SQLite 数据库初始化
systemd/             →  定时任务配置
```

## 快速开始

1. 安装依赖: `pip install -r requirements.txt`
2. 配置: 复制 `.env.example` 为 `.env` 并填写
3. 初始化数据库: `python3 scripts/db_init.py`
4. 测试: `python3 scripts/register_manager.py --scan-only`
5. 启动定时任务: `systemctl --user enable codex-register-daily.timer`

## 定时任务

- `codex-register-daily`: 每天 03:30 启动三路注册机
- `codex-pool-manager`: 每 12 小时补充号池
- `cpa-daily-clean`: 每 6 小时清理死号

## 与 v1.0 的区别

- 完全重写，不兼容 v1.0
- 使用 SQLite 替代文件状态
- 三路注册机并行 (IPRoyal NA / Canada Bell / aaitr US)
- systemd 定时任务替代 crontab
- 每日目标: 250 新账号

## 许可证

MIT
