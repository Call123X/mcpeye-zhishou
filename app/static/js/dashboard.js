const state = {
  servers: [],
  serverTags: [],
  serverHealth: [],
  commands: [],
  selectedServerId: null,
  selectedCommandId: null,
  xiaozhi: null,
  alerting: null,
  currentView: document.body.dataset.initialView || "overview",
};

const pageTitles = {
  overview: "监控总览",
  servers: "服务器管理",
  commands: "巡检命令",
  logs: "请求日志",
  settings: "系统设置",
  about: "关于",
};

const categoryLabels = {
  mcp_tool: "MCP 工具",
  mcp_protocol: "MCP 协议",
  xiaozhi: "小智桥接",
  ssh_probe: "SSH 巡检",
  alert: "主动告警",
  settings: "后台设置",
};

const serverForm = document.getElementById("server-form");
const commandForm = document.getElementById("command-form");
const xiaozhiForm = document.getElementById("xiaozhi-settings-form");
const alertForm = document.getElementById("alert-settings-form");
const probeButton = document.getElementById("probe-button");

function initializeView() {
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== state.currentView;
  });
  document.querySelectorAll("[data-nav-view]").forEach((link) => {
    link.classList.toggle("active", link.dataset.navView === state.currentView);
  });
  document.getElementById("page-title").textContent = pageTitles[state.currentView] || pageTitles.overview;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("登录已过期");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败 (${response.status})`);
  }
  return payload;
}

async function fetchBootstrap() {
  const payload = await requestJson("/api/bootstrap");
  state.servers = payload.servers || [];
  state.serverTags = payload.server_tags || [];
  state.commands = payload.commands || [];

  document.getElementById("mcp-url").textContent = payload.defaults.mcp_url;
  document.getElementById("server-count").textContent = state.servers.length;
  document.getElementById("server-list-count").textContent = `${state.servers.length} 台`;
  document.getElementById("command-list-count").textContent = `${state.commands.length} 条`;

  ensureSelectedServer();
  ensureSelectedCommand();
  renderOverviewServerOptions();
  renderServerList();
  renderCommandList();
  renderServerDetail();
  renderCommandDetail();
  renderCommandTargetSelectors();
  renderXiaozhi(payload.integrations.xiaozhi, true);
  renderAlerting(payload.integrations.alerting, true);

  if (state.currentView === "logs") {
    await loadLogs();
  }
  if (state.currentView === "servers") {
    await loadServerHealthBoard();
  } else {
    renderServerHealthBoard(state.serverHealth);
  }
}

function ensureSelectedServer() {
  const exists = state.servers.some((server) => server.id === state.selectedServerId);
  state.selectedServerId = exists ? state.selectedServerId : state.servers[0]?.id || null;
}

function ensureSelectedCommand() {
  const exists = state.commands.some((command) => command.id === state.selectedCommandId);
  state.selectedCommandId = exists ? state.selectedCommandId : state.commands[0]?.id || null;
}

function activeServer() {
  return state.servers.find((server) => server.id === state.selectedServerId) || null;
}

function activeCommand() {
  return state.commands.find((command) => command.id === state.selectedCommandId) || null;
}

function serverHealthRecord(serverId) {
  return state.serverHealth.find((item) => item.server_id === serverId) || null;
}

function applicableCommandsForServer(serverId) {
  return state.commands.filter((command) =>
    (command.applicable_servers || []).some((server) => server.id === serverId),
  );
}

function commandSummary(command) {
  const all = command.scope_all_servers ? "全部服务器" : "";
  const direct = command.server_names?.length ? `${command.server_names.length} 台服务器` : "";
  const tags = command.tags?.length ? `${command.tags.length} 个标签` : "";
  return [all, direct, tags].filter(Boolean).join(" / ") || "未分配范围";
}

function renderOverviewServerOptions() {
  const select = document.getElementById("overview-server-select");
  if (!state.servers.length) {
    select.innerHTML = '<option value="">尚未添加服务器</option>';
    select.disabled = true;
    probeButton.disabled = true;
    return;
  }
  select.disabled = false;
  probeButton.disabled = false;
  select.innerHTML = state.servers
    .map((server) => `<option value="${server.id}">${escapeHtml(server.name)}</option>`)
    .join("");
  if (state.selectedServerId) {
    select.value = String(state.selectedServerId);
  }
}

function renderServerList() {
  const container = document.getElementById("server-list");
  if (!state.servers.length) {
    container.innerHTML =
      '<div class="empty-state"><strong>还没有服务器</strong><span>点击“添加服务器”创建第一条 SSH 连接。</span></div>';
    return;
  }
  container.innerHTML = state.servers
    .map((server) => {
      const active = server.id === state.selectedServerId ? "active" : "";
      const tags = (server.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
      const health = serverHealthRecord(server.id);
      const status = statusPresentation(health);
      const latency = health?.latency_ms == null ? "未检测" : `${health.latency_ms} ms`;
      return `
        <button type="button" class="server-list-item ${active}" data-server-id="${server.id}">
          <span class="server-list-title">
            <strong>${escapeHtml(server.name)}</strong>
            <i>${server.auth_type === "key" ? "密钥" : "密码"}</i>
          </span>
          <span class="server-address">${escapeHtml(server.host)}:${server.port} · ${escapeHtml(server.username)}</span>
          <span class="server-health-inline">
            <b class="health-badge ${status.css}">${status.label}</b>
            <small>${escapeHtml(latency)}</small>
          </span>
          <span class="tag-row">${tags || "<span>未设置标签</span>"}</span>
        </button>
      `;
    })
    .join("");
  container.querySelectorAll("[data-server-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedServerId = Number(button.dataset.serverId);
      renderOverviewServerOptions();
      renderServerList();
      renderServerDetail();
    });
  });
}

function renderServerDetail() {
  const empty = document.getElementById("server-detail-empty");
  const card = document.getElementById("server-detail-card");
  const editButton = document.getElementById("edit-server-button");
  const probeDetailButton = document.getElementById("detail-probe-button");
  const server = activeServer();

  if (!server) {
    empty.hidden = false;
    card.hidden = true;
    editButton.disabled = true;
    probeDetailButton.disabled = true;
    return;
  }

  empty.hidden = true;
  card.hidden = false;
  editButton.disabled = false;
  probeDetailButton.disabled = false;

  const health = serverHealthRecord(server.id);
  const status = statusPresentation(health);
  const commands = applicableCommandsForServer(server.id);

  document.getElementById("server-detail-status").className = `summary-chip ${status.css}`;
  document.getElementById("server-detail-status").textContent = status.label;
  document.getElementById("server-detail-name").textContent = server.name;
  document.getElementById("server-detail-meta").textContent = `${server.host}:${server.port} · ${server.username}`;
  document.getElementById("server-detail-auth").textContent = server.auth_type === "key" ? "SSH 密钥" : "密码";
  document.getElementById("server-detail-tags").textContent = (server.tags || []).join(" / ") || "未设置";
  document.getElementById("server-detail-notes").textContent = server.notes || "未填写";
  document.getElementById("server-detail-command-count").textContent = `${commands.length} 条`;
  document.getElementById("selected-server-commands").innerHTML = commands.length
    ? commands.map((command) => `<span class="token-chip">${escapeHtml(command.name)}</span>`).join("")
    : '<span class="token-chip muted">暂无适用命令</span>';
}

async function loadServerHealthBoard() {
  const button = document.getElementById("refresh-server-health-button");
  button.disabled = true;
  button.textContent = "刷新中...";
  try {
    state.serverHealth = await requestJson("/api/server-status-board");
    renderServerHealthBoard(state.serverHealth);
    renderServerList();
    renderServerDetail();
  } catch (error) {
    document.getElementById("server-health-summary").innerHTML = `<span class="inline-error">${escapeHtml(error.message)}</span>`;
    document.getElementById("server-health-table-body").innerHTML = `<tr><td colspan="7" class="status-table-empty">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    button.disabled = false;
    button.textContent = "批量刷新状态";
  }
}

function renderServerHealthBoard(rows) {
  const summary = document.getElementById("server-health-summary");
  const tableBody = document.getElementById("server-health-table-body");
  if (!summary || !tableBody) return;

  if (!state.servers.length) {
    summary.innerHTML = '<span class="summary-chip neutral">还没有可巡检的服务器</span>';
    tableBody.innerHTML = '<tr><td colspan="7" class="status-table-empty">先添加服务器，再进行批量巡检。</td></tr>';
    return;
  }

  if (!rows.length) {
    summary.innerHTML = '<span class="summary-chip neutral">尚未执行批量巡检</span>';
    tableBody.innerHTML = '<tr><td colspan="7" class="status-table-empty">点击“批量刷新状态”开始检查。</td></tr>';
    return;
  }

  const counts = {
    online: rows.filter((item) => item.status === "online").length,
    warning: rows.filter((item) => item.status === "warning").length,
    offline: rows.filter((item) => item.status === "offline").length,
    error: rows.filter((item) => item.status === "error").length,
  };

  summary.innerHTML = `
    <span class="summary-chip success">正常 ${counts.online}</span>
    <span class="summary-chip warning">告警 ${counts.warning}</span>
    <span class="summary-chip neutral">离线 ${counts.offline}</span>
    <span class="summary-chip danger">错误 ${counts.error}</span>
  `;

  tableBody.innerHTML = rows
    .map((item) => {
      const status = statusPresentation(item);
      const auth = authPresentation(item.auth_status);
      return `
        <tr>
          <td>
            <button type="button" class="status-board-link" data-open-server="${item.server_id}">
              <strong>${escapeHtml(item.server_name)}</strong>
              <span>${escapeHtml(item.host)}:${item.port}</span>
            </button>
          </td>
          <td><span class="health-badge ${status.css}">${status.label}</span></td>
          <td>${item.latency_ms == null ? "-" : `${item.latency_ms} ms`}</td>
          <td><span class="mini-badge ${auth.css}">${auth.label}</span></td>
          <td>${escapeHtml(item.disk_used_percent || "-")}<small>${escapeHtml(item.disk_available ? `可用 ${item.disk_available}` : "")}</small></td>
          <td>${escapeHtml(networkStatusText(item.network_status))}</td>
          <td class="issue-cell">${escapeHtml(statusIssueText(item))}</td>
        </tr>
      `;
    })
    .join("");

  tableBody.querySelectorAll("[data-open-server]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedServerId = Number(button.dataset.openServer);
      renderOverviewServerOptions();
      renderServerList();
      renderServerDetail();
    });
  });
}

function openServerModal(server = null) {
  fillServerForm(server);
  document.getElementById("server-modal").hidden = false;
}

function closeServerModal() {
  document.getElementById("server-modal").hidden = true;
  document.getElementById("server-form-status").textContent = "";
}

function fillServerForm(server) {
  serverForm.reset();
  document.getElementById("server-form-status").classList.remove("error");
  if (!server) {
    serverForm.elements.server_id.value = "";
    serverForm.elements.port.value = 22;
    serverForm.elements.notes.value = "";
    serverForm.elements.tags.value = "";
    setAuthType("password");
    document.getElementById("server-modal-title").textContent = "添加服务器";
    document.getElementById("delete-server-button").disabled = true;
    return;
  }

  serverForm.elements.server_id.value = server.id;
  serverForm.elements.name.value = server.name;
  serverForm.elements.host.value = server.host;
  serverForm.elements.port.value = server.port;
  serverForm.elements.username.value = server.username;
  serverForm.elements.notes.value = server.notes || "";
  serverForm.elements.tags.value = (server.tags || []).join(", ");
  serverForm.elements.password.value = "";
  serverForm.elements.private_key.value = "";
  serverForm.elements.private_key_passphrase.value = "";
  setAuthType(server.auth_type);
  document.getElementById("server-modal-title").textContent = `编辑 ${server.name}`;
  document.getElementById("delete-server-button").disabled = false;
}

function setAuthType(value) {
  serverForm.elements.auth_type.value = value;
  const isPassword = value === "password";
  document.getElementById("password-auth-fields").hidden = !isPassword;
  document.getElementById("key-auth-fields").hidden = isPassword;
  document.querySelectorAll(".auth-option").forEach((button) => {
    button.classList.toggle("active", button.dataset.authType === value);
  });
}

async function saveServer(event) {
  event.preventDefault();
  const formData = new FormData(serverForm);
  const serverId = formData.get("server_id");
  const payload = {
    name: formData.get("name"),
    host: formData.get("host"),
    port: Number(formData.get("port") || 22),
    username: formData.get("username"),
    auth_type: formData.get("auth_type"),
    password: formData.get("password"),
    private_key: formData.get("private_key"),
    private_key_passphrase: formData.get("private_key_passphrase"),
    notes: formData.get("notes") || "",
    tags: formData.get("tags") || "",
  };

  const statusNode = document.getElementById("server-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "正在保存...";
  try {
    const result = await requestJson(serverId ? `/api/servers/${serverId}` : "/api/servers", {
      method: serverId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.selectedServerId = result.id;
    statusNode.textContent = "保存成功";
    await fetchBootstrap();
    closeServerModal();
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function deleteSelectedServer() {
  const server = state.servers.find((item) => item.id === Number(serverForm.elements.server_id.value)) || activeServer();
  if (!server || !window.confirm(`确认删除“${server.name}”吗？`)) return;
  try {
    await requestJson(`/api/servers/${server.id}`, { method: "DELETE" });
    if (state.selectedServerId === server.id) {
      state.selectedServerId = null;
    }
    closeServerModal();
    await fetchBootstrap();
  } catch (error) {
    window.alert(error.message);
  }
}

async function probeSelectedServer() {
  const server = activeServer();
  if (!server) {
    window.alert("请先选择一台服务器");
    return;
  }
  const buttons = [probeButton, document.getElementById("detail-probe-button")].filter(Boolean);
  buttons.forEach((button) => {
    button.disabled = true;
    button.textContent = "巡检中...";
  });
  document.getElementById("selected-server-dot").className = "status-dot warning";
  try {
    const snapshot = await requestJson(`/api/servers/${server.id}/probe`, { method: "POST" });
    renderSnapshot(snapshot);
  } catch (error) {
    document.getElementById("selected-server-dot").className = "status-dot error";
    document.getElementById("selected-server-meta").textContent = error.message;
  } finally {
    buttons.forEach((button, index) => {
      button.disabled = false;
      button.textContent = index === 0 ? "立即巡检" : "实时巡检";
    });
  }
}

function setMetric(id, value, detail) {
  document.getElementById(`${id}-metric`).textContent = value;
  document.getElementById(`${id}-detail`).textContent = detail;
}

function renderSnapshot(snapshot) {
  document.getElementById("selected-server-dot").className = "status-dot online";
  document.getElementById("selected-server-name").textContent = snapshot.server_name;
  document.getElementById("selected-server-meta").textContent = `${snapshot.host}:${snapshot.port} · ${snapshot.username} · 在线`;
  setMetric("cpu", `${Number(snapshot.cpu.usage_percent || 0).toFixed(2)}%`, `负载 ${snapshot.cpu.load_average.join(" / ") || "-"}`);
  setMetric("memory", `${Number(snapshot.memory.used_percent || 0).toFixed(2)}%`, `${snapshot.memory.used_mb} / ${snapshot.memory.total_mb} MB`);
  setMetric("disk", snapshot.disk.used_percent || "-", `${snapshot.disk.used} / ${snapshot.disk.size} · ${snapshot.disk.mount}`);
  setMetric("network", networkStatusText(snapshot.network.status), `DNS ${snapshot.network.dns} · ${snapshot.network.default_route || "无默认路由"}`);
  document.getElementById("processor-model").textContent = snapshot.processor_model || "-";
  document.getElementById("hostname-value").textContent = snapshot.hostname || "-";
  document.getElementById("uptime-value").textContent = snapshot.uptime || "-";
  document.getElementById("os-value").textContent = snapshot.os_info || "-";
  document.getElementById("network-interfaces").textContent = snapshot.network.interfaces.join("\n") || "没有接口信息";
  document.getElementById("network-traffic").innerHTML = renderTrafficTable(snapshot.network.traffic || []);
}

function renderCommandList() {
  const container = document.getElementById("command-list");
  if (!state.commands.length) {
    container.innerHTML =
      '<div class="empty-state"><strong>还没有巡检命令</strong><span>点击“新增命令”创建第一条巡检命令。</span></div>';
    return;
  }

  container.innerHTML = state.commands
    .map((command) => {
      const active = command.id === state.selectedCommandId ? "active" : "";
      const applicableCount = command.applicable_servers?.length || 0;
      return `
        <button type="button" class="server-list-item ${active}" data-command-id="${command.id}">
          <span class="server-list-title">
            <strong>${escapeHtml(command.name)}</strong>
            <i>${applicableCount} 台</i>
          </span>
          <span class="server-address">${escapeHtml(command.description || "未填写用途说明")}</span>
          <span class="server-health-inline">
            <b class="mini-badge neutral">范围</b>
            <small>${escapeHtml(commandSummary(command))}</small>
          </span>
          <span class="tag-row">
            ${(command.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("") || "<span>无标签范围</span>"}
          </span>
        </button>
      `;
    })
    .join("");

  container.querySelectorAll("[data-command-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCommandId = Number(button.dataset.commandId);
      renderCommandList();
      renderCommandDetail();
    });
  });
}

function renderCommandDetail() {
  const empty = document.getElementById("command-detail-empty");
  const card = document.getElementById("command-detail-card");
  const editButton = document.getElementById("edit-command-button");
  const deleteButton = document.getElementById("delete-command-button");
  const runButton = document.getElementById("run-command-button");
  const command = activeCommand();

  if (!command) {
    empty.hidden = false;
    card.hidden = true;
    editButton.disabled = true;
    deleteButton.disabled = true;
    runButton.disabled = true;
    return;
  }

  empty.hidden = true;
  card.hidden = false;
  editButton.disabled = false;
  deleteButton.disabled = false;

  document.getElementById("command-detail-name").textContent = command.name;
  document.getElementById("command-detail-description").textContent = command.description || "未填写用途说明";
  document.getElementById("command-detail-command").textContent = command.command || "-";
  document.getElementById("command-detail-targets").innerHTML = command.scope_all_servers
    ? '<span class="token-chip">全部服务器</span>'
    : renderTokenList(command.server_names || [], "未直接指定服务器");
  document.getElementById("command-detail-tags").innerHTML = renderTokenList(command.tags || [], "未设置标签范围");
  document.getElementById("command-detail-applicable").innerHTML = (command.applicable_servers || []).length
    ? command.applicable_servers.map((server) => `<span class="token-chip">${escapeHtml(server.name)}</span>`).join("")
    : '<span class="token-chip muted">当前没有命中任何服务器</span>';

  const select = document.getElementById("command-run-server-select");
  const applicableServers = command.applicable_servers || [];
  if (!applicableServers.length) {
    select.innerHTML = '<option value="">没有可执行服务器</option>';
    select.disabled = true;
    runButton.disabled = true;
  } else {
    select.disabled = false;
    runButton.disabled = false;
    select.innerHTML = applicableServers
      .map((server) => `<option value="${server.id}">${escapeHtml(server.name)}</option>`)
      .join("");
  }
}

function renderTokenList(values, emptyText) {
  return values.length
    ? values.map((value) => `<span class="token-chip">${escapeHtml(value)}</span>`).join("")
    : `<span class="token-chip muted">${escapeHtml(emptyText)}</span>`;
}

function renderCommandTargetSelectors() {
  renderServerTargetSelectors([]);
  renderTagTargetSelectors([]);
}

function renderServerTargetSelectors(selectedServerIds) {
  const container = document.getElementById("command-server-targets");
  if (!state.servers.length) {
    container.innerHTML = '<div class="selector-empty">请先添加服务器</div>';
    return;
  }
  container.innerHTML = state.servers
    .map(
      (server) => `
        <label class="selector-option">
          <input type="checkbox" name="command_server_ids" value="${server.id}" ${selectedServerIds.includes(server.id) ? "checked" : ""} />
          <span>
            <strong>${escapeHtml(server.name)}</strong>
            <small>${escapeHtml(server.host)}:${server.port}</small>
          </span>
        </label>
      `,
    )
    .join("");
}

function renderTagTargetSelectors(selectedTags) {
  const container = document.getElementById("command-tag-targets");
  if (!state.serverTags.length) {
    container.innerHTML = '<div class="selector-empty">当前没有可选标签</div>';
    return;
  }
  const selectedSet = new Set(selectedTags.map((tag) => tag.toLowerCase()));
  container.innerHTML = state.serverTags
    .map(
      (tag) => `
        <label class="selector-option">
          <input type="checkbox" name="command_tag_names" value="${escapeHtml(tag)}" ${selectedSet.has(tag.toLowerCase()) ? "checked" : ""} />
          <span>
            <strong>${escapeHtml(tag)}</strong>
            <small>匹配该标签的全部服务器</small>
          </span>
        </label>
      `,
    )
    .join("");
}

function syncCommandScopeUi() {
  const checked = Boolean(commandForm?.elements.scope_all_servers?.checked);
  const serverPanel = document.getElementById("command-server-panel");
  const tagPanel = document.getElementById("command-tag-panel");
  if (serverPanel) {
    serverPanel.classList.toggle("disabled-panel", checked);
  }
  if (tagPanel) {
    tagPanel.classList.toggle("disabled-panel", checked);
  }
  document.querySelectorAll('input[name="command_server_ids"], input[name="command_tag_names"]').forEach((input) => {
    input.disabled = checked;
  });
  if (commandForm?.elements.tags) {
    commandForm.elements.tags.disabled = checked;
  }
}

function openCommandModal(command = null) {
  fillCommandForm(command);
  document.getElementById("command-modal").hidden = false;
}

function closeCommandModal() {
  document.getElementById("command-modal").hidden = true;
  document.getElementById("command-form-status").textContent = "";
}

function fillCommandForm(command) {
  commandForm.reset();
  document.getElementById("command-form-status").classList.remove("error");

  if (!command) {
    commandForm.elements.command_id.value = "";
    commandForm.elements.scope_all_servers.checked = false;
    document.getElementById("command-modal-title").textContent = "新增巡检命令";
    document.getElementById("delete-command-modal-button").disabled = true;
    renderServerTargetSelectors([]);
    renderTagTargetSelectors([]);
    syncCommandScopeUi();
    return;
  }

  commandForm.elements.command_id.value = command.id;
  commandForm.elements.name.value = command.name;
  commandForm.elements.description.value = command.description || "";
  commandForm.elements.command.value = command.command;
  commandForm.elements.scope_all_servers.checked = Boolean(command.scope_all_servers);

  const knownTagKeys = new Set(state.serverTags.map((tag) => tag.toLowerCase()));
  const manualTags = (command.tags || []).filter((tag) => !knownTagKeys.has(tag.toLowerCase()));
  commandForm.elements.tags.value = manualTags.join(", ");

  renderServerTargetSelectors(command.server_ids || []);
  renderTagTargetSelectors(command.tags || []);
  syncCommandScopeUi();
  document.getElementById("command-modal-title").textContent = `编辑 ${command.name}`;
  document.getElementById("delete-command-modal-button").disabled = false;
}

async function saveCommand(event) {
  event.preventDefault();
  const commandId = commandForm.elements.command_id.value;
  const selectedServerIds = Array.from(document.querySelectorAll('input[name="command_server_ids"]:checked'))
    .map((input) => Number(input.value))
    .filter((value) => Number.isFinite(value));
  const checkedTags = Array.from(document.querySelectorAll('input[name="command_tag_names"]:checked')).map((input) => input.value);
  const manualTags = String(commandForm.elements.tags.value || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
  const tagMap = new Map();
  [...checkedTags, ...manualTags].forEach((tag) => {
    const key = tag.toLowerCase();
    if (!tagMap.has(key)) {
      tagMap.set(key, tag);
    }
  });

  const payload = {
    name: commandForm.elements.name.value.trim(),
    description: commandForm.elements.description.value.trim(),
    command: commandForm.elements.command.value,
    scope_all_servers: commandForm.elements.scope_all_servers.checked,
    server_ids: selectedServerIds,
    tags: Array.from(tagMap.values()),
  };

  const statusNode = document.getElementById("command-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "正在保存...";

  try {
    const result = await requestJson(commandId ? `/api/commands/${commandId}` : "/api/commands", {
      method: commandId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.selectedCommandId = result.id;
    statusNode.textContent = "保存成功";
    await fetchBootstrap();
    closeCommandModal();
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function deleteSelectedCommand() {
  const command = state.commands.find((item) => item.id === Number(commandForm.elements.command_id.value)) || activeCommand();
  if (!command || !window.confirm(`确认删除命令“${command.name}”吗？`)) return;
  try {
    await requestJson(`/api/commands/${command.id}`, { method: "DELETE" });
    if (state.selectedCommandId === command.id) {
      state.selectedCommandId = null;
    }
    document.getElementById("command-output").textContent = "等待执行巡检命令";
    closeCommandModal();
    await fetchBootstrap();
  } catch (error) {
    window.alert(error.message);
  }
}

async function runSelectedCommand() {
  const command = activeCommand();
  if (!command) {
    window.alert("请先选择一条命令");
    return;
  }

  const select = document.getElementById("command-run-server-select");
  const serverId = Number(select.value);
  if (!serverId) {
    window.alert("请选择要执行的服务器");
    return;
  }

  const output = document.getElementById("command-output");
  const runButton = document.getElementById("run-command-button");
  output.textContent = "执行中...";
  runButton.disabled = true;
  try {
    const result = await requestJson(`/api/commands/${command.id}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_id: serverId }),
    });
    output.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    output.textContent = error.message;
  } finally {
    runButton.disabled = false;
  }
}

function bridgeStateInfo(config) {
  const status = config?.status || {};
  if (!config?.enabled) return { label: "已停用", css: "neutral", detail: "后台未启用小智桥接" };
  if (status.connected) return { label: "已连接", css: "success", detail: "小智可以调用本地 MCP 工具" };
  if (status.state === "connecting") return { label: "连接中", css: "warning", detail: "正在建立 WebSocket 连接" };
  if (status.state === "retrying") return { label: "重连中", css: "error", detail: status.last_error || "连接已断开，正在等待重试" };
  return { label: "未连接", css: "neutral", detail: status.last_error || "等待连接" };
}

function renderXiaozhi(config, fillForm = false) {
  state.xiaozhi = config;
  const info = bridgeStateInfo(config);
  const dot = document.getElementById("sidebar-xiaozhi-dot");
  dot.className = `status-dot ${info.css === "success" ? "online" : info.css === "error" ? "error" : info.css === "warning" ? "warning" : "muted"}`;
  document.getElementById("sidebar-xiaozhi-state").textContent = info.label;
  document.getElementById("overview-xiaozhi-state").textContent = info.label;
  document.getElementById("overview-xiaozhi-detail").textContent = info.detail;

  const badge = document.getElementById("settings-xiaozhi-badge");
  badge.textContent = info.label;
  badge.className = `state-badge ${info.css}`;

  const status = config?.status || {};
  document.getElementById("xiaozhi-detail-state").textContent = info.label;
  document.getElementById("xiaozhi-detail-endpoint").textContent = status.endpoint_url || config?.endpoint_base_url || "-";
  document.getElementById("xiaozhi-detail-connected").textContent = formatDateTime(status.last_connected_at);
  document.getElementById("xiaozhi-detail-attempt").textContent = formatDateTime(status.last_attempt_at);
  document.getElementById("xiaozhi-detail-delay").textContent = `${status.reconnect_delay_seconds || 5} 秒`;

  const errorNode = document.getElementById("xiaozhi-last-error");
  errorNode.hidden = !status.last_error;
  errorNode.textContent = status.last_error || "";

  if (fillForm) {
    xiaozhiForm.elements.enabled.checked = Boolean(config?.enabled);
    xiaozhiForm.elements.endpoint_base_url.value = config?.endpoint_base_url || "wss://api.xiaozhi.me/mcp/";
    xiaozhiForm.elements.token.value = "";
    document.getElementById("token-hint").textContent = config?.has_token
      ? `已保存：${config.token_masked}，留空表示保持不变`
      : "尚未保存 Token";
  }
}

function alertingStateInfo(config) {
  const status = config?.status || {};
  if (!config?.enabled) return { label: "已停用", css: "neutral", detail: "后台未启用主动告警" };
  if (status.state === "running") return { label: "运行中", css: "success", detail: "正在后台巡检并等待状态变化" };
  if (status.state === "error") return { label: "异常", css: "error", detail: status.last_error || "巡检过程中出现错误" };
  if (status.state === "stopped") return { label: "已停止", css: "neutral", detail: "后台任务已停止" };
  return { label: "等待中", css: "warning", detail: "正在等待下一次巡检" };
}

function renderAlerting(config, fillForm = false) {
  state.alerting = config;
  const info = alertingStateInfo(config);
  const badge = document.getElementById("settings-alert-badge");
  badge.textContent = info.label;
  badge.className = `state-badge ${info.css}`;

  const status = config?.status || {};
  document.getElementById("alert-detail-state").textContent = info.label;
  document.getElementById("alert-detail-interval").textContent = `${config?.interval_seconds || status.interval_seconds || 60} 秒`;
  document.getElementById("alert-detail-checked").textContent = formatDateTime(status.last_checked_at);
  document.getElementById("alert-detail-sent").textContent = formatDateTime(status.last_alert_at);
  document.getElementById("alert-detail-count").textContent = String(status.sent_alerts || 0);

  const errorNode = document.getElementById("alert-last-error");
  errorNode.hidden = !status.last_error;
  errorNode.textContent = status.last_error || "";

  if (fillForm) {
    alertForm.elements.enabled.checked = Boolean(config?.enabled);
    alertForm.elements.interval_seconds.value = String(config?.interval_seconds || 60);
    alertForm.elements.notify_offline.checked = Boolean(config?.notify_offline);
    alertForm.elements.notify_recovery.checked = Boolean(config?.notify_recovery);
  }
}

async function refreshXiaozhiStatus(fillForm = false) {
  try {
    const config = await requestJson("/api/integrations/xiaozhi");
    renderXiaozhi(config, fillForm);
  } catch (error) {
    document.getElementById("sidebar-xiaozhi-state").textContent = "状态读取失败";
  }
}

async function refreshAlertStatus(fillForm = false) {
  try {
    const config = await requestJson("/api/alerting");
    renderAlerting(config, fillForm);
  } catch (error) {
    document.getElementById("alert-detail-state").textContent = error.message;
  }
}

async function saveXiaozhiSettings(event) {
  event.preventDefault();
  const statusNode = document.getElementById("xiaozhi-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "正在保存并重连...";
  try {
    const tokenValue = xiaozhiForm.elements.token.value.trim();
    const config = await requestJson("/api/integrations/xiaozhi", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: xiaozhiForm.elements.enabled.checked,
        endpoint_base_url: xiaozhiForm.elements.endpoint_base_url.value.trim(),
        token: tokenValue || null,
      }),
    });
    renderXiaozhi(config, true);
    statusNode.textContent = "配置已保存，正在应用";
    window.setTimeout(() => refreshXiaozhiStatus(false), 1200);
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function reconnectXiaozhi() {
  const statusNode = document.getElementById("xiaozhi-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "已发起重连";
  try {
    await requestJson("/api/integrations/xiaozhi/reconnect", { method: "POST" });
    window.setTimeout(() => refreshXiaozhiStatus(false), 1000);
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function saveAlertSettings(event) {
  event.preventDefault();
  const statusNode = document.getElementById("alert-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "正在保存...";
  try {
    const config = await requestJson("/api/alerting", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: alertForm.elements.enabled.checked,
        interval_seconds: Number(alertForm.elements.interval_seconds.value || 60),
        notify_offline: alertForm.elements.notify_offline.checked,
        notify_recovery: alertForm.elements.notify_recovery.checked,
      }),
    });
    renderAlerting(config, true);
    statusNode.textContent = "告警设置已保存";
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function sendTestAlert() {
  const statusNode = document.getElementById("alert-form-status");
  statusNode.classList.remove("error");
  statusNode.textContent = "正在发送测试告警...";
  try {
    await requestJson("/api/alerting/test", { method: "POST" });
    statusNode.textContent = "测试告警已发出";
    window.setTimeout(() => refreshAlertStatus(false), 800);
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.classList.add("error");
  }
}

async function loadLogs() {
  const category = document.getElementById("log-category-filter").value;
  const level = document.getElementById("log-level-filter").value;
  const limit = document.getElementById("log-limit-filter").value;
  const params = new URLSearchParams({ limit });
  if (category) params.set("category", category);
  if (level) params.set("level", level);

  try {
    const logs = await requestJson(`/api/logs?${params.toString()}`);
    renderLogs(logs);
  } catch (error) {
    document.getElementById("log-summary").textContent = error.message;
  }
}

function renderLogs(logs) {
  const container = document.getElementById("log-list");
  const errorCount = logs.filter((item) => !item.success).length;
  document.getElementById("log-summary").textContent = `显示 ${logs.length} 条记录${errorCount ? `，其中 ${errorCount} 条错误` : "，全部正常"}`;
  if (!logs.length) {
    container.innerHTML =
      '<div class="empty-state"><strong>暂无日志</strong><span>小智桥接或 MCP 工具调用后会显示在这里。</span></div>';
    return;
  }
  container.innerHTML = logs.map(renderLogItem).join("");
}

function renderLogItem(item) {
  const duration = item.duration_ms == null ? "" : `${item.duration_ms} ms`;
  const request = item.request == null ? "无请求内容" : formatJson(item.request);
  const response = item.response == null ? "无返回内容" : formatJson(item.response);
  const direction = item.direction ? `<span>${escapeHtml(directionLabel(item.direction))}</span>` : "";
  return `
    <details class="activity-item ${item.success ? "success" : "failed"}">
      <summary>
        <span class="activity-status"></span>
        <span class="activity-type">${escapeHtml(categoryLabels[item.category] || item.category)}</span>
        <span class="activity-event">${escapeHtml(item.event)}</span>
        ${direction}
        ${item.request_id ? `<code>#${escapeHtml(item.request_id)}</code>` : ""}
        <span class="activity-spacer"></span>
        ${duration ? `<span>${duration}</span>` : ""}
        <time>${escapeHtml(formatDateTime(item.created_at))}</time>
      </summary>
      <div class="activity-detail">
        <section><div class="payload-head"><strong>请求</strong><span>${escapeHtml(item.source)}</span></div><pre>${request}</pre></section>
        <section><div class="payload-head"><strong>返回</strong><span>${item.success ? "成功" : "失败"}</span></div><pre>${response}</pre></section>
      </div>
    </details>
  `;
}

function directionLabel(value) {
  const labels = {
    request: "请求",
    response: "响应",
    request_response: "请求 / 返回",
    outbound: "出站",
  };
  return labels[value] || value;
}

async function clearLogs() {
  if (!window.confirm("确认清空全部日志吗？")) return;
  try {
    await requestJson("/api/logs", { method: "DELETE" });
    await loadLogs();
  } catch (error) {
    window.alert(error.message);
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

function statusIssueText(item) {
  const base = item.issue_message || "-";
  if (item.status !== "offline" || !item.offline_since) return base;
  const since = formatDateTimeCn(item.offline_since);
  const duration = formatDuration(item.offline_duration_seconds);
  const suffix = duration ? `离线始于 ${since}，已持续 ${duration}` : `离线始于 ${since}`;
  if (String(base).includes("离线始于")) return base;
  return `${base}（${suffix}）`;
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "";
  if (value < 60) return `${Math.max(0, Math.round(value))} 秒`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分钟`;
  const days = Math.floor(hours / 24);
  return `${days} 天 ${hours % 24} 小时`;
}

function networkStatusText(value) {
  if (value === "reachable") return "正常";
  if (value === "degraded") return "异常";
  return value || "未知";
}

function statusPresentation(item) {
  if (!item) return { label: "未检测", css: "neutral" };
  if (item.status === "online") return { label: "在线", css: "success" };
  if (item.status === "warning") return { label: "告警", css: "warning" };
  if (item.status === "offline") return { label: "离线", css: "neutral" };
  return { label: "错误", css: "danger" };
}

function authPresentation(value) {
  if (value === "ok") return { label: "通过", css: "success" };
  if (value === "auth_failed") return { label: "密码或密钥错误", css: "danger" };
  return { label: "未知", css: "neutral" };
}

function renderTrafficTable(rows) {
  if (!rows.length) return '<div class="empty-inline">暂无流量统计</div>';
  return `<table class="traffic-table"><thead><tr><th>接口</th><th>接收</th><th>发送</th></tr></thead><tbody>${rows
    .map((row) => `<tr><td>${escapeHtml(row.interface)}</td><td>${formatBytes(row.rx_bytes)}</td><td>${formatBytes(row.tx_bytes)}</td></tr>`)
    .join("")}</tbody></table>`;
}

function formatDateTimeCn(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const weekdays = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日${weekdays[date.getDay()]} ${String(date.getHours()).padStart(2, "0")}点${String(date.getMinutes()).padStart(2, "0")}分${String(date.getSeconds()).padStart(2, "0")}秒`;
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatBytes(value) {
  let bytes = Number(value || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unitIndex = 0;
  while (bytes >= 1024 && unitIndex < units.length - 1) {
    bytes /= 1024;
    unitIndex += 1;
  }
  return `${bytes.toFixed(unitIndex ? 1 : 0)} ${units[unitIndex]}`;
}

function formatJson(value) {
  return escapeHtml(JSON.stringify(value, null, 2));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function updateClock() {
  document.getElementById("topbar-time").textContent = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function closeModal(name) {
  if (name === "server") closeServerModal();
  if (name === "command") closeCommandModal();
}

document.querySelectorAll(".auth-option").forEach((button) => {
  button.addEventListener("click", () => setAuthType(button.dataset.authType));
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.dataset.closeModal));
});

document.getElementById("overview-server-select").addEventListener("change", (event) => {
  state.selectedServerId = Number(event.target.value);
  renderServerList();
  renderServerDetail();
});

document.getElementById("new-server-button").addEventListener("click", () => openServerModal(null));
document.getElementById("edit-server-button").addEventListener("click", () => {
  const server = activeServer();
  if (server) openServerModal(server);
});
document.getElementById("delete-server-button").addEventListener("click", deleteSelectedServer);
document.getElementById("refresh-server-health-button").addEventListener("click", loadServerHealthBoard);

document.getElementById("new-command-button").addEventListener("click", () => openCommandModal(null));
document.getElementById("edit-command-button").addEventListener("click", () => {
  const command = activeCommand();
  if (command) openCommandModal(command);
});
document.getElementById("delete-command-button").addEventListener("click", deleteSelectedCommand);
document.getElementById("delete-command-modal-button").addEventListener("click", deleteSelectedCommand);
document.getElementById("run-command-button").addEventListener("click", runSelectedCommand);
document.getElementById("command-scope-all").addEventListener("change", syncCommandScopeUi);

document.getElementById("detail-probe-button").addEventListener("click", probeSelectedServer);
document.getElementById("logout-button").addEventListener("click", logout);
document.getElementById("refresh-logs-button").addEventListener("click", loadLogs);
document.getElementById("clear-logs-button").addEventListener("click", clearLogs);
document.getElementById("log-category-filter").addEventListener("change", loadLogs);
document.getElementById("log-level-filter").addEventListener("change", loadLogs);
document.getElementById("log-limit-filter").addEventListener("change", loadLogs);
document.getElementById("reconnect-xiaozhi-button").addEventListener("click", reconnectXiaozhi);
document.getElementById("send-test-alert-button").addEventListener("click", sendTestAlert);
document.getElementById("toggle-token-button").addEventListener("click", (event) => {
  const input = xiaozhiForm.elements.token;
  input.type = input.type === "password" ? "text" : "password";
  event.currentTarget.textContent = input.type === "password" ? "显示" : "隐藏";
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeServerModal();
  closeCommandModal();
});

probeButton.addEventListener("click", probeSelectedServer);
serverForm.addEventListener("submit", saveServer);
commandForm.addEventListener("submit", saveCommand);
xiaozhiForm.addEventListener("submit", saveXiaozhiSettings);
alertForm.addEventListener("submit", saveAlertSettings);

initializeView();
updateClock();
window.setInterval(updateClock, 1000);
window.setInterval(() => refreshXiaozhiStatus(false), 5000);
window.setInterval(() => {
  if (state.currentView === "settings") {
    refreshAlertStatus(false);
  }
}, 5000);
window.setInterval(() => {
  if (state.currentView === "logs" && document.getElementById("auto-refresh-logs").checked) {
    loadLogs();
  }
}, 3000);
fetchBootstrap().catch((error) => window.alert(error.message));
