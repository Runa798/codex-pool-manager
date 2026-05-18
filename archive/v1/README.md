# codex-pool-manager

> 自动化 ChatGPT/Codex 账号注册、清理、刷新与池化管理工具

---

## 目录

- [项目定位](#项目定位)
- [功能列表](#功能列表)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置文件总览](#配置文件总览)
- [config.yaml 参数说明](#configyaml-参数说明)
- [manage.py 命令参考](#managepy-命令参考)
- [预设一键导入](#预设一键导入)
- [crontab 参考配置](#crontab-参考配置)
- [架构说明](#架构说明)
- [工作流详解](#工作流详解)
- [故障排查](#故障排查)
- [安全建议](#安全建议)
- [FAQ](#faq)
- [更新建议](#更新建议)

---

## 项目定位

`codex-pool-manager` 是一套围绕 ChatGPT/Codex 账号生命周期管理的自动化工具：

1. 注册：按批次并发注册账号。
2. 缓冲：先进入蓄水池，避免直接写爆线上号池。
3. 补池：按阈值从蓄水池补充到 CPA 活跃池。
4. 维护：自动刷新 token、清理 401 死号。
5. 额度治理：额度耗尽临时禁用，恢复后自动启用。
6. 运维集成：提供 crontab 模板与预设一键导入模式。

适用对象：

- 个人开发者（本地长期维护账号池）
- 小团队（需要固定质量的可用账号集）
- 代运维同学（需要自动巡检 + 可回滚流程）

---

## 功能列表

- 批量注册 ChatGPT 账号（支持多域名轮换、并发控制）
- 蓄水池 + CPA 号池双层架构（蓄水池缓冲，CPA 保持活跃号）
- Token 自动刷新（临近过期提前续期）
- 死号自动清理（401 检测 + 删除）
- 额度监控（5 小时窗口耗尽临时禁用，恢复后自动启用）
- Cloudflare Email Routing 一键配置
- 预设一键导入（资产注入 + 限速 + 种子账号）
- mihomo 代理一键安装与订阅配置
- CLIProxyAPI 一键安装

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Linux / WSL2 | - | Windows 用户需安装 WSL2：https://learn.microsoft.com/zh-cn/windows/wsl/install |
| Python | 3.11+ | `python3 --version` 验证 |
| mihomo | 最新版 | 代理管理，`setup-proxy` 自动安装 |
| CLIProxyAPI | 最新版 | 号池终端，`install-cpa` 自动安装 |
| Capsolver 账号 | - | https://capsolver.com，注册获取 API Key |
| Cloudflare 账号 + 自有域名 | - | 用于自定义邮箱接收验证码（可选，但强烈推荐） |

### 推荐机器配置

- CPU：2 核以上
- 内存：4GB 以上
- 磁盘：2GB 可用空间
- 网络：稳定代理链路

### 必备命令

```bash
python3 --version
pip --version
git --version
curl --version
```

---

## 快速开始

### 第一步：克隆项目

```bash
git clone https://github.com/Runa798/codex-pool-manager.git
cd codex-pool-manager
pip install -r requirements.txt
```

### 第二步：安装依赖服务

```bash
# 安装 CPA（账号池终端）
python3 manage.py install-cpa

# 安装并配置代理（mihomo）
python3 manage.py setup-proxy
```

### 第三步：配置 config.yaml

```bash
cp config.defaults.yaml config.yaml
# 编辑 config.yaml，填写所有必填项
nano config.yaml
```

### 第四步：配置邮件路由（可选但推荐）

```bash
# 自动为 config.yaml 中的所有域名配置 CF Email Routing
python3 manage.py setup-cf
```

### 第五步：部署 Cloudflare Worker（邮件 OTP 接收）

参考 `worker/email-worker.js`，在 Cloudflare Workers 中创建新 Worker 并粘贴代码。  
需要绑定 KV Namespace（名称：`CODEX_OTP`）。

### 第六步：基础健康检查

```bash
python3 manage.py --help
python3 manage.py status
```

---

## 配置文件总览

项目中常见配置文件：

- `config.defaults.yaml`：默认模板，不建议直接改。
- `config.yaml`：主配置文件，实际运行读取此文件。
- `preset/preset-profile.example.yaml`：预设模式样例，不含真实密钥。
- `crontab.example`：定时任务参考模板。

配置原则：

1. 模板文件可提交；真实密钥文件不要提交。
2. 所有敏感值都使用占位符管理。
3. 每次改配置后先跑 `status` 再执行批量任务。

---

## config.yaml 参数说明

下面按模块解释 `config.yaml` 关键字段。

### 1) `mail`

- `provider`: 邮件来源，支持 `cf_worker` 或 `duckmail`。
- `cf_worker_url`: Cloudflare Worker 地址，用于收取 OTP。
- `domains`: 域名列表，用于注册邮箱轮换。

建议：

- 生产优先使用 `cf_worker`。
- `domains` 至少配置 2~3 个，降低单域风控。

### 2) `capsolver`

- `api_key`: 验证码服务 API Key。

说明：

- 这是核心必填项，`status` 会校验。

### 3) `proxy`

- `http`: 本地代理地址，例如 `http://127.0.0.1:7894`。
- `subscription_url`: 代理订阅链接，`setup-proxy` 会读取此值。

建议：

- 如果你使用机场订阅，务必填写 `subscription_url`。
- 若为固定节点，也可只保留 `http`。

### 4) `cpa`

- `url`: CPA API 地址。
- `api_key`: CPA 管理密钥。
- `auths_dir`: CPA 本地 auth 文件目录。

说明：

- `cpa.api_key` 为空时，`status` 会阻止执行并友好提示。

### 5) `cloudflare`

- `email`: Cloudflare 登录邮箱。
- `api_key`: Global API Key。
- `account_id`: 账户 ID。

用途：

- 自动化创建 Email Routing 记录。

### 6) `register`

- `workers`: 并发线程数。
- `batch_size`: 每批注册目标数。
- `daily_limit_per_domain`: 每域名每日上限。

建议值：

- `workers=2~4`
- `batch_size=50~200`

### 7) `pool`

- `max`: 号池上限。
- `min`: 号池补充阈值。
- `reservoir_min`: 蓄水池最低水位（低于触发注册）。

调参建议：

- 高峰期提高 `min`。
- 低资源机器降低 `max`。

---

## manage.py 命令参考

```bash
python manage.py status           # 查看号池/蓄水池状态
python manage.py register         # 启动注册（按配置注册一批）
python manage.py register --domain yourdomain.win  # 指定域名
python manage.py fill-pool        # 从蓄水池补充到号池
python manage.py clean            # 清理 401 死号
python manage.py check-quota      # 检查额度耗尽账号并临时禁用
python manage.py restore-quota    # 恢复额度已刷新的账号
python manage.py refresh          # 刷新即将过期的 token
python manage.py setup-cf         # 自动配置 CF Email Routing
python manage.py install-cpa      # 安装 CPA 二进制
python manage.py setup-proxy      # 安装并配置 mihomo 代理
python manage.py preset-setup --profile preset-profile.yaml  # 预设一键导入
```

### status 的友好提示逻辑

当下列任意字段为空时，会提示配置未完成，不再输出误导性的全 0 状态：

- `cpa.api_key`
- `capsolver.api_key`

提示信息：

```text
⚠️  config.yaml 未配置，请先填写必填项后再运行。
    参考: config.yaml 中的注释说明
```

---

## 预设一键导入

维护者可准备一个 profile 发给朋友，用户在自己机器上执行：

```bash
python3 manage.py preset-setup --profile preset-profile.yaml
```

导入后自动：

- 注入 CF/代理/Capsolver 等资产
- 强制限速（每域名每日 ≤ 50 个）
- 导入初始 200 个种子账号到 CPA

### profile 设计原则

1. 可复用：维护者只改资产字段。
2. 可控：限制字段由系统强制写入。
3. 可审计：导入流程输出摘要。

---

## crontab 参考配置

项目根目录提供 `crontab.example`，可直接复制后按路径修改。

核心任务包含：

- `restore-quota`：每 30 分钟恢复额度已刷新的号
- `check-quota`：每 3 小时检查并临时禁用耗尽号
- `clean`：每日清理 401 死号
- `refresh` + `fill-pool`：每日维护 token 与号池
- `register --auto`：按蓄水池水位自动补充

---

## 架构说明

```text
注册机 (register/)
    ↓ 注册成功
CPA (号池终端) ← fill-pool ← 蓄水池 (reservoir.db)
    ↓ token 失效
clean / check-quota / restore-quota
```

### 双层池化的意义

- 蓄水池负责缓冲，避免突发写入影响 CPA 稳定性。
- CPA 号池保持活跃工作集，优先保证可用账号质量。
- 清理与刷新任务负责“提纯”池内账号。

---

## Storm Guard（CPA 写风暴防护）

CPA 在 WSL2 下依赖 inotify 监听 auth 文件变化。若 auth-dir 位于 DrvFS（`/mnt/d/` 等 Windows 挂载路径），inotify 会产生大量冗余事件，导致 auth WRITE 风暴，CPA 尾延迟显著升高（p99 可达 54s+）。

**解决方案：**
1. `config.yaml` 中 `cpa.auths_dir` 设为 WSL 本地路径（`~/cliproxyapi_runtime/auths`，默认值已设好）
2. 部署 storm guard 作为兜底防护：

```bash
# 复制并填写 env 文件
cp scripts/cpa_storm_guard.env.example scripts/cpa_storm_guard.env
nano scripts/cpa_storm_guard.env

# 安装为 systemd 用户服务
mkdir -p ~/.config/systemd/user/
cat > ~/.config/systemd/user/cpa-storm-guard.service << 'EOF'
[Unit]
Description=CPA Storm Guard
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/codex-pool-manager/scripts/cpa_storm_guard.env
ExecStart=python3 %h/codex-pool-manager/scripts/cpa_storm_guard.py daemon
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cpa-storm-guard
```

## 单主机限制

CLIProxyAPI 在单主机只支持运行一个实例。多节点扩容需要部署多台机器，每台各跑一个 CPA 实例。

## 工作流详解

### 注册阶段

1. 读取配置与代理。
2. 按域名和并发执行注册。
3. 写入临时结果与注册输出。

### 入池阶段

1. 注册成功账号进入蓄水池。
2. `fill-pool` 按阈值转移到 CPA。
3. CPA 对外提供稳定账号集。

### 巡检阶段

1. `clean` 检测 401 并清理。
2. `check-quota` 标记额度耗尽账号并禁用。
3. `restore-quota` 在额度恢复后重新启用。
4. `refresh` 续签临期 token。

---

## 故障排查

### 问题 1：`status` 直接提示配置未完成

原因：

- `cpa.api_key` 或 `capsolver.api_key` 为空。

处理：

```bash
nano config.yaml
# 填写 cpa.api_key / capsolver.api_key
python3 manage.py status
```

### 问题 2：`setup-proxy` 后无法连通

检查：

1. `proxy.http` 是否与本地端口一致。
2. `subscription_url` 是否可访问。
3. 系统防火墙是否阻断。

### 问题 3：`check-quota` 无结果

可能原因：

- 当前无可用 codex provider 账号。
- CPA 返回非 200 状态。
- auth 文件路径不可读。

---

## 安全建议

1. 不要把真实 `config.yaml` 提交到仓库。
2. 不要在日志中打印完整 token。
3. 使用最小权限的 Cloudflare API Key。
4. 周期性轮换 Capsolver 与 CPA 密钥。
5. 将 `preset/*.yaml` 保持在忽略列表内。

---

## FAQ

### Q1：可以只用 duckmail 吗？

可以，但成功率与稳定性通常弱于自有域名 + Worker。

### Q2：为什么要有蓄水池，不直接写 CPA？

为了把注册波动与线上使用隔离，降低运行风险。

### Q3：check-quota 会不会误伤？

脚本依据额度响应判断，并给账号打 `quota_disabled` 标记，后续可自动恢复。

### Q4：朋友模式能否改并发和限额？

默认不建议。朋友模式的限制是为了安全和长期稳定。

### Q5：如何快速回滚配置？

保留一份 `config.yaml.bak`，验证失败时立即恢复。

---

## 更新建议

如果你准备继续演进项目，建议按以下顺序进行：

1. 增加结构化日志（JSON）方便 ELK/ClickHouse 接入。
2. 补充单元测试（配置校验、命令分发、quota 判定）。
3. 增加 Prometheus 指标导出。
4. 提供 `docker-compose` 一键部署模板。
5. 增加 Web 控制台查看池状态。

---

## 免责声明

本项目仅用于自动化运维与学习研究，请在合法合规前提下使用。  
任何账号、域名、代理、第三方服务的使用责任由使用者自行承担。

---

## 致谢

感谢所有贡献者和使用者的反馈。欢迎提交 Issue 和 PR 共同完善。

---

## 附录 A：最小可用配置示例

```yaml
mail:
  provider: "cf_worker"
  cf_worker_url: "https://your-worker.workers.dev"
  domains:
    - "example.win"

capsolver:
  api_key: "CAP-xxxxxxxx"

proxy:
  http: "http://127.0.0.1:7894"
  subscription_url: "https://your-sub-url"

cpa:
  url: "http://localhost:8317"
  api_key: "sk-xxxxxxxx"
  auths_dir: "./cpa/runtime/auths"

register:
  workers: 2
  batch_size: 100
  daily_limit_per_domain: 200

pool:
  max: 388
  min: 350
  reservoir_min: 500
```

---

## 附录 B：命令速查

```bash
python3 manage.py status
python3 manage.py register
python3 manage.py fill-pool
python3 manage.py clean
python3 manage.py check-quota
python3 manage.py restore-quota
python3 manage.py refresh
```

---

## 附录 C：推荐定时顺序

1. 先 `restore-quota`，释放可恢复账号。
2. 再 `check-quota`，回收新增耗尽账号。
3. 每天跑 `clean`，清理 401 死号。
4. 每天跑 `refresh` + `fill-pool`，保持池活性。
5. 水位低时再触发 `register --auto`。

---

## License

如仓库根目录存在 `LICENSE` 文件，以该文件为准。

## Credits / 致谢

本项目为二次开发，基于以下开源项目整合而成：

- **注册机核心** (`register/chatgpt_register.py`)：来自 LINUX DO 社区 kun775 发布的 ChatGPT 批量注册工具，感谢原作者的出色工作
- **账号清理工具** (`cleaner/clean_codex.py`)：参考 [HsMirage/CliproxyAccountCleaner](https://github.com/HsMirage/CliproxyAccountCleaner) 的设计思路
- **CPA 账号池终端**：[router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)

如有侵权请联系删除。本项目仅供学习研究使用。
