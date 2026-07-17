<img width="1893" height="952" alt="image" src="https://github.com/user-attachments/assets/9fc25e9f-5dba-4680-9b2a-4ec1109aa4ea" /># McpEye 智守
<img width="1880" height="952" alt="image" src="https://github.com/user-attachments/assets/b9ba9939-71c6-48a1-b3ec-0e2ed26db774" />

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
<img width="1893" height="952" alt="image" src="https://github.com/user-attachments/assets/460df840-1eeb-4eb7-a36e-3688284130f7" />
<img width="1882" height="959" alt="image" src="https://github.com/user-attachments/assets/0b8c9c0f-f5bd-46b1-b6b2-2e6f3f8dbebd" />
<img width="1867" height="952" alt="image" src="https://github.com/user-attachments/assets/868714b8-7e05-41c2-8385-8b1cbfcf2edc" />
