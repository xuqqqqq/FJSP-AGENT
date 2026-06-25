const state = {
  currentJobId: null,
  pollTimer: null,
  deepseekStatus: null,
  lastRenderedStatus: null,
  slotManifest: null,
};
const DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9";
const DEFAULT_CHAT_ACTIONS = [
  {label: "载入示例", command: "载入示例"},
  {label: "代码槽", command: "代码槽"},
  {label: "确认代码槽", command: "确认代码槽"},
  {label: "策略层", command: "策略层"},
  {label: "Template", command: "template"},
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
  $("evolution-mode").value = demo.config.evolution_mode;
  $("profile-mode").value = demo.config.profile_mode;
  $("strategy-candidates").value = demo.config.strategy_candidates;
  $("awls-zi-candidates").value = demo.config.awls_zi_candidates || 2;
  $("portfolio-size").value = demo.config.portfolio_size;
  $("timeout-seconds").value = demo.config.timeout_seconds;
  $("awls-same-machine-eval").value = demo.config.awls_same_machine_eval || "stable";
  $("worker-max-steps").value = demo.config.worker_max_steps;
  $("apply-worker-changes").checked = Boolean(demo.config.apply_worker_changes);
  $("artifact-preview").textContent = "内置示例已载入，可以直接启动循环迭代。";
  updateContractSummary();
  if (!options.silent) {
    appendChatMessage("assistant", "内置 Mk01 示例已载入。你可以继续说“代码槽”或“策略层”，也可以直接“启动”。");
  }
}

async function loadDeepSeekStatus() {
  const response = await fetch("/api/deepseek-status");
  const status = await response.json();
  state.deepseekStatus = status;
  const badge = $("deepseek-status");
  if (status.configured) {
    badge.textContent = `DeepSeek API：已配置 · ${status.model} · ${status.base_url}`;
    badge.className = "api-status ready";
  } else {
    badge.textContent = "DeepSeek API：未配置，请设置 DEEPSEEK_API_KEY 或 DEEPSEEK_API_KEY_FILE";
    badge.className = "api-status missing";
  }
}

async function loadSlotManifest() {
  const response = await fetch("/api/slot-manifest");
  const manifest = await response.json();
  state.slotManifest = manifest;
  renderSlotCards(manifest.slots || []);
  updateContractSummary();
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
    evolution_mode: $("evolution-mode").value,
    selected_slot_id: $("selected-slot-id").value || "awls_zi_policy",
    slot_user_confirmed: $("slot-user-confirmed").checked,
    profile_mode: $("profile-mode").value,
    strategy_candidates: Number($("strategy-candidates").value || 2),
    awls_zi_candidates: Number($("awls-zi-candidates").value || 2),
    portfolio_size: Number($("portfolio-size").value || 8),
    timeout_seconds: Number($("timeout-seconds").value || 60),
    local_search_neighborhood_profile: $("neighborhood-profile").value,
    awls_restarts: Number($("awls-restarts").value || 1),
    awls_cycles_per_restart: Number($("awls-cycles").value || 200),
    awls_iterations: Number($("awls-iterations").value || 2000),
    awls_time_limit_sec: Number($("awls-time-limit").value || 6),
    awls_init: $("awls-init").value,
    awls_beta: Number($("awls-beta").value || 500),
    awls_gamma: Number($("awls-gamma").value || 40),
    awls_theta: Number($("awls-theta").value || 5),
    awls_same_machine_eval: $("awls-same-machine-eval").value,
    awls_portfolio_lanes: $("awls-portfolio-lanes").value,
    apply_worker_changes: $("apply-worker-changes").checked,
    worker_max_steps: Number($("worker-max-steps").value || 4),
  };
}

function updateContractSummary() {
  const target = $("slot-contract-summary");
  if (!target) return;
  const runMode = $("run-mode").value;
  const evolutionMode = $("evolution-mode").value;
  const profileMode = $("profile-mode").value;
  if (runMode === "awls_zi") {
    target.textContent = "AWLS-ZI policy search";
    return;
  }
  if (evolutionMode === "slot") {
    const slot = selectedSlot();
    const status = $("slot-user-confirmed").checked ? "confirmed" : "needs confirmation";
    target.textContent = `${slot?.slot_id || "code slot"} · ${status}`;
    updateSlotConfirmationState();
    return;
  }
  if (evolutionMode === "code") {
    target.textContent = "candidate worktree code";
    return;
  }
  target.textContent = `${profileMode} strategy profile`;
}

function selectedSlot() {
  const selectedId = $("selected-slot-id")?.value || "awls_zi_policy";
  return (state.slotManifest?.slots || []).find((slot) => slot.slot_id === selectedId) || null;
}

function updateSlotConfirmationState() {
  const stateBadge = $("slot-confirmation-state");
  if (!stateBadge) return;
  const confirmed = $("slot-user-confirmed").checked;
  const selectedId = $("selected-slot-id").value || "awls_zi_policy";
  stateBadge.textContent = confirmed ? `confirmed · ${selectedId}` : `unconfirmed · ${selectedId}`;
  stateBadge.className = `status-pill ${confirmed ? "confirmed" : "unconfirmed"}`;
}

function renderSlotCards(slots) {
  const container = $("slot-cards");
  if (!container) return;
  container.innerHTML = "";
  const selectedId = $("selected-slot-id").value || "awls_zi_policy";
  for (const slot of slots) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `slot-card ${slot.slot_id === selectedId ? "active" : ""}`;
    const workerStatus = slot.slot_id === "awls_zi_policy" ? "可执行" : "规划中";
    card.innerHTML = `
      <span>${escapeHtml(slot.slot_id)} · ${escapeHtml(workerStatus)}</span>
      <strong>${escapeHtml(slot.title || slot.slot_id)}</strong>
      <small>${escapeHtml(slot.purpose || "")}</small>
      <em>${escapeHtml(slot.target_file || "")}</em>
    `;
    card.addEventListener("click", () => selectSlot(slot.slot_id));
    container.appendChild(card);
  }
  updateSlotConfirmationState();
}

function selectSlot(slotId) {
  $("selected-slot-id").value = slotId;
  $("slot-user-confirmed").checked = false;
  renderSlotCards(state.slotManifest?.slots || []);
  updateContractSummary();
  const slot = selectedSlot();
  appendChatMessage("assistant", `已选择代码槽：${slot?.title || slotId}。启动代码槽演进前还需要确认 IO 和 evaluator 不变。`);
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
    payload.evolution_mode === "slot" ||
    payload.evolution_mode === "code" ||
    payload.profile_mode === "deepseek";
  if (payload.evolution_mode === "slot" && !payload.slot_user_confirmed) {
    $("artifact-preview").textContent = "代码槽模式需要先确认选中的 slot 契约。";
    appendChatMessage("assistant", "请先勾选确认，或在对话里说“确认代码槽”。");
    return;
  }
  if (payload.evolution_mode === "slot" && payload.selected_slot_id !== "awls_zi_policy") {
    $("artifact-preview").textContent = "当前可执行的 slot worker 只支持 awls_zi_policy；邻域动作槽已建模，执行器还在下一步接入。";
    appendChatMessage("assistant", "这个代码槽已经进入平台 manifest，但当前执行 worker 还只支持 AWLS zi。先选 awls_zi_policy 跑通闭环。");
    return;
  }
  if (needsDeepSeek && state.deepseekStatus && !state.deepseekStatus.configured) {
    $("artifact-preview").textContent =
      "当前选择需要 DeepSeek API，但本地没有检测到 DEEPSEEK_API_KEY / DEEPSEEK_API_KEY_FILE。若只想跑通流程，可把策略来源切到 template；若要 DeepSeek 写策略或写代码，请先配置本地密钥。";
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
  renderJob(job);
  $("artifact-preview").textContent =
    "任务已启动，进度会持续刷新到右侧事件流。完成后会自动载入报告预览。";
  startPolling();
}

function initializeChat() {
  $("chat-thread").innerHTML = "";
  appendChatMessage("assistant", "我们从一个可验证的 FJSP 任务开始。你可以载入示例、切换代码槽或策略层，然后启动循环。");
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
  if (["确认代码槽", "确认slot", "确认 slot", "confirm slot"].some((token) => normalized.includes(token))) {
    $("slot-user-confirmed").checked = true;
    updateContractSummary();
    appendChatMessage("assistant", `已确认代码槽：${$("selected-slot-id").value}。本轮会锁住 IO、parser 和 evaluator。`);
    return;
  }
  if (["代码槽", "slot", "zi"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("solver").value = "awls";
    $("evolution-mode").value = "slot";
    $("profile-mode").value = "deepseek";
    $("slot-user-confirmed").checked = false;
    $("worker-max-steps").value = Math.max(4, Number($("worker-max-steps").value || 4));
    updateContractSummary();
    appendChatMessage("assistant", "已切到代码槽模式。请选中 slot 并说“确认代码槽”，DeepSeek 才能修改该槽。");
    return;
  }
  if (["策略", "strategy", "profile"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("evolution-mode").value = "strategy";
    $("profile-mode").value = "deepseek";
    $("slot-user-confirmed").checked = false;
    updateContractSummary();
    appendChatMessage("assistant", "已切到策略层：生成 profile，不直接改源码。");
    return;
  }
  if (["template", "本地", "兜底"].some((token) => normalized.includes(token))) {
    $("profile-mode").value = "template";
    if ($("evolution-mode").value !== "slot") {
      $("evolution-mode").value = "strategy";
    }
    updateContractSummary();
    appendChatMessage("assistant", "已切到 template，本地兜底流程可以在没有 DeepSeek 密钥时跑通。");
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
    appendChatMessage("assistant", state.currentJobId ? "状态已刷新。" : "当前还没有运行任务。");
    return;
  }
  if (["参数", "配置", "config"].some((token) => normalized.includes(token))) {
    $("job-form").scrollIntoView({behavior: "smooth", block: "start"});
    appendChatMessage("assistant", "配置区已经定位到左侧下方。");
    return;
  }
  appendChatMessage("assistant", "我可以处理：载入示例、代码槽、策略层、template、DeepSeek、AWLS、启动、刷新。");
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
    }
    return;
  }
  const response = await fetch(`/api/jobs/${state.currentJobId}`);
  const job = await response.json();
  if (response.ok) {
    renderJob(job);
    if (!["queued", "running"].includes(job.status) && state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      const preferredReport =
        job.artifacts?.report ? "report" :
        job.artifacts?.zi_evolution_report ? "zi_evolution_report" :
        job.artifacts?.standard_agent_report ? "standard_agent_report" :
        null;
      if (preferredReport) {
        loadArtifact(preferredReport);
      }
    }
  }
}

function renderJob(job) {
  $("empty-state").classList.add("hidden");
  $("job-view").classList.remove("hidden");
  $("job-title").textContent = job.title;
  $("job-status").textContent = job.status;
  $("job-status").className = `status-pill ${job.status}`;
  if (job.status !== state.lastRenderedStatus) {
    state.lastRenderedStatus = job.status;
    appendChatMessage("assistant", `任务状态：${job.status}`);
  }

  const summary = job.summary?.last_summary || {};
  const benchmark = job.summary?.benchmark_summary || {};
  const roundSummary = job.summary?.round_summary || {};
  const workerSummary = job.summary?.worker_summary || {};
  const ziSummary = job.summary?.zi_summary || {};
  const makespan =
    workerSummary.final_makespan ??
    summary.best_metrics?.makespan ??
    summary.best_candidate_metrics?.avg_makespan;
  const gap =
    ziSummary.best_avg_gap_pct ??
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
        ? `baseline ${workerSummary.baseline_valid ?? "-"}/${workerSummary.baseline_total ?? "-"} · promoted ${workerSummary.promoted_rounds}/${workerSummary.round_count}`
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

function labelForArtifact(name) {
  const labels = {
    manifest: "Manifest",
    report: "Demo Report",
    standard_agent_report: "Agent Report",
    zi_evolution_summary: "AWLS-ZI Summary",
    zi_evolution_report: "AWLS-ZI Report",
    hypothesis_graph: "Hypothesis Graph",
    exception: "Exception Trace",
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
  $("artifact-path").textContent = payload.path;
  $("artifact-preview").textContent = payload.text + (payload.truncated ? "\n\n[内容过长，已截断预览]" : "");
}

function formatMetric(value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  return Number.isInteger(num) ? String(num) : num.toFixed(2);
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
$("chat-form").addEventListener("submit", handleChatSubmit);
$("reset-chat").addEventListener("click", initializeChat);
$("slot-user-confirmed").addEventListener("change", updateContractSummary);
for (const id of ["run-mode", "evolution-mode", "profile-mode"]) {
  $(id).addEventListener("change", updateContractSummary);
}

initializeChat();
loadDeepSeekStatus().catch(() => {
  $("deepseek-status").textContent = "DeepSeek API：状态读取失败";
  $("deepseek-status").className = "api-status missing";
});
loadSlotManifest().catch(() => {
  $("artifact-preview").textContent = "代码槽 manifest 读取失败，代码槽模式暂不可用。";
});
loadDemo({silent: true}).catch(() => {
  $("artifact-preview").textContent = "内置示例读取失败，但仍可手动粘贴文档和算例。";
});
