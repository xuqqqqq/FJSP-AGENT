const state = {
  currentJobId: null,
  pollTimer: null,
  deepseekStatus: null,
  lastRenderedStatus: null,
  previewArtifactName: null,
  autoPreviewKey: null,
};
const DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9";
const DEFAULT_CHAT_ACTIONS = [
  {label: "载入示例", command: "载入示例"},
  {label: "自由代码", command: "自由代码"},
  {label: "策略层", command: "策略层"},
  {label: "本地兜底", command: "template"},
  {label: "历史任务", command: "历史任务"},
  {label: "启动", command: "启动"},
];

const $ = (id) => document.getElementById(id);

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
  $("run-mode").value = demo.config.run_mode || "standard_loop";
  $("max-rounds").value = demo.config.max_rounds;
  $("seeds").value = demo.config.seeds;
  $("solver").value = demo.config.solver;
  $("baseline-source").value = demo.config.baseline_source || "current_project";
  $("evolution-mode").value = demo.config.evolution_mode === "slot" ? "code" : demo.config.evolution_mode;
  $("profile-mode").value = demo.config.profile_mode;
  $("strategy-candidates").value = demo.config.strategy_candidates;
  $("awls-zi-candidates").value = demo.config.awls_zi_candidates || 2;
  $("portfolio-size").value = demo.config.portfolio_size;
  $("timeout-seconds").value = demo.config.timeout_seconds;
  $("awls-time-policy").value = demo.config.awls_time_policy || "scaled";
  $("awls-time-limit").value = demo.config.awls_time_limit_sec || 30;
  $("awls-restarts").value = demo.config.awls_restarts || 1;
  $("awls-cycles").value = demo.config.awls_cycles_per_restart || 200;
  $("awls-iterations").value = demo.config.awls_iterations || 2000;
  $("awls-init").value = demo.config.awls_init || "random";
  $("awls-beta").value = demo.config.awls_beta || 500;
  $("awls-gamma").value = demo.config.awls_gamma || 40;
  $("awls-theta").value = demo.config.awls_theta ?? 5;
  $("awls-zi-policy").value = demo.config.awls_zi_policy || "auto";
  $("awls-critical-block-exhaustive-pct").value = demo.config.awls_critical_block_exhaustive_pct ?? 75;
  $("awls-same-machine-eval").value = demo.config.awls_same_machine_eval || "stable";
  $("awls-portfolio-lanes").value = demo.config.awls_portfolio_lanes ?? "3:random:1,5:mixed:1,17:random:1";
  $("worker-max-steps").value = demo.config.worker_max_steps;
  $("apply-worker-changes").checked = Boolean(demo.config.apply_worker_changes);
  $("artifact-preview").textContent = "SDST-HUdata LA20 默认测试已载入，可以直接启动循环迭代。";
  updateContractSummary();
  if (!options.silent) {
    appendChatMessage("assistant", "SDST-HUdata LA20 默认测试已载入：Agent 自写初始 solver，DeepSeek 自由代码层，10 轮，10 个种子。可以直接“启动”。");
  }
}

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
    badge.className = "api-status ready";
  } else {
    badge.innerHTML = renderDeepSeekHelp(status);
    badge.className = "api-status missing";
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

function profileModeLabel(mode) {
  const labels = {
    deepseek: "DeepSeek ",
    auto: "自动 ",
    template: "本地兜底 ",
  };
  return labels[mode] || `${mode} `;
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
    run_mode: $("run-mode").value,
    max_rounds: Number($("max-rounds").value || 2),
    seeds: $("seeds").value || DEFAULT_STANDARD_SEEDS,
    solver: $("solver").value,
    baseline_source: $("baseline-source").value,
    evolution_mode: $("evolution-mode").value === "slot" ? "code" : $("evolution-mode").value,
    selected_slot_id: "agent_auto",
    slot_user_confirmed: false,
    profile_mode: $("profile-mode").value,
    strategy_candidates: Number($("strategy-candidates").value || 2),
    awls_zi_candidates: Number($("awls-zi-candidates").value || 2),
    portfolio_size: Number($("portfolio-size").value || 8),
    timeout_seconds: Number($("timeout-seconds").value || 60),
    local_search_neighborhood_profile: $("neighborhood-profile").value,
    awls_restarts: Number($("awls-restarts").value || 1),
    awls_cycles_per_restart: Number($("awls-cycles").value || 200),
    awls_iterations: Number($("awls-iterations").value || 2000),
    awls_time_limit_sec: Number($("awls-time-limit").value || 30),
    awls_time_policy: $("awls-time-policy").value,
    awls_init: $("awls-init").value,
    awls_beta: Number($("awls-beta").value || 500),
    awls_gamma: Number($("awls-gamma").value || 40),
    awls_theta: Number($("awls-theta").value || 5),
    awls_zi_policy: $("awls-zi-policy").value,
    awls_critical_block_exhaustive_pct: Number($("awls-critical-block-exhaustive-pct").value || 75),
    awls_same_machine_eval: $("awls-same-machine-eval").value,
    awls_portfolio_lanes: $("awls-portfolio-lanes").value,
    apply_worker_changes: $("apply-worker-changes").checked,
    worker_max_steps: Number($("worker-max-steps").value || 4),
  };
}

function updateContractSummary() {
  const target = $("edit-scope-summary");
  if (!target) return;
  const runMode = $("run-mode").value;
  const evolutionMode = $("evolution-mode").value;
  const baselineSource = $("baseline-source").value;
  const profileMode = $("profile-mode").value;
  if (runMode === "awls_zi") {
    target.textContent = "AWLS-ZI 参数/规则搜索";
    return;
  }
  if (evolutionMode === "code") {
    target.textContent = `候选 worktree 代码 · 基线=${baselineSource === "agent_generated" ? "agent自写" : "当前工程"}`;
    return;
  }
  target.textContent = `${profileModeLabel(profileMode)}策略层`;
}

function syncBaselineControls(changedId) {
  if (changedId === "solver" && $("solver").value === "agent-generated") {
    $("baseline-source").value = "agent_generated";
    $("evolution-mode").value = "code";
  }
  if (changedId === "baseline-source" && $("baseline-source").value === "agent_generated") {
    $("solver").value = "agent-generated";
    $("evolution-mode").value = "code";
  }
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
  const needsDeepSeek =
    payload.run_mode === "awls_zi" ||
    payload.evolution_mode === "code" ||
    payload.profile_mode === "deepseek";
  if (needsDeepSeek && state.deepseekStatus && !state.deepseekStatus.configured) {
    $("artifact-preview").textContent =
      "当前选择需要 DeepSeek API，但本地没有检测到 DEEPSEEK_API_KEY / DEEPSEEK_API_KEY_FILE。若只想跑通流程，可把策略来源切到本地兜底；若要 DeepSeek 写策略或写代码，请先配置本地密钥。";
    appendChatMessage("assistant", "当前配置需要 DeepSeek，但本地没有检测到密钥。可以说“template”切成本地兜底流程。");
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

function initializeChat() {
  $("chat-thread").innerHTML = "";
  appendChatMessage("assistant", "我们从一个可验证的 FJSP 任务开始。你可以载入示例、切换策略层或自由代码层，然后启动循环。");
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
  if (["自由代码", "自写", "agent baseline", "agent自写"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("solver").value = "agent-generated";
    $("baseline-source").value = "agent_generated";
    $("evolution-mode").value = "code";
    $("profile-mode").value = "deepseek";
    updateContractSummary();
    appendChatMessage("assistant", "已切到 Agent 自写 baseline：先生成初始 solver，再由 Core 评测。");
    return;
  }
  if (["策略", "strategy", "profile"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("evolution-mode").value = "strategy";
    $("profile-mode").value = "deepseek";
    updateContractSummary();
    appendChatMessage("assistant", "已切到策略层：生成策略参数，不直接改源码。");
    return;
  }
  if (["template", "本地", "兜底"].some((token) => normalized.includes(token))) {
    $("profile-mode").value = "template";
    $("evolution-mode").value = "strategy";
    updateContractSummary();
    appendChatMessage("assistant", "已切到本地兜底流程，可以在没有 DeepSeek 密钥时跑通。");
    return;
  }
  if (["deepseek", "模型"].some((token) => normalized.includes(token))) {
    $("profile-mode").value = "deepseek";
    updateContractSummary();
    appendChatMessage("assistant", "已切回 DeepSeek。启动前会检查本地密钥状态。");
    return;
  }
  if (["awls"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "awls_zi";
    $("solver").value = "awls";
    updateContractSummary();
    appendChatMessage("assistant", "已切到 AWLS-ZI 参数/规则演进。");
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
    await loadJobHistory({restoreLatest: false});
    $("history-list").scrollIntoView({behavior: "smooth", block: "nearest"});
    appendChatMessage("assistant", "历史任务已刷新，可以在右侧点击任意一次运行查看报告。");
    return;
  }
  if (["参数", "配置", "config"].some((token) => normalized.includes(token))) {
    $("job-form").scrollIntoView({behavior: "smooth", block: "start"});
    appendChatMessage("assistant", "配置区已经定位到左侧下方。");
    return;
  }
  appendChatMessage("assistant", "我可以处理：载入示例、自由代码、策略层、template、DeepSeek、AWLS、历史任务、启动、刷新。");
}

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
}

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
    standard_agent_report: "Agent 报告",
    zi_evolution_summary: "AWLS-ZI 摘要",
    zi_evolution_report: "AWLS-ZI 报告",
    slot_manifest: "内部配置",
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
  if (artifacts.standard_agent_report) return "standard_agent_report";
  if (artifacts.loop_report) return "loop_report";
  if (artifacts.zi_evolution_report) return "zi_evolution_report";
  return null;
}

function formatMetric(value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  return Number.isInteger(num) ? String(num) : num.toFixed(2);
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
for (const id of ["run-mode", "evolution-mode", "profile-mode", "baseline-source", "solver"]) {
  $(id).addEventListener("change", () => {
    syncBaselineControls(id);
    updateContractSummary();
  });
}

initializeChat();
loadDeepSeekStatus().catch(() => {
  $("deepseek-status").textContent = "DeepSeek API：状态读取失败";
  $("deepseek-status").className = "api-status missing";
});
loadDemo({silent: true}).catch(() => {
  $("artifact-preview").textContent = "内置示例读取失败，但仍可手动粘贴文档和算例。";
}).finally(() => {
  loadJobHistory({restoreLatest: true}).catch(() => {});
});
