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
};
const DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9";
const DEFAULT_CHAT_ACTIONS = [
  {label: "载入示例", command: "载入示例"},
  {label: "历史任务", command: "历史任务"},
  {label: "启动", command: "启动"},
];

const $ = (id) => document.getElementById(id);

const VIEW_TITLES = {
  overview: "FJSP 求解质量优化",
  context: "Context Packet",
  worker: "Worker 输出过程",
  experiments: "实验监督",
  versions: "版本记录",
  resources: "知识库 / Skills",
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
  $("promotion-repeats").value = demo.config.promotion_repeats;
  $("artifact-preview").textContent = "SDST-HUdata LA20 默认测试已载入，可以直接启动循环迭代。";
  updateContractSummary();
  if (!options.silent) {
    appendChatMessage("assistant", "SDST-HUdata LA20 默认测试已载入：Main Agent 规划方向，OpenCode Coding Agent 自写 solver，固定 Core 评测。可以直接“启动”。");
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

async function loadJobHistory(options = {}) {
  const response = await fetch("/api/jobs");
  const payload = await response.json();
  const jobs = payload.jobs || [];
  renderJobHistory(jobs);
  if (options.restoreLatest && !state.currentJobId && jobs.length) {
    await selectHistoryJob(jobs[0].id, {loadReport: true});
  }
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
    const button = document.createElement("button");
    button.type = "button";
    button.className = `history-item ${job.id === state.currentJobId ? "active" : ""}`;
    const summary = job.summary?.worker_summary || {};
    const makespan = summary.final_makespan ?? summary.best_makespan_so_far ?? job.summary?.last_summary?.best_metrics?.makespan;
    button.innerHTML = `
      <strong>${escapeHtml(job.title || job.id)}</strong>
      <span>${escapeHtml(statusLabel(job.status))} · ${escapeHtml(formatShortTime(job.updated_at || job.created_at))}</span>
      <small>${makespan === undefined || makespan === null ? "makespan -" : `makespan ${escapeHtml(formatMetric(makespan))}`}</small>
    `;
    button.addEventListener("click", () => selectHistoryJob(job.id, {loadReport: true}));
    container.appendChild(button);
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
  await loadJobHistory({restoreLatest: false});
  if (["queued", "running"].includes(job.status)) {
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
    timeout_seconds: Number($("timeout-seconds").value || 60),
    worker_max_steps: Number($("worker-max-steps").value || 4),
    worker_max_runtime_seconds: Number($("worker-max-runtime-seconds").value || 120),
    in_round_repair_attempts: Number($("in-round-repair-attempts").value || 0),
    promotion_repeats: Number($("promotion-repeats").value || 1),
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
  if (state.deepseekStatus && !state.deepseekStatus.configured) {
    $("artifact-preview").textContent =
      "当前流程需要 DeepSeek API，但本地没有检测到 DEEPSEEK_API_KEY / DEEPSEEK_API_KEY_FILE。";
    appendChatMessage("assistant", "还没有检测到 DeepSeek 密钥，无法启动 Main Agent 和 Coding Agent。");
    return;
  }
  $("artifact-preview").textContent = "任务已提交，等待后端启动...";
  appendChatMessage("assistant", "任务已提交，我会把运行状态同步到右侧驾驶舱。");
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
  await loadJobHistory({restoreLatest: false});
  $("artifact-preview").textContent =
    "任务已启动，进度会持续刷新到右侧事件流。完成后会自动载入报告预览。";
  startPolling();
}

// ---------------------------------------------------------------------------
// 对话式操作入口：当前负责把自然操作意图映射为载入、启动、查看等 UI 命令。
// 真正的 Main Agent 方向规划发生在后端任务闭环中。
// ---------------------------------------------------------------------------

function initializeChat() {
  $("chat-thread").innerHTML = "";
  appendChatMessage("assistant", "请提供需求、IO 和算例。Main Agent 会自动匹配知识，OpenCode Coding Agent 负责写 solver，Core 负责复验。");
  renderChatActions(DEFAULT_CHAT_ACTIONS);
}

function appendChatMessage(role, text) {
  const thread = $("chat-thread");
  if (!thread) return;
  const item = document.createElement("div");
  item.className = `chat-message ${role}`;
  item.textContent = text;
  thread.appendChild(item);
  thread.scrollTop = thread.scrollHeight;
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
    await loadJobHistory({restoreLatest: false});
    appendChatMessage("assistant", state.currentJobId ? "状态已刷新。" : "当前还没有运行任务。");
    return;
  }
  if (["历史", "历史任务", "history"].some((token) => normalized.includes(token))) {
    setActiveView("versions");
    await loadJobHistory({restoreLatest: false});
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

async function refreshJob() {
  if (!state.currentJobId) {
    const response = await fetch("/api/jobs");
    const payload = await response.json();
    if (payload.jobs?.length) {
      state.currentJobId = payload.jobs[0].id;
      renderJob(payload.jobs[0]);
      if (["queued", "running"].includes(payload.jobs[0].status)) {
        startPolling();
      } else {
        await handleTerminalJob(payload.jobs[0]);
      }
    }
    return;
  }
  const response = await fetch(`/api/jobs/${state.currentJobId}`);
  const job = await response.json();
  if (response.ok) {
    renderJob(job);
    await handleTerminalJob(job);
  }
}

async function handleTerminalJob(job) {
  if (["queued", "running"].includes(job.status)) return;
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
  if (job.status !== state.lastRenderedStatus) {
    state.lastRenderedStatus = job.status;
    appendChatMessage("assistant", `任务状态：${statusLabel(job.status)}`);
  }

  const summary = job.summary?.last_summary || {};
  const benchmark = job.summary?.benchmark_summary || {};
  const roundSummary = job.summary?.round_summary || {};
  const workerSummary = job.summary?.worker_summary || {};
  const ziSummary = job.summary?.zi_summary || {};
  const makespan =
    workerSummary.final_makespan ??
    workerSummary.best_makespan_so_far ??
    workerSummary.latest_makespan ??
    summary.best_metrics?.makespan ??
    summary.best_candidate_metrics?.avg_makespan;
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
  $("metric-makespan").textContent = formatMetric(makespan);
  $("metric-gap").textContent = gap === undefined || gap === null ? "-" : `${Number(gap).toFixed(2)}%`;

  const log = $("event-log");
  log.innerHTML = "";
  for (const event of job.events || []) {
    const item = document.createElement("li");
    item.className = event.level || "info";
    item.innerHTML = `<strong>${event.time}</strong>${escapeHtml(event.message)}`;
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
    detail.innerHTML = "<div class=\"knowledge-empty\">运行后这里会展示每轮提出的方向、修补次数、JA/evaluator 结果和证据来源。</div>";
    return;
  }
  for (const round of rounds) {
    const card = document.createElement("article");
    card.className = `direction-card ${safeClass(round.status)} ${safeClass(round.decision)}`;
    const failures = (round.failure_signatures || []).map((item) => `<span class="tag danger">${escapeHtml(item)}</span>`).join("");
    const evidence = (round.evidence_used || []).slice(0, 2).map((item) => `<span class="tag">${escapeHtml(truncateText(item, 72))}</span>`).join("");
    card.innerHTML = `
      <header>
        <div>
          <span class="section-kicker">第 ${round.round_index + 1} 轮 · ${escapeHtml(round.strategy_type || "-")}</span>
          <h3>${escapeHtml(round.title || "-")}</h3>
        </div>
        <span class="tag ${round.decision === "promoted" ? "success" : round.status === "no_improvement" ? "warning" : "danger"}">${workerDecisionLabel(round.decision)}</span>
      </header>
      <p>${escapeHtml(truncateText(round.strategy_intent || "没有记录策略说明。", 220))}</p>
      <div class="direction-meta">
        <span class="tag">尝试 ${round.attempt_count ?? 0}</span>
        <span class="tag">valid ${round.valid ?? 0}/${round.total ?? 0}</span>
        <span class="tag">makespan ${formatMetric(round.makespan)}</span>
        ${round.gap_pct === null || round.gap_pct === undefined ? "" : `<span class="tag">gap ${formatPct(round.gap_pct)}</span>`}
        ${failures}
        ${evidence}
      </div>
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
    completed: "已完成",
    completed_with_warnings: "完成但有警告",
    interrupted: "已中断",
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
$("refresh-history").addEventListener("click", () => loadJobHistory({restoreLatest: false}));
$("chat-form").addEventListener("submit", handleChatSubmit);
$("reset-chat").addEventListener("click", initializeChat);
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
  loadJobHistory({restoreLatest: true}).catch(() => {});
});
