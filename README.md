# McpEye 智守

McpEye 智守 是一个面向 Linux 服务器巡检场景的 MCP 管理系统，提供网页后台、SSH 巡检能力和机器人可调用的 MCP 接口。

你可以在后台维护多台服务器，用自然语言名称区分业务主机，然后让机器人直接提问：

- 帮我看一下算力服务器的网络状态
- 帮我看一下算力服务器的处理器型号
- 帮我看一下算力服务器的硬盘使用率
- 执行一下 GPU 巡检命令

## 功能概览

- 登录后台管理服务器
- 支持 SSH 密码认证
- 支持 SSH 私钥和私钥口令
- 实时巡检 CPU、内存、磁盘、网络和系统信息
- 支持批量查看服务器延迟、在线状态、认证失败、磁盘告警等
- 支持独立管理巡检命令
- 支持按服务器和按标签分配巡检命令
- 支持 MCP 工具调用
- 支持日志查看请求和返回内容
- 支持后台配置小智接入地址和 Token

## 技术栈

- FastAPI
- Jinja2
- SQLite
- Paramiko
- FastMCP

## 快速启动

### 1. 安装依赖

```bash
pip install -e .
```

### 2. 准备环境变量

复制 `.env.example` 到 `.env`，至少确认以下配置：

```env
APP_HOST=127.0.0.1
APP_PORT=8765
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123456
APP_SECRET=
XIAOZHI_BRIDGE_ENABLED=false
XIAOZHI_ENDPOINT_URL=
XIAOZHI_RECONNECT_DELAY_SECONDS=5
```

说明：

- `APP_SECRET` 留空时，程序会自动生成本地密钥
- `XIAOZHI_ENDPOINT_URL` 可以先留空，之后在后台设置

### 3. 启动服务

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

启动后访问：

- 后台首页: `http://127.0.0.1:8765/`
- 服务器管理: `http://127.0.0.1:8765/servers`
- 巡检命令: `http://127.0.0.1:8765/commands`
- 请求日志: `http://127.0.0.1:8765/logs`
- 系统设置: `http://127.0.0.1:8765/settings`
- MCP 地址: `http://127.0.0.1:8765/mcp`


## Docker 部署

### 方式一：使用 Docker Compose

1. 复制环境变量文件：

```bash
cp .env.example .env
```

2. 编辑 `.env`，至少修改管理员密码和密钥：

```env
APP_HOST=0.0.0.0
APP_PORT=8765
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请修改为强密码
APP_SECRET=请修改为随机长字符串
XIAOZHI_BRIDGE_ENABLED=false
XIAOZHI_ENDPOINT_URL=
XIAOZHI_RECONNECT_DELAY_SECONDS=5
```

`APP_SECRET` 用于会话签名和凭据加密。生产环境建议固定填写，不要留空，否则更换容器或数据目录后可能无法解密已保存的服务器密码或密钥。

3. 启动服务：

```bash
docker compose up -d --build
```

4. 查看状态和日志：

```bash
docker compose ps
docker compose logs -f
```

启动后访问：

- 后台首页：`http://服务器IP:8765/`
- MCP 地址：`http://服务器IP:8765/mcp`

数据会保存在 Docker volume `mcpeye_data` 中，包括 SQLite 数据库和本地密钥文件。

### 方式二：使用 Docker 命令

```bash
docker build -t mcpeye-zhishou:latest .
docker run -d \
  --name mcpeye-zhishou \
  --restart unless-stopped \
  --env-file .env \
  -e APP_HOST=0.0.0.0 \
  -e APP_PORT=8765 \
  -p 8765:8765 \
  -v mcpeye_data:/app/data \
  mcpeye-zhishou:latest
```

### 升级容器

```bash
git pull
docker compose up -d --build
```

只要继续使用同一个 `mcpeye_data` volume，服务器配置、巡检命令、日志和历史数据会保留。

### 安全提醒

- 不要把 `.env`、`data/`、数据库文件或小智 Token 提交到 GitHub。
- 生产环境务必修改 `ADMIN_PASSWORD` 和 `APP_SECRET`。
- 如果小智 Token 曾经泄露，请先在小智侧重置 Token，再更新 `.env` 或后台配置。

## MCP 能力

当前提供的工具包括：

- `list_servers`
- `get_server_snapshot`
- `get_server_metric`
- `run_server_custom_check`

适合的提问方式包括：

- 查询某台服务器的完整巡检信息
- 单独查询 CPU、内存、磁盘、网络、处理器、主机名、系统信息
- 执行已保存的巡检命令

## 项目定位

McpEye 智守 适合以下场景：

- 运维巡检后台
- 算力服务器状态查看
- 机器人问答式服务器诊断
- 多台服务器的统一 SSH 巡检管理

## 开源信息

- 项目名称：McpEye 智守
- 项目主页：[github.com/Call123X](https://github.com/Call123X)
