// 浏览器只保存当前选中任务和视图状态；任务事实以服务端
// web_job_status.json 和正式 artifacts 为准，刷新页面不会改变实验。
const state = {
  currentJobId: null,
  pollTimer: null,
  deepseekStatus: null,
  lastRenderedStatus: null,
  previewArtifactName: null,
  autoPreviewKey: null,
  activeView: "overview",
  conversationJobId: null,
  traceJobId: null,
  renderedMainTraceIds: new Set(),
  eventJobId: null,
  renderedAuditEventIds: new Set(),
  timelineJobId: null,
  renderedTimelineIds: new Set(),
  mainTimelineHeaderRendered: false,
  codingTimelineHeaders: new Set(),
  currentJobStatus: null,
  renderedInterventionKey: null,
  resources: [],
  resourceCategory: "skill",
  selectedResourceId: null,
  resourceCatalogLoaded: false,
};
const DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9";
const DEFAULT_CHAT_PLACEHOLDER = "例如：载入示例、检查配置、启动任务、查看历史任务";
const INTERVENTION_CHAT_PLACEHOLDER = "输入你希望下一轮重点解决的不足或算法方向";
const DEFAULT_CHAT_ACTIONS = [
  {label: "载入示例", command: "载入示例"},
  {label: "历史任务", command: "历史任务"},
  {label: "启动", command: "启动"},
];
const WAITING_CHAT_ACTIONS = [
  {label: "采用建议", command: "采用 Main 建议"},
  {label: "刷新状态", command: "刷新"},
  {label: "历史任务", command: "历史任务"},
];

const $ = (id) => document.getElementById(id);

const VIEW_TITLES = {
  overview: "FJSP 求解质量优化",
  context: "Context Packet",
  worker: "过程审计明细",
  experiments: "实验监督",
  versions: "版本记录",
  resources: "知识库 / Skills",
  models: "模型分配",
  setup: "任务配置",
};

// ---------------------------------------------------------------------------
// 页面导航与本地文件镜像
// ---------------------------------------------------------------------------

function setActiveView(view) {
  const targetView = VIEW_TITLES[view] ? view : "overview";
  state.activeView = targetView;
  document.querySelectorAll(".workspace-view").forEach((item) => {
    item.classList.toggle("active", item.id === `view-${targetView}`);
  });
  document.querySelectorAll("[data-view-target]").forEach((item) => {
    item.classList.toggle("active", item.dataset.viewTarget === targetView);
  });
  const title = $("workspace-title");
  if (title) title.textContent = VIEW_TITLES[targetView];
  if (targetView === "resources" && !state.resourceCatalogLoaded) {
    loadResourceCatalog().catch(() => {});
  }
}

async function readFileToTextarea(fileInput, textarea) {
  const file = fileInput.files?.[0];
  if (!file) return;
  textarea.value = await file.text();
  textarea.dataset.filename = file.name;
}

function setupFileMirror(inputId, textId) {
  const fileInput = $(inputId);
  const textarea = $(textId);
  fileInput.addEventListener("change", () => readFileToTextarea(fileInput, textarea));
}

async function loadDemo(options = {}) {
  const response = await fetch("/api/examples");
  const demo = await response.json();
  $("title").value = demo.config.title;
  $("requirement-text").value = demo.requirement.text;
  $("requirement-text").dataset.filename = demo.requirement.name;
  $("io-text").value = demo.io.text;
  $("io-text").dataset.filename = demo.io.name;
  $("instance-text").value = demo.instance.text;
  $("instance-text").dataset.filename = demo.instance.name;
  $("best-text").value = demo.best_known_csv.text;
  $("best-text").dataset.filename = demo.best_known_csv.name;
  $("max-rounds").value = demo.config.max_rounds;
  $("seeds").value = demo.config.seeds;
  $("max-workers").value = demo.config.max_workers || 2;
  $("timeout-seconds").value = demo.config.timeout_seconds;
  $("worker-max-steps").value = demo.config.worker_max_steps;
  $("worker-max-runtime-seconds").value = demo.config.worker_max_runtime_seconds;
  $("in-round-repair-attempts").value = demo.config.in_round_repair_attempts;
  $("main-max-subagents").value = demo.config.main_max_subagents ?? 4;
  $("max-competing-workers").value = demo.config.max_competing_workers ?? 4;
  $("promotion-repeats").value = demo.config.promotion_repeats;
  $("pause-between-rounds").checked = demo.config.pause_between_rounds !== false;
  $("artifact-preview").textContent = "标准 FJSP DP18a 默认测试已载入，可以直接启动循环迭代。";
  updateContractSummary();
  if (!options.silent) {
    appendChatMessage("assistant", "标准 FJSP DP18a 默认测试已载入：Main Agent 规划方向，OpenCode Coding Agent 自写 solver，固定 Core 评测。可以直接“启动”。");
    setActiveView("setup");
  }
}

// ---------------------------------------------------------------------------
// 运行环境与历史任务
// ---------------------------------------------------------------------------

async function loadDeepSeekStatus() {
  const response = await fetch("/api/deepseek-status");
  const status = await response.json();
  state.deepseekStatus = status;
  setAgentControlsFromConfig(status);
  renderModelProviderStatus(status);
  const badge = $("deepseek-status");
  if (status.configured) {
    badge.innerHTML = `
      <strong>DeepSeek API：已配置</strong>
      <span>${escapeHtml(status.model)} · ${escapeHtml(status.base_url)}</span>
      <small>${escapeHtml(status.diagnosis || "密钥已加载，界面不会展示密钥内容。")}</small>
    `;
    badge.className = "api-panel ready";
  } else {
    badge.innerHTML = renderDeepSeekHelp(status);
    badge.className = "api-panel missing";
  }
}

// ---------------------------------------------------------------------------
// Skills / 知识库浏览：后端只返回两个白名单目录中的文本资源。
// ---------------------------------------------------------------------------

async function loadResourceCatalog(options = {}) {
  if (state.resourceCatalogLoaded && !options.force) {
    renderResourceList();
    return;
  }
  $("resource-count").textContent = "读取中...";
  $("resource-list").innerHTML = '<div class="resource-list-empty">正在读取项目资源...</div>';
  const response = await fetch("/api/resources");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "资源目录读取失败");
  }
  state.resources = Array.isArray(payload.resources) ? payload.resources : [];
  state.resourceCatalogLoaded = true;
  renderResourceList();
}

function setResourceCategory(category) {
  state.resourceCategory = category === "knowledge" ? "knowledge" : "skill";
  document.querySelectorAll("[data-resource-category]").forEach((button) => {
    const active = button.dataset.resourceCategory === state.resourceCategory;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  renderResourceList();
}

function renderResourceList() {
  const list = $("resource-list");
  const query = $("resource-search").value.trim().toLocaleLowerCase();
  const resources = state.resources.filter((item) => {
    if (item.category !== state.resourceCategory) return false;
    if (!query) return true;
    return `${item.title || ""} ${item.path || ""} ${item.description || ""}`
      .toLocaleLowerCase()
      .includes(query);
  });
  const categoryTotal = state.resources.filter((item) => item.category === state.resourceCategory).length;
  $("resource-count").textContent = query
    ? `${resources.length} / ${categoryTotal} 项`
    : `${categoryTotal} 项`;
  list.innerHTML = "";
  if (!resources.length) {
    list.innerHTML = '<div class="resource-list-empty">没有匹配的资源。</div>';
    return;
  }
  for (const resource of resources) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `resource-list-item${resource.id === state.selectedResourceId ? " active" : ""}`;
    button.dataset.resourceId = resource.id;
    button.innerHTML = `
      <strong>${escapeHtml(resource.title || resource.path)}</strong>
      <span>${escapeHtml(resource.path || "-")}</span>
      ${resource.description ? `<small>${escapeHtml(truncateText(resource.description, 120))}</small>` : ""}
    `;
    button.addEventListener("click", () => selectResource(resource.id));
    list.appendChild(button);
  }
  if (!resources.some((item) => item.id === state.selectedResourceId)) {
    selectResource(resources[0].id).catch(() => {});
  }
}

async function selectResource(resourceId) {
  state.selectedResourceId = resourceId;
  document.querySelectorAll("[data-resource-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.resourceId === resourceId);
  });
  const summary = state.resources.find((item) => item.id === resourceId);
  $("resource-preview-title").textContent = summary?.title || "读取资源";
  $("resource-preview-path").textContent = summary?.path || "-";
  $("resource-preview-meta").textContent = summary
    ? `${String(summary.format || "text").toUpperCase()} · ${formatFileSize(summary.size)}`
    : "-";
  $("resource-preview-content").textContent = "正在读取内容...";
  const response = await fetch(`/api/resources/content?id=${encodeURIComponent(resourceId)}`);
  const payload = await response.json();
  if (state.selectedResourceId !== resourceId) return;
  if (!response.ok) {
    $("resource-preview-content").textContent = payload.error || "资源内容读取失败";
    return;
  }
  $("resource-preview-title").textContent = payload.title || payload.path;
  $("resource-preview-path").textContent = payload.path || "-";
  $("resource-preview-meta").textContent = `${String(payload.format || "text").toUpperCase()} · ${formatFileSize(payload.size)}${payload.truncated ? " · 已截断" : ""}`;
  $("resource-preview-content").textContent = payload.content || "（空文件）";
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 1024) return `${Math.max(0, bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function setModelSelectValue(select, model) {
  if (!select || !model) return;
  if (![...select.options].some((option) => option.value === model)) {
    select.add(new Option(model, model));
  }
  select.value = model;
}

function selectedProvider(model) {
  return String(model || "").trim().split("/", 1)[0].toLowerCase();
}

function selectedAgentModel(role) {
  return $(`${role}-model-setup`)?.value.trim() || $(`${role}-model`)?.value.trim() || "";
}

function selectedAgentVariant(role) {
  return $(`${role}-variant-setup`)?.value.trim() || $(`${role}-variant`)?.value.trim() || "";
}

function syncAgentControl(sourceId, targetId) {
  const source = $(sourceId);
  const target = $(targetId);
  if (source && target) target.value = source.value.trim();
  if (state.deepseekStatus) renderModelProviderStatus(state.deepseekStatus);
}

function setAgentControlsFromConfig(config) {
  const fallbackModel = config.opencode_model || "deepseek/deepseek-v4-pro";
  const values = {
    "main-agent-model": config.main_agent_model || fallbackModel,
    "main-agent-variant": config.main_agent_variant || "",
    "coding-worker-model": config.coding_worker_model || fallbackModel,
    "coding-worker-variant": config.coding_worker_variant || "",
  };
  Object.entries(values).forEach(([baseId, value]) => {
    const primary = $(baseId);
    const setup = $(`${baseId}-setup`);
    if (baseId.endsWith("-model")) {
      setModelSelectValue(primary, value);
      setModelSelectValue(setup, value);
    } else {
      if (primary) primary.value = value;
      if (setup) setup.value = value;
    }
  });
}

function renderModelProviderStatus(status) {
  const target = $("model-provider-status");
  if (!target) return;
  const roles = [
    ["Main", selectedAgentModel("main-agent")],
    ["Worker", selectedAgentModel("coding-worker")],
  ];
  const missing = roles.filter(([, model]) => !status.provider_keys?.[selectedProvider(model)]);
  target.textContent = roles
    .map(([label, model]) => `${label} ${model || "未选择"} / ${selectedAgentVariant(label === "Main" ? "main-agent" : "coding-worker") || "默认"}`)
    .join(" · ");
  target.className = missing.length ? "provider-missing" : "provider-ready";
}

function renderDeepSeekHelp(status) {
  const checked = (status.checked_env_files || [])
    .map((item) => `<li>${item.exists ? "已找到" : "未找到"}：${escapeHtml(shortPath(item.path))}</li>`)
    .join("");
  const examples = (status.help?.examples || [])
    .map((item) => `<code>${escapeHtml(item)}</code>`)
    .join("");
  return `
    <strong>DeepSeek API：未配置</strong>
    <span>${escapeHtml(status.diagnosis || "没有检测到本地密钥。")}</span>
    <details>
      <summary>查看配置方法</summary>
      <p>推荐在仓库根目录新建 <code>.env</code> 或 <code>.env.local</code>，也可以用 <code>DEEPSEEK_API_KEY_FILE</code> 指向私有密钥文件。</p>
      <div class="api-code-list">${examples}</div>
      <ul>${checked}</ul>
      <p>${escapeHtml(status.env_example?.note || ".env.example 只是模板，不会被加载。")}</p>
    </details>
  `;
}

function shortPath(path) {
  const text = String(path || "");
  if (text.length <= 72) return text;
  return `...${text.slice(-69)}`;
}

async function loadJobHistory() {
  const response = await fetch("/api/jobs");
  const payload = await response.json();
  const jobs = payload.jobs || [];
  renderJobHistory(jobs);
  return jobs;
}

function renderJobHistory(jobs) {
  const container = $("history-list");
  if (!container) return;
  container.innerHTML = "";
  if (!jobs.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "暂无历史任务";
    container.appendChild(empty);
    return;
  }
  for (const job of jobs.slice(0, 12)) {
    const item = document.createElement("article");
    item.className = `history-item ${job.id === state.currentJobId ? "active" : ""}`;
    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "history-select";
    const summary = job.summary?.worker_summary || {};
    const makespan = summary.final_makespan ?? summary.best_makespan_so_far ?? job.summary?.last_summary?.best_metrics?.makespan;
    const diagnosticMakespan = summary.diagnostic_makespan ?? summary.latest_diagnostic_makespan;
    const metricText = makespan !== undefined && makespan !== null
      ? `makespan ${escapeHtml(formatMetric(makespan))}`
      : diagnosticMakespan !== undefined && diagnosticMakespan !== null
        ? `诊断 makespan ${escapeHtml(formatMetric(diagnosticMakespan))}`
        : "makespan -";
    selectButton.innerHTML = `
      <strong>${escapeHtml(job.title || job.id)}</strong>
      <span>${escapeHtml(statusLabel(job.status))} · ${escapeHtml(formatShortTime(job.updated_at || job.created_at))}</span>
      <small>${metricText}</small>
    `;
    selectButton.addEventListener("click", () => selectHistoryJob(job.id, {loadReport: true}));
    item.appendChild(selectButton);
    if (["queued", "running", "waiting_for_user", "stopping"].includes(job.status)) {
      const stopButton = document.createElement("button");
      stopButton.type = "button";
      stopButton.className = "history-stop-button";
      stopButton.title = `停止任务：${job.title || job.id}`;
      stopButton.textContent = job.status === "stopping" ? "停止中" : "停止";
      stopButton.disabled = job.status === "stopping";
      stopButton.addEventListener("click", () => stopJob(job.id, {
        button: stopButton,
        status: job.status,
      }));
      item.appendChild(stopButton);
    }
    container.appendChild(item);
  }
}

async function selectHistoryJob(jobId, options = {}) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  const job = await response.json();
  if (!response.ok) {
    $("artifact-preview").textContent = job.error || "历史任务读取失败";
    return;
  }
  state.currentJobId = job.id;
  state.lastRenderedStatus = null;
  if (options.loadReport) {
    state.previewArtifactName = null;
    state.autoPreviewKey = null;
  }
  renderJob(job);
  await loadJobHistory();
  if (isActiveJobStatus(job.status)) {
    startPolling();
  } else {
    await handleTerminalJob(job);
  }
}

// ---------------------------------------------------------------------------
// 任务提交：前端只提交文档、算例和资源预算，不提交具体算法选择。
// ---------------------------------------------------------------------------

function formatShortTime(value) {
  const text = String(value || "");
  if (!text) return "-";
  return text.replace("T", " ").replace("Z", "").slice(0, 16);
}

function buildPayload() {
  return {
    title: $("title").value,
    requirement: {
      name: $("requirement-text").dataset.filename || "requirement.md",
      text: $("requirement-text").value,
    },
    io: {
      name: $("io-text").dataset.filename || "io_spec.md",
      text: $("io-text").value,
    },
    instance: {
      name: $("instance-text").dataset.filename || "instance.fjs",
      text: $("instance-text").value,
    },
    best_known_csv: {
      name: $("best-text").dataset.filename || "best_known.csv",
      text: $("best-text").value,
    },
    max_rounds: Number($("max-rounds").value || 2),
    seeds: $("seeds").value || DEFAULT_STANDARD_SEEDS,
    max_workers: Number($("max-workers").value || 1),
    coding_backend: "opencode",
    main_agent_model: selectedAgentModel("main-agent"),
    main_agent_variant: selectedAgentVariant("main-agent"),
    coding_worker_model: selectedAgentModel("coding-worker"),
    coding_worker_variant: selectedAgentVariant("coding-worker"),
    main_max_subagents: Number($("main-max-subagents").value ?? 4),
    max_competing_workers: Number($("max-competing-workers").value || 4),
    timeout_seconds: Number($("timeout-seconds").value || 60),
    worker_max_steps: Number($("worker-max-steps").value || 4),
    worker_max_runtime_seconds: Number($("worker-max-runtime-seconds").value || 120),
    in_round_repair_attempts: Number($("in-round-repair-attempts").value || 0),
    promotion_repeats: Number($("promotion-repeats").value || 1),
    pause_between_rounds: $("pause-between-rounds").checked,
  };
}

function updateContractSummary() {
  const target = $("edit-scope-summary");
  if (!target) return;
  target.textContent = "Agent 自写 solver · 固定 Core 评测 · 自动选择方法知识";
}

async function submitJob(event) {
  event.preventDefault();
  await submitCurrentJob();
}

async function submitCurrentJob() {
  const payload = buildPayload();
  if (!payload.requirement.text.trim() || !payload.io.text.trim() || !payload.instance.text.trim()) {
    $("artifact-preview").textContent = "请先提供需求文档、IO 文档和算例。";
    appendChatMessage("assistant", "还缺需求文档、IO 文档或算例。把内容粘到配置区，或先说“载入示例”。");
    return;
  }
  const roleModels = [
    ["Main Agent", payload.main_agent_model],
    ["Coding Worker", payload.coding_worker_model],
  ];
  const missingProviders = roleModels.filter(([, model]) => {
    const provider = selectedProvider(model);
    return !state.deepseekStatus?.provider_keys?.[provider];
  });
  if (state.deepseekStatus && missingProviders.length) {
    const missingText = missingProviders
      .map(([role, model]) => `${role}: ${model || "未配置"}`)
      .join("；");
    $("artifact-preview").textContent =
      `以下角色缺少对应 provider API key：${missingText}`;
    appendChatMessage("assistant", `模型配置未就绪：${missingText}`);
    return;
  }
  $("artifact-preview").textContent = "任务已提交，等待后端启动...";
  appendChatMessage("assistant", "任务已提交，统一对话区会持续汇总 Main Agent 和可审计运行事件。");
  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const job = await response.json();
  if (!response.ok) {
    $("artifact-preview").textContent = `提交失败：${job.error || response.statusText}`;
    appendChatMessage("assistant", `提交失败：${job.error || response.statusText}`);
    return;
  }
  state.currentJobId = job.id;
  state.lastRenderedStatus = null;
  state.previewArtifactName = null;
  renderJob(job);
  await loadJobHistory();
  $("artifact-preview").textContent =
    "任务已启动，统一对话区会持续刷新；完成后会自动载入报告预览。";
  showUnifiedConversation({scrollThread: true});
  startPolling();
}

// ---------------------------------------------------------------------------
// 对话式操作入口：当前负责把自然操作意图映射为载入、启动、查看等 UI 命令。
// 真正的 Main Agent 方向规划发生在后端任务闭环中。
// ---------------------------------------------------------------------------

function initializeChat() {
  resetConversationState(null);
  renderChatActions(DEFAULT_CHAT_ACTIONS);
}

function resetConversationState(jobId) {
  $("chat-thread").innerHTML = "";
  state.conversationJobId = jobId;
  state.traceJobId = null;
  state.renderedMainTraceIds = new Set();
  state.eventJobId = null;
  state.renderedAuditEventIds = new Set();
  state.timelineJobId = null;
  state.renderedTimelineIds = new Set();
  state.mainTimelineHeaderRendered = false;
  state.codingTimelineHeaders = new Set();
  state.renderedInterventionKey = null;
  setChatInputPlaceholder(DEFAULT_CHAT_PLACEHOLDER);
}

function appendChatMessage(role, text, options = {}) {
  const thread = $("chat-thread");
  if (!thread) return;
  const item = document.createElement("div");
  item.className = `chat-message ${role}${options.className ? ` ${options.className}` : ""}`;
  if (options.label) {
    const label = document.createElement("strong");
    label.textContent = options.label;
    item.appendChild(label);
  }
  const content = document.createElement("span");
  content.textContent = text;
  item.appendChild(content);
  thread.appendChild(item);
  thread.scrollTop = thread.scrollHeight;
}

function renderMainAgentTrace(job) {
  const trace = Array.isArray(job.main_agent_trace) ? job.main_agent_trace : [];
  if (state.traceJobId !== job.id) {
    state.traceJobId = job.id;
    state.renderedMainTraceIds = new Set();
    if (trace.length) {
      appendMainAgentRunHeader(job);
    }
  }
  const labels = {
    commentary: "Main Agent 思考过程",
    analysis: "Main Agent 思考摘要（兜底）",
    tool: "工具 / Subagent",
    usage: "模型用量",
    final: "Main 最终结论",
  };
  for (const record of trace) {
    const recordId = String(record.id || `${record.attempt}:${record.timestamp}:${record.kind}:${record.text}`);
    if (state.renderedMainTraceIds.has(recordId)) continue;
    state.renderedMainTraceIds.add(recordId);
    appendMainAgentTraceItem(record, labels[record.kind] || "Main Agent");
  }
}

function appendMainAgentRunHeader(job) {
  const thread = $("chat-thread");
  if (!thread) return;
  const header = document.createElement("div");
  header.className = "main-agent-run";
  const model = job.config?.main_agent_model || job.config?.opencode_model || "-";
  const variant = job.config?.main_agent_variant || "模型默认";
  header.innerHTML = `
    <span class="agent-avatar" aria-hidden="true">A</span>
    <div>
      <strong>优化 Agent</strong>
      <code>${escapeHtml(job.id || "run")}</code>
      <small>${escapeHtml(model)} · ${escapeHtml(variant)}</small>
    </div>
  `;
  thread.appendChild(header);
}

function appendMainAgentTraceItem(record, label) {
  const thread = $("chat-thread");
  if (!thread) return;
  const item = document.createElement("article");
  item.className = `main-trace-item trace-${record.kind || "commentary"}`;
  const head = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = `${record.stage || label} · ${record.attempt || "当前轮"}`;
  const timestamp = document.createElement("time");
  timestamp.textContent = formatTraceTime(record.timestamp);
  head.append(title, timestamp);
  const content = document.createElement("p");
  content.textContent = record.text || "-";
  item.append(head, content);
  thread.appendChild(item);
  thread.scrollTop = thread.scrollHeight;
}

function appendCodingAgentRunHeader(record) {
  const thread = $("chat-thread");
  if (!thread) return;
  const header = document.createElement("div");
  const colorIndex = stableAgentColorIndex(record.agent_key || record.display_name || record.attempt || "worker");
  header.className = `main-agent-run coding-agent-run agent-color-${colorIndex}`;
  const model = record.model || "模型未知";
  const variant = record.variant || "模型默认";
  header.innerHTML = `
    <span class="agent-avatar" aria-hidden="true">C${colorIndex + 1}</span>
    <div>
      <strong>Coding Agent · ${escapeHtml(record.display_name || record.candidate_id || "worker")}</strong>
      <code>${escapeHtml(record.round || record.attempt || "当前轮")}</code>
      <small>${escapeHtml(model)} · ${escapeHtml(variant)}</small>
    </div>
  `;
  thread.appendChild(header);
}

function appendCodingAgentTraceItem(record) {
  const thread = $("chat-thread");
  if (!thread) return;
  const labels = {
    commentary: "Coding Agent 公开思考",
    tool: "Coding Agent 工具阶段",
    usage: "Coding Agent 模型用量",
    final: "Coding Agent 完成报告",
  };
  const colorIndex = stableAgentColorIndex(record.agent_key || record.display_name || record.attempt || "worker");
  const item = document.createElement("article");
  item.className = `main-trace-item coding-trace-item trace-${record.kind || "commentary"} agent-color-${colorIndex}`;
  const head = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = `${labels[record.kind] || "Coding Agent"} · ${record.display_name || record.attempt || "worker"}`;
  const timestamp = document.createElement("time");
  timestamp.textContent = formatTraceTime(record.timestamp);
  head.append(title, timestamp);
  const content = document.createElement("p");
  content.textContent = record.text || "-";
  item.append(head, content);
  thread.appendChild(item);
  thread.scrollTop = thread.scrollHeight;
}

function stableAgentColorIndex(value) {
  let hash = 0;
  for (const char of String(value || "")) hash = ((hash * 31) + char.charCodeAt(0)) >>> 0;
  return hash % 4;
}

function renderWorkerAuditEvents(job) {
  const events = Array.isArray(job.events) ? job.events : [];
  if (state.eventJobId !== job.id) {
    state.eventJobId = job.id;
    state.renderedAuditEventIds = new Set();
  }
  for (const event of events) {
    const eventId = `${job.id}:${event.time || ""}:${event.level || "info"}:${event.message || ""}`;
    if (state.renderedAuditEventIds.has(eventId)) continue;
    state.renderedAuditEventIds.add(eventId);
    appendAuditEventItem(event);
  }
}

function appendAuditEventItem(event) {
  const thread = $("chat-thread");
  if (!thread) return;
  const meta = auditEventMeta(event);
  const item = document.createElement("article");
  item.className = `audit-event-item level-${safeClass(event.level || "info")} kind-${safeClass(meta.key)}`;
  item.innerHTML = `
    <header>
      <span class="audit-badge tone-${safeClass(meta.tone)}">${escapeHtml(meta.label)}</span>
      <time>${escapeHtml(event.time || "")}</time>
    </header>
    <p>${escapeHtml(event.message || "-")}</p>
  `;
  thread.appendChild(item);
  thread.scrollTop = thread.scrollHeight;
}

function renderUnifiedTimeline(job) {
  if (state.timelineJobId !== job.id) {
    state.timelineJobId = job.id;
    state.renderedTimelineIds = new Set();
    state.mainTimelineHeaderRendered = false;
    state.codingTimelineHeaders = new Set();
  }
  const entries = [
    ...(Array.isArray(job.main_agent_trace) ? job.main_agent_trace : []).map((record) => ({
      type: "main",
      id: `main:${record.id || `${record.attempt}:${record.timestamp}:${record.kind}:${record.text}`}`,
      timestamp: Number(record.timestamp) || 0,
      value: record,
    })),
    ...(Array.isArray(job.coding_agent_trace) ? job.coding_agent_trace : []).map((record) => ({
      type: "coding",
      id: `coding:${record.id || `${record.agent_key}:${record.timestamp}:${record.kind}:${record.text}`}`,
      timestamp: Number(record.timestamp) || 0,
      value: record,
    })),
    ...(Array.isArray(job.events) ? job.events : []).map((event) => ({
      type: "audit",
      id: `audit:${event.time || ""}:${event.level || "info"}:${event.message || ""}`,
      timestamp: Date.parse(event.time || "") || 0,
      value: event,
    })),
  ].sort((left, right) => left.timestamp - right.timestamp || left.id.localeCompare(right.id));
  const labels = {
    commentary: "Main Agent 思考过程",
    analysis: "Main Agent 思考摘要（兜底）",
    tool: "工具 / Subagent",
    usage: "模型用量",
    final: "Main 最终结论",
  };
  for (const entry of entries) {
    if (state.renderedTimelineIds.has(entry.id)) continue;
    state.renderedTimelineIds.add(entry.id);
    if (entry.type === "main") {
      if (!state.mainTimelineHeaderRendered) {
        appendMainAgentRunHeader(job);
        state.mainTimelineHeaderRendered = true;
      }
      appendMainAgentTraceItem(entry.value, labels[entry.value.kind] || "Main Agent");
    } else if (entry.type === "coding") {
      const agentKey = entry.value.agent_key || entry.value.attempt || "worker";
      if (!state.codingTimelineHeaders.has(agentKey)) {
        appendCodingAgentRunHeader(entry.value);
        state.codingTimelineHeaders.add(agentKey);
      }
      appendCodingAgentTraceItem(entry.value);
    } else {
      appendAuditEventItem(entry.value);
    }
  }
}

function auditEventMeta(event) {
  const level = String(event.level || "info").toLowerCase();
  const message = String(event.message || "").toLowerCase();
  if (message.includes("候选预检") || message.includes("agentic")) {
    return {key: "preflight", label: "候选预检", tone: level === "error" ? "danger" : "warning"};
  }
  if (/\bpromoted\b|已晋升|晋升成功|严格提升/.test(message)) {
    return {key: "promotion", label: "Promotion", tone: "success"};
  }
  if (/\brolled_back\b|已回滚|执行回滚|回滚完成/.test(message)) {
    return {key: "rollback", label: "Rollback", tone: "danger"};
  }
  if (message.includes("repair") || message.includes("修补")) {
    return {key: "repair", label: "修补", tone: level === "error" ? "danger" : "warning"};
  }
  if (message.includes("evaluator") || message.includes("core") || message.includes("benchmark") || message.includes("基线")) {
    return {key: "core", label: "Core", tone: level === "error" ? "danger" : "info"};
  }
  if (message.includes("main agent") || message.includes("planningpacket") || message.includes("assignment") || message.includes("上下文包")) {
    return {key: "planner", label: "Main / Planner", tone: "info"};
  }
  return {key: "worker", label: "阶段状态", tone: level === "error" ? "danger" : level === "warning" ? "warning" : "info"};
}

function formatTraceTime(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return "";
  return new Date(numeric).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
}

function renderPendingIntervention(job) {
  const pending = job.pending_intervention;
  if (!pending || pending.status !== "waiting") return;
  const key = `${job.id}:${pending.next_round_index}:${pending.requested_at}`;
  if (state.renderedInterventionKey === key) return;
  state.renderedInterventionKey = key;
  setChatInputPlaceholder(INTERVENTION_CHAT_PLACEHOLDER);
  const analysis = pending.main_analysis || {};
  const assessment = analysis.incumbent_assessment || {};
  const mutation = analysis.next_mutation || {};
  const thread = $("chat-thread");
  const card = document.createElement("section");
  card.className = "round-intervention-card";
  card.innerHTML = `
    <header>
      <div>
        <span>轮间人工门控</span>
        <strong>第 ${Number(pending.completed_round_index) + 1} 轮已完成</strong>
      </div>
      <small>下一轮：${Number(pending.next_round_index) + 1}</small>
    </header>
    <h3>${escapeHtml(analysis.title || "Main Agent 下一轮建议")}</h3>
    ${renderReasoningTrace(analysis.reasoning_trace)}
    ${analysis.diagnosis ? `<p><b>诊断：</b>${escapeHtml(analysis.diagnosis)}</p>` : ""}
    ${renderAnalysisList("已验证能力", assessment.verified_capabilities)}
    ${renderAnalysisList("实现限制", assessment.implementation_limits)}
    ${renderAnalysisList("瓶颈假设", assessment.bottleneck_hypotheses)}
    ${renderAnalysisList("审计证据", assessment.evidence_refs)}
    ${renderAnalysisList("仍未知", assessment.unknowns)}
    ${renderAnalysisList("当前不足", analysis.observed_shortcomings)}
    ${renderAnalysisList("判断证据", analysis.evidence_summary)}
    ${analysis.direction_judgment ? `<p><b>方向判断：</b>${escapeHtml(analysis.direction_judgment)}</p>` : ""}
    ${analysis.selection_rationale ? `<p><b>选择理由：</b>${escapeHtml(analysis.selection_rationale)}</p>` : ""}
    ${mutation.change ? `<p><b>下一次变异：</b>${escapeHtml(mutation.change)}</p>` : ""}
    ${renderAnalysisList("目标符号", mutation.target_symbols)}
    ${mutation.expected_effect ? `<p><b>预期作用：</b>${escapeHtml(mutation.expected_effect)}</p>` : ""}
    ${renderAnalysisList("证伪指标", mutation.falsification_metrics)}
    ${renderAnalysisList("未选方向", analysis.alternatives_considered)}
    ${renderAnalysisList("验收条件", analysis.acceptance_checks)}
    <footer>
      <button type="button" class="primary" data-intervention-action="accept">采用 Main 建议</button>
      <button type="button" class="ghost" data-intervention-action="custom">输入指定方向</button>
    </footer>
  `;
  thread.appendChild(card);
  card.querySelector('[data-intervention-action="accept"]').addEventListener("click", async () => {
    appendChatMessage("user", "采用 Main Agent 建议，继续下一轮。");
    await submitRoundIntervention({useMainRecommendation: true});
  });
  card.querySelector('[data-intervention-action="custom"]').addEventListener("click", () => {
    setChatInputPlaceholder(INTERVENTION_CHAT_PLACEHOLDER);
    $("chat-input")?.focus();
  });
  thread.scrollTop = thread.scrollHeight;
  showUnifiedConversation({scrollThread: true});
}

function renderAnalysisList(title, values) {
  const items = Array.isArray(values) ? values.filter(Boolean) : [];
  if (!items.length) return "";
  return `<div class="analysis-list"><b>${escapeHtml(title)}：</b><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
}

function renderReasoningTrace(values) {
  const entries = Array.isArray(values) ? values.filter((item) => item && item.summary) : [];
  if (!entries.length) return "";
  return `
    <div class="reasoning-journal">
      <b>研究过程：</b>
      <ol>
        ${entries.map((entry) => `
          <li>
            <strong>${escapeHtml(entry.stage || "分析")}</strong>
            <p>${escapeHtml(entry.summary || "")}</p>
            ${renderAnalysisList("证据", entry.evidence)}
            ${entry.inference ? `<p><b>判断：</b>${escapeHtml(entry.inference)}</p>` : ""}
            ${entry.decision ? `<p><b>决定：</b>${escapeHtml(entry.decision)}</p>` : ""}
            ${entry.next_check ? `<p><b>下一项验证：</b>${escapeHtml(entry.next_check)}</p>` : ""}
          </li>
        `).join("")}
      </ol>
    </div>
  `;
}

async function submitRoundIntervention({direction = "", useMainRecommendation = false} = {}) {
  if (!state.currentJobId) return;
  const response = await fetch(`/api/jobs/${encodeURIComponent(state.currentJobId)}/continue`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      direction,
      use_main_recommendation: useMainRecommendation,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    appendChatMessage("assistant", `无法继续下一轮：${payload.error || response.statusText}`);
    return;
  }
  appendChatMessage(
    "assistant",
    useMainRecommendation
      ? "已采用 Main Agent 建议，Coding Worker 即将按任务书开始下一轮。"
      : "已收到你的方向。Main Agent 正在结合硬约束重新整理下一轮任务书。",
  );
  state.currentJobStatus = "running";
  state.renderedInterventionKey = null;
  setChatInputPlaceholder(DEFAULT_CHAT_PLACEHOLDER);
  renderChatActions(DEFAULT_CHAT_ACTIONS);
  showUnifiedConversation({scrollThread: true});
  startPolling();
}

function setChatInputPlaceholder(value) {
  const input = $("chat-input");
  if (!input) return;
  input.placeholder = value || DEFAULT_CHAT_PLACEHOLDER;
}

function showUnifiedConversation(options = {}) {
  setActiveView("overview");
  const thread = $("chat-thread");
  if (!thread) return;
  if (options.scrollThread) {
    thread.scrollTop = thread.scrollHeight;
  }
  thread.scrollIntoView({behavior: options.behavior || "smooth", block: "nearest"});
}

function renderChatActions(actions) {
  const container = $("chat-actions");
  if (!container) return;
  container.innerHTML = "";
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-chip";
    button.textContent = action.label;
    button.addEventListener("click", () => handleChatCommand(action.command));
    container.appendChild(button);
  }
}

async function handleChatSubmit(event) {
  event.preventDefault();
  const input = $("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendChatMessage("user", message);
  await handleChatCommand(message, {echo: false});
}

async function handleChatCommand(message, options = {}) {
  if (options.echo !== false) {
    appendChatMessage("user", message);
  }
  const normalized = message.trim().toLowerCase();
  if (state.currentJobStatus === "waiting_for_user") {
    if (["采用建议", "采用 main 建议", "继续", "continue"].some((token) => normalized.includes(token))) {
      await submitRoundIntervention({useMainRecommendation: true});
      return;
    }
    if (!["刷新", "状态", "status"].some((token) => normalized.includes(token))) {
      await submitRoundIntervention({direction: message});
      return;
    }
  }
  if (["载入示例", "示例", "demo", "load demo"].some((token) => normalized.includes(token))) {
    await loadDemo();
    return;
  }
  if (["启动", "start", "run", "开始"].some((token) => normalized.includes(token))) {
    await submitCurrentJob();
    return;
  }
  if (["刷新", "状态", "status"].some((token) => normalized.includes(token))) {
    await refreshJob();
    await loadJobHistory();
    appendChatMessage("assistant", state.currentJobId ? "状态已刷新。" : "当前还没有运行任务。");
    return;
  }
  if (["历史", "历史任务", "history"].some((token) => normalized.includes(token))) {
    setActiveView("versions");
    await loadJobHistory();
    $("history-list").scrollIntoView({behavior: "smooth", block: "nearest"});
    appendChatMessage("assistant", "历史任务已刷新，可以在右侧点击任意一次运行查看报告。");
    return;
  }
  if (["参数", "配置", "config"].some((token) => normalized.includes(token))) {
    setActiveView("setup");
    $("job-form").scrollIntoView({behavior: "smooth", block: "start"});
    appendChatMessage("assistant", "配置区已经定位到左侧下方。");
    return;
  }
  appendChatMessage("assistant", "我可以处理：载入示例、历史任务、参数配置、启动和刷新。算法方向由 Main Agent 自动决定。");
}

// ---------------------------------------------------------------------------
// 状态轮询：终态后自动加载正式报告，运行中只展示服务端已持久化的事件。
// ---------------------------------------------------------------------------

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(refreshJob, 1800);
  refreshJob();
}

function isActiveJobStatus(status) {
  return ["queued", "running", "waiting_for_user", "stopping"].includes(status);
}

async function stopCurrentJob() {
  if (!state.currentJobId) return;
  await stopJob(state.currentJobId, {
    button: $("stop-job"),
    status: state.currentJobStatus,
  });
}

async function stopJob(jobId, options = {}) {
  if (!jobId || !["queued", "running", "waiting_for_user"].includes(options.status)) return;
  if (!window.confirm("停止这个任务？已完成的轮次、产物和 incumbent 会保留。")) return;
  const button = options.button;
  if (button) {
    button.disabled = true;
    button.textContent = "停止中";
  }
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/stop`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: "{}",
  });
  const payload = await response.json();
  if (!response.ok) {
    if (button) {
      button.disabled = false;
      button.textContent = button.id === "stop-job" ? "停止任务" : "停止";
    }
    appendChatMessage("assistant", `停止失败：${payload.error || response.statusText}`);
    return;
  }
  if (jobId === state.currentJobId) {
    state.currentJobStatus = payload.status || "stopping";
    startPolling();
  }
  appendChatMessage("assistant", `已请求停止任务 ${jobId}，正在终止 Main、Coding Worker 和 Core 进程。`);
  await loadJobHistory();
}

async function refreshJob() {
  if (!state.currentJobId) return;
  const response = await fetch(`/api/jobs/${state.currentJobId}`);
  const job = await response.json();
  if (response.ok) {
    renderJob(job);
    await handleTerminalJob(job);
  }
}

async function handleTerminalJob(job) {
  if (isActiveJobStatus(job.status)) return;
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  const preferredReport = preferredArtifactName(job);
  const autoPreviewKey = `${job.id}:${job.updated_at || job.status}:${preferredReport || "none"}`;
  const previewIsTransient = !state.previewArtifactName || state.previewArtifactName === "status";
  if (preferredReport && previewIsTransient && state.autoPreviewKey !== autoPreviewKey) {
    state.autoPreviewKey = autoPreviewKey;
    await loadArtifact(preferredReport);
  }
}

function renderJob(job) {
  if (state.conversationJobId !== job.id) {
    resetConversationState(job.id);
  }
  state.currentJobStatus = job.status;
  const workspaceStatus = $("workspace-status");
  if (workspaceStatus) {
    workspaceStatus.textContent = statusLabel(job.status);
    workspaceStatus.className = `status-pill ${job.status}`;
  }
  const inspectorTitle = $("inspector-title");
  if (inspectorTitle) {
    inspectorTitle.textContent = job.title || "Agent 自写 solver 能力提升";
  }
  $("empty-state").classList.add("hidden");
  $("job-view").classList.remove("hidden");
  $("job-title").textContent = job.title;
  $("job-status").textContent = statusLabel(job.status);
  $("job-status").className = `status-pill ${job.status}`;
  const stopButton = $("stop-job");
  const stoppable = ["queued", "running", "waiting_for_user"].includes(job.status);
  stopButton.classList.toggle("hidden", !stoppable && job.status !== "stopping");
  stopButton.disabled = job.status === "stopping";
  stopButton.textContent = job.status === "stopping" ? "正在停止" : "停止任务";
  if (job.status !== state.lastRenderedStatus) {
    state.lastRenderedStatus = job.status;
    appendChatMessage("assistant", `任务状态：${statusLabel(job.status)}`);
  }
  renderChatActions(job.status === "waiting_for_user" ? WAITING_CHAT_ACTIONS : DEFAULT_CHAT_ACTIONS);
  renderUnifiedTimeline(job);
  renderPendingIntervention(job);

  const summary = job.summary?.last_summary || {};
  const benchmark = job.summary?.benchmark_summary || {};
  const roundSummary = job.summary?.round_summary || {};
  const workerSummary = job.summary?.worker_summary || {};
  const ziSummary = job.summary?.zi_summary || {};
  const officialMakespan =
    workerSummary.final_makespan ??
    workerSummary.best_makespan_so_far ??
    workerSummary.latest_makespan ??
    summary.best_metrics?.makespan ??
    summary.best_candidate_metrics?.avg_makespan;
  const diagnosticMakespan = workerSummary.diagnostic_makespan ?? workerSummary.latest_diagnostic_makespan;
  const makespan = officialMakespan ?? diagnosticMakespan;
  const diagnosticOnly = (officialMakespan === undefined || officialMakespan === null) && diagnosticMakespan !== undefined && diagnosticMakespan !== null;
  const gap =
    ziSummary.best_avg_gap_pct ??
    workerSummary.final_gap_pct ??
    workerSummary.best_gap_pct_so_far ??
    workerSummary.latest_gap_pct ??
    benchmark.gap_metrics?.avg_gap_pct ??
    summary.best_metrics?.avg_gap_pct;
  $("metric-rounds").textContent =
    ziSummary.completed_round_count ??
    roundSummary.completed_round_count ??
    job.config?.max_rounds ??
    "-";
  $("metric-valid").textContent =
    ziSummary.best_valid_instance_count !== undefined
      ? `${ziSummary.best_valid_instance_count}/${ziSummary.selected_instance_count}`
      : diagnosticOnly
        ? `诊断 ${workerSummary.diagnostic_valid ?? "-"}/${workerSummary.diagnostic_total ?? "-"} · 不参与晋升`
      : workerSummary.promoted_rounds !== undefined
        ? [
            `最终 ${workerSummary.final_valid ?? workerSummary.latest_valid ?? "-"}/${workerSummary.final_total ?? workerSummary.latest_total ?? "-"}`,
            `提升 ${workerSummary.promoted_rounds}/${workerSummary.round_count}`,
            workerRepairText(workerSummary),
          ].filter(Boolean).join(" · ")
        : workerSummary.best_valid_so_far !== undefined || workerSummary.latest_valid !== undefined
        ? [
            workerSummary.best_valid_so_far !== undefined
              ? `当前最好 ${formatMetric(workerSummary.best_makespan_so_far)}`
              : null,
            workerSummary.latest_valid !== undefined
              ? `最新 ${workerSummary.latest_valid}/${workerSummary.latest_total ?? "-"}`
              : null,
            workerRepairText(workerSummary),
            workerSummary.promoted_rounds !== undefined && workerSummary.round_count !== undefined
              ? `提升 ${workerSummary.promoted_rounds}/${workerSummary.round_count}`
              : null,
          ].filter(Boolean).join(" · ")
        : summary.valid ?? benchmark.valid_experiments ?? "-";
  $("metric-makespan").textContent = diagnosticOnly ? `${formatMetric(makespan)}（诊断）` : formatMetric(makespan);
  $("metric-gap").textContent = gap === undefined || gap === null ? "-" : `${Number(gap).toFixed(2)}%`;

  const log = $("event-log");
  log.innerHTML = "";
  for (const event of job.events || []) {
    const meta = auditEventMeta(event);
    const item = document.createElement("li");
    item.className = event.level || "info";
    item.innerHTML = `
      <strong>${escapeHtml(event.time || "")}</strong>
      <span class="event-label">${escapeHtml(meta.label)}</span>
      <span>${escapeHtml(event.message || "-")}</span>
    `;
    log.appendChild(item);
  }

  const artifactList = $("artifact-list");
  artifactList.innerHTML = "";
  const artifacts = {
    status: "任务状态 JSON",
    ...(job.artifacts || {}),
  };
  for (const [name, labelOrPath] of Object.entries(artifacts)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "artifact-button";
    button.textContent = name === "status" ? "任务状态 JSON" : labelForArtifact(name, labelOrPath);
    button.addEventListener("click", () => loadArtifact(name));
    artifactList.appendChild(button);
  }
  loadAndRenderInsights(job).catch(() => {});
}

// ---------------------------------------------------------------------------
// 证据视图：把 Context Packet、方向图、Core 结果和经验分层渲染成面板。
// 这里只做展示，不会在浏览器端重新计算 promotion 或合法性。
// ---------------------------------------------------------------------------

async function loadAndRenderInsights(job) {
  if (!job?.id) return;
  const response = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/insights`);
  if (!response.ok) return;
  const insights = await response.json();
  if (state.currentJobId && state.currentJobId !== job.id) return;
  renderInsights(insights, job);
}

function renderInsights(insights, job) {
  renderContextInsight(insights.context || {});
  renderWorkerInsight(insights.worker || {});
  renderExperimentInsight(insights.experiments || {});
  renderKnowledgeInsight(insights.knowledge || {});
  renderInspectorInsight(insights, job);
}

function renderContextInsight(context) {
  setText("context-packet-hash", compactHash(context.packet_hash));
  setText("context-contract-hash", compactHash(context.contract_hash));
  setText(
    "context-selected-count",
    `${context.selected_source_count ?? 0} 类 · 文档 ${context.document_count ?? 0} · 知识 ${context.knowledge_card_count ?? 0}`,
  );
  setText("context-excluded-count", `${context.excluded_source_count ?? 0} 项`);
  const list = $("context-source-list");
  if (!list) return;
  const sources = Array.isArray(context.sources) ? context.sources : [];
  list.innerHTML = "";
  if (!sources.length) {
    list.innerHTML = `<article><span class="source-state excluded">待生成</span><strong>Context Packet</strong><p>运行后展示来源链。</p></article>`;
    return;
  }
  for (const source of sources) {
    const article = document.createElement("article");
    const state = source.state === "excluded" ? "excluded" : "selected";
    article.innerHTML = `
      <span class="source-state ${state}">${state === "selected" ? "已选择" : "已排除"}</span>
      <strong>${escapeHtml(source.title || "Context Source")}</strong>
      <p>${escapeHtml(source.detail || "-")}</p>
    `;
    list.appendChild(article);
  }
  for (const hint of context.diagnostics?.direction_hints || []) {
    const article = document.createElement("article");
    article.innerHTML = `
      <span class="source-state selected">诊断</span>
      <strong>方向提示</strong>
      <p>${escapeHtml(hint)}</p>
    `;
    list.appendChild(article);
  }
}

function renderWorkerInsight(worker) {
  const rounds = Array.isArray(worker.rounds) ? worker.rounds : [];
  const roundList = $("worker-round-list");
  if (roundList) {
    roundList.innerHTML = "";
    if (!rounds.length) {
      roundList.innerHTML = "<li class=\"active\"><strong>待运行</strong><span>暂无回合</span></li>";
    } else {
      rounds.forEach((round, index) => {
        const item = document.createElement("li");
        item.className = index === rounds.length - 1 ? "active" : "";
        item.innerHTML = `
          <strong>${String(round.round_index + 1).padStart(2, "0")}</strong>
          <span>${escapeHtml(truncateText(round.title || "-", 34))}<br>${workerDecisionLabel(round.decision)} · ${formatMetric(round.makespan)}</span>
        `;
        roundList.appendChild(item);
      });
    }
  }

  const detail = $("worker-direction-list");
  if (!detail) return;
  detail.innerHTML = "";
  if (!rounds.length) {
    detail.innerHTML = "<div class=\"knowledge-empty\">运行后这里会展示每轮提出的方向、修补次数、候选预检/Core 结果和证据来源。</div>";
    return;
  }
  for (const round of rounds) {
    const card = document.createElement("article");
    card.className = `direction-card ${safeClass(round.status)} ${safeClass(round.decision)}`;
    const failures = (round.failure_signatures || []).map((item) => `<span class="tag danger">${escapeHtml(item)}</span>`).join("");
    const evidence = (round.evidence_used || []).slice(0, 2).map((item) => `<span class="tag">${escapeHtml(truncateText(item, 72))}</span>`).join("");
    const assignments = (round.worker_assignments || []).map((item) => `<span class="tag">${escapeHtml(item.assignment_id || "assignment")}</span>`).join("");
    const implementation = (round.implementation_order || []).slice(0, 4).map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("");
    const assessment = round.incumbent_assessment || {};
    const mutation = round.next_mutation || {};
    const competition = round.competition || {};
    const competitionCandidates = (competition.candidates || []).map((candidate) => {
      const gate = candidate.eligible
        ? "候选可晋升"
        : !candidate.ja_accepted
          ? "预检拦截"
          : !candidate.core_eligible
            ? "Core 无效"
            : candidate.status || "未完成";
      const objective = Array.isArray(candidate.objective_key) && candidate.objective_key.length
        ? candidate.objective_key.map((value) => formatMetric(value)).join(" / ")
        : "-";
      const selected = candidate.candidate_id === competition.selected_candidate_id;
      return `<span class="tag ${candidate.eligible ? "success" : "warning"}">${selected ? "胜出 " : ""}${escapeHtml(candidate.candidate_id || "candidate")} · ${escapeHtml(gate)} · ${escapeHtml(objective)}</span>`;
    }).join("");
    card.innerHTML = `
      <header>
        <div>
          <span class="section-kicker">第 ${round.round_index + 1} 轮 · ${escapeHtml(round.strategy_type || "-")}</span>
          <h3>${escapeHtml(round.title || "-")}</h3>
        </div>
        <span class="tag ${round.decision === "promoted" ? "success" : round.status === "no_improvement" ? "warning" : "danger"}">${workerDecisionLabel(round.decision)}</span>
      </header>
      <p>${escapeHtml(truncateText(round.strategy_intent || "没有记录策略说明。", 220))}</p>
      ${round.diagnosis ? `<p><strong>诊断：</strong>${escapeHtml(truncateText(round.diagnosis, 220))}</p>` : ""}
      ${renderAnalysisList("实现限制", (assessment.implementation_limits || []).slice(0, 3))}
      ${renderAnalysisList("瓶颈假设", (assessment.bottleneck_hypotheses || []).slice(0, 2))}
      ${mutation.change ? `<p><strong>本轮变异：</strong>${escapeHtml(truncateText(mutation.change, 220))}</p>` : ""}
      ${round.selection_rationale ? `<p><strong>选择理由：</strong>${escapeHtml(truncateText(round.selection_rationale, 220))}</p>` : ""}
      <div class="direction-meta">
        <span class="tag">尝试 ${round.attempt_count ?? 0}</span>
        <span class="tag">valid ${round.valid ?? 0}/${round.total ?? 0}</span>
        <span class="tag">makespan ${formatMetric(round.makespan)}</span>
        ${round.gap_pct === null || round.gap_pct === undefined ? "" : `<span class="tag">gap ${formatPct(round.gap_pct)}</span>`}
        ${failures}
        ${evidence}
        ${implementation}
        ${assignments}
      </div>
      ${competition.candidate_count ? `
        <div class="direction-meta competition-meta">
          <span class="tag">竞争候选 ${competition.eligible_candidate_count ?? 0}/${competition.candidate_count}</span>
          ${competitionCandidates}
        </div>
      ` : ""}
    `;
    detail.appendChild(card);
  }
}

function renderExperimentInsight(experiments) {
  setText("experiment-baseline", formatMetric(experiments.baseline_makespan));
  setText("experiment-best", formatMetric(experiments.best_makespan ?? experiments.final_makespan));
  const finalTotal = experiments.final_total ?? 0;
  const finalValid = experiments.final_valid ?? 0;
  setText("experiment-valid-rate", finalTotal ? `${finalValid}/${finalTotal}` : "-");
  setText("experiment-promotions", `${experiments.promoted_rounds ?? 0}/${experiments.round_count ?? 0}`);
  const trend = $("experiment-trend");
  if (!trend) return;
  const points = Array.isArray(experiments.trend) ? experiments.trend : [];
  trend.innerHTML = "";
  trend.className = "trend-placeholder trend-chart";
  const finiteMakespans = points.map((item) => Number(item.makespan)).filter((value) => Number.isFinite(value));
  const max = finiteMakespans.length ? Math.max(...finiteMakespans) : 0;
  const min = finiteMakespans.length ? Math.min(...finiteMakespans) : 0;
  if (!points.length) {
    trend.innerHTML = "<span>运行后展示趋势</span>";
    return;
  }
  for (const point of points) {
    const makespan = Number(point.makespan);
    const valid = Number(point.valid || 0);
    const denominator = Math.max(1, max - min);
    const height = Number.isFinite(makespan) ? 52 + ((max - makespan) / denominator) * 128 : 32;
    const bar = document.createElement("div");
    bar.className = `trend-bar ${safeClass(point.decision)} ${!valid && point.decision !== "baseline" ? "invalid" : ""}`;
    bar.innerHTML = `
      <span style="--bar-height: ${Math.round(height)}px">${escapeHtml(formatMetric(point.makespan))}</span>
      <strong>${escapeHtml(point.label || "-")}</strong>
    `;
    trend.appendChild(bar);
  }
}

function renderKnowledgeInsight(knowledge) {
  const ledger = $("knowledge-ledger");
  if (!ledger) return;
  ledger.innerHTML = "";
  const summary = document.createElement("article");
  summary.className = "knowledge-item";
  const usageSummary = knowledge.skill_usage_summary || {};
  summary.innerHTML = `
    <header>
      <div>
        <span class="section-kicker">经验分层沉淀</span>
        <h3>候选经验 ${knowledge.lesson_count ?? 0} · 已验证 ${knowledge.validated_lesson_count ?? 0} · 使用记录 ${knowledge.skill_usage_record_count ?? 0}</h3>
      </div>
    </header>
    <p>${escapeHtml(knowledge.purpose || "运行后按层沉淀经验。")}</p>
    <div class="knowledge-meta">
      <span class="tag">promotion 关联 ${usageSummary.promoted_usage_count ?? 0}</span>
      <span class="tag">record ${usageSummary.record_count ?? 0}</span>
    </div>
  `;
  ledger.appendChild(summary);
  const lessons = Array.isArray(knowledge.lessons) ? knowledge.lessons : [];
  if (!lessons.length) {
    const empty = document.createElement("div");
    empty.className = "knowledge-empty";
    empty.textContent = "还没有可展示的经验条目。";
    ledger.appendChild(empty);
    return;
  }
  for (const lesson of lessons) {
    const item = document.createElement("article");
    const negative = /failure|no_improvement|not_promoted|infeasible/i.test(`${lesson.lesson_type} ${lesson.outcome}`);
    item.className = `knowledge-item ${negative ? "negative" : ""}`;
    item.innerHTML = `
      <header>
        <div>
          <span class="section-kicker">${escapeHtml(lesson.lesson_type || "-")}</span>
          <h3>${escapeHtml(lesson.strategy || "-")}</h3>
        </div>
        <span class="tag ${negative ? "warning" : "success"}">${escapeHtml(lesson.outcome || "-")}</span>
      </header>
      <p>${escapeHtml(lesson.recommended_skill_update || "暂无 skill 更新建议。")}</p>
      <div class="knowledge-meta"><span class="tag">confidence ${escapeHtml(lesson.confidence || "-")}</span></div>
    `;
    ledger.appendChild(item);
  }
}

function renderInspectorInsight(insights, job) {
  const experiments = insights.experiments || {};
  const context = insights.context || {};
  const worker = insights.worker || {};
  setText("inspector-validator", `${experiments.final_valid ?? 0}/${experiments.final_total ?? 0}`);
  setText(
    "inspector-benchmark",
    `${formatMetric(experiments.best_makespan ?? experiments.final_makespan)}${experiments.final_gap_pct === null || experiments.final_gap_pct === undefined ? "" : ` · ${formatPct(experiments.final_gap_pct)}`}`,
  );
  setText("inspector-context", `${context.selected_source_count ?? 0} 类 · ${compactHash(context.packet_hash)}`);
  setText("inspector-artifacts", `${Object.keys(job.artifacts || {}).length} 个 · 方向 ${worker.direction_count ?? 0}`);
}

// ---------------------------------------------------------------------------
// 产物预览与纯显示辅助函数
// ---------------------------------------------------------------------------

function workerRepairText(workerSummary) {
  const repair = workerSummary.in_round_repair || {};
  const parts = [];
  if (workerSummary.direction_count !== undefined) {
    parts.push(`方向 ${workerSummary.direction_count}`);
  }
  if (
    workerSummary.attempt_count !== undefined &&
    workerSummary.attempt_count !== workerSummary.direction_count
  ) {
    parts.push(`尝试 ${workerSummary.attempt_count}`);
  }
  if (repair.repair_attempt_count) {
    parts.push(`修补 ${repair.recovered_round_count || 0}/${repair.repair_round_count || 0}`);
  }
  if (workerSummary.candidate_lesson_count) {
    parts.push(`经验 ${workerSummary.candidate_lesson_count}`);
  }
  if (workerSummary.skill_usage_record_count) {
    parts.push(`知识 ${workerSummary.skill_usage_record_count}`);
  }
  if (workerSummary.rejected_before_eval !== undefined) {
    parts.push(`预检未修复 ${workerSummary.rejected_before_eval}`);
  }
  return parts.join(" · ") || null;
}

function labelForArtifact(name) {
  const labels = {
    manifest: "运行清单",
    report: "演示报告",
    hypothesis_graph: "假设图谱",
    hypothesis_graph_report: "假设图谱报告",
    experience_memory: "经验记忆",
    experience_memory_report: "经验报告",
    skill_usage_records: "知识使用记录",
    exception: "异常追踪",
  };
  return labels[name] || name;
}

async function loadArtifact(name) {
  if (!state.currentJobId) return;
  const response = await fetch(`/api/jobs/${state.currentJobId}/artifact?name=${encodeURIComponent(name)}`);
  const payload = await response.json();
  if (!response.ok) {
    $("artifact-preview").textContent = payload.error || "读取产物失败";
    return;
  }
  state.previewArtifactName = name;
  $("artifact-path").textContent = payload.path;
  $("artifact-preview").textContent = payload.text + (payload.truncated ? "\n\n[内容过长，已截断预览]" : "");
}

function preferredArtifactName(job) {
  const artifacts = job.artifacts || {};
  if (artifacts.report) return "report";
  if (artifacts.loop_report) return "loop_report";
  return null;
}

function formatMetric(value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  return Number.isInteger(num) ? String(num) : num.toFixed(2);
}

function formatPct(value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(2)}%`;
}

function setText(id, value) {
  const element = $(id);
  if (!element) return;
  element.textContent = value === undefined || value === null || value === "" ? "-" : String(value);
}

function compactHash(value) {
  const text = String(value || "");
  if (!text || text === "-") return "-";
  return text.length <= 14 ? text : `${text.slice(0, 10)}...`;
}

function truncateText(value, maxLength) {
  const text = String(value || "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
}

function safeClass(value) {
  return String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
}

function workerDecisionLabel(value) {
  const labels = {
    promoted: "已提升",
    rolled_back: "已回滚",
    baseline: "基线",
    skipped: "跳过",
    unknown: "未知",
  };
  return labels[value] || value || "-";
}

function statusLabel(status) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    waiting_for_user: "等待用户指定下一轮",
    completed: "已完成",
    completed_with_warnings: "完成但有警告",
    interrupted: "已中断",
    stopping: "正在停止",
    stopped: "已停止",
    failed: "失败",
  };
  return labels[status] || status || "-";
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ---------------------------------------------------------------------------
// 页面启动：集中绑定事件，随后加载环境状态、示例和最近任务。
// ---------------------------------------------------------------------------

setupFileMirror("requirement-file", "requirement-text");
setupFileMirror("io-file", "io-text");
setupFileMirror("instance-file", "instance-text");
setupFileMirror("best-file", "best-text");
$("load-demo").addEventListener("click", loadDemo);
$("job-form").addEventListener("submit", submitJob);
$("refresh").addEventListener("click", refreshJob);
$("refresh-history").addEventListener("click", loadJobHistory);
$("refresh-resources").addEventListener("click", () => loadResourceCatalog({force: true}).catch((error) => {
  $("resource-count").textContent = "读取失败";
  $("resource-list").innerHTML = `<div class="resource-list-empty">${escapeHtml(error.message)}</div>`;
}));
$("resource-search").addEventListener("input", renderResourceList);
document.querySelectorAll("[data-resource-category]").forEach((button) => {
  button.addEventListener("click", () => setResourceCategory(button.dataset.resourceCategory));
});
$("stop-job").addEventListener("click", stopCurrentJob);
$("chat-form").addEventListener("submit", handleChatSubmit);
$("reset-chat").addEventListener("click", initializeChat);
[
  ["main-agent-model", "main-agent-model-setup"],
  ["main-agent-variant", "main-agent-variant-setup"],
  ["coding-worker-model", "coding-worker-model-setup"],
  ["coding-worker-variant", "coding-worker-variant-setup"],
].forEach(([primaryId, setupId]) => {
  $(primaryId).addEventListener("change", () => syncAgentControl(primaryId, setupId));
  $(setupId).addEventListener("change", () => syncAgentControl(setupId, primaryId));
});
window.addEventListener("focus", () => {
  refreshJob().catch(() => {});
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshJob().catch(() => {});
});
document.querySelectorAll("[data-view-target]").forEach((button) => {
  button.addEventListener("click", () => {
    setActiveView(button.dataset.viewTarget);
  });
});
document.querySelectorAll("[data-artifact-shortcut]").forEach((button) => {
  button.addEventListener("click", () => {
    loadArtifact(button.dataset.artifactShortcut);
  });
});

initializeChat();
setActiveView("overview");
loadDeepSeekStatus().catch(() => {
  $("deepseek-status").textContent = "DeepSeek API：状态读取失败";
  $("deepseek-status").className = "api-panel missing";
});
loadDemo({silent: true}).catch(() => {
  $("artifact-preview").textContent = "内置示例读取失败，但仍可手动粘贴文档和算例。";
}).finally(() => {
  loadJobHistory().catch(() => {});
});
