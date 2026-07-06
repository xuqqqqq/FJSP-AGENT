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
  {label: "本地兜底", command: "template"},
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
  $("selected-slot-id").value = "agent_auto";
  $("slot-user-confirmed").checked = false;
  $("strategy-candidates").value = demo.config.strategy_candidates;
  $("awls-zi-candidates").value = demo.config.awls_zi_candidates || 2;
  $("portfolio-size").value = demo.config.portfolio_size;
  $("timeout-seconds").value = demo.config.timeout_seconds;
  $("awls-time-policy").value = demo.config.awls_time_policy || "scaled";
  $("awls-time-limit").value = demo.config.awls_time_limit_sec || 30;
  $("awls-zi-policy").value = demo.config.awls_zi_policy || "auto";
  $("awls-critical-block-exhaustive-pct").value = demo.config.awls_critical_block_exhaustive_pct ?? 75;
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
    selected_slot_id: $("selected-slot-id").value || "agent_auto",
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
  const target = $("slot-contract-summary");
  if (!target) return;
  const runMode = $("run-mode").value;
  const evolutionMode = $("evolution-mode").value;
  const profileMode = $("profile-mode").value;
  if (runMode === "awls_zi") {
    target.textContent = "AWLS-ZI 参数/规则搜索";
    return;
  }
  if (evolutionMode === "slot") {
    const slot = selectedSlot();
    const status = $("slot-user-confirmed").checked ? "已确认" : "待确认";
    const label = $("selected-slot-id").value === "agent_auto" ? "agent 自动选槽" : (slot?.slot_id || "code slot");
    target.textContent = `${label} · ${status}`;
    updateSlotConfirmationState();
    return;
  }
  if (evolutionMode === "code") {
    target.textContent = "候选 worktree 代码";
    return;
  }
  target.textContent = `${profileModeLabel(profileMode)}策略层`;
}

function selectedSlot() {
  const selectedId = $("selected-slot-id")?.value || "agent_auto";
  if (selectedId === "agent_auto") return null;
  return (state.slotManifest?.slots || []).find((slot) => slot.slot_id === selectedId) || null;
}

function updateSlotConfirmationState() {
  const stateBadge = $("slot-confirmation-state");
  if (!stateBadge) return;
  const confirmed = $("slot-user-confirmed").checked;
  const selectedId = $("selected-slot-id").value || "agent_auto";
  const label = selectedId === "agent_auto" ? "agent 自动选槽" : selectedId;
  stateBadge.textContent = confirmed ? `已确认 · ${label}` : `未确认 · ${label}`;
  stateBadge.className = `status-pill ${confirmed ? "confirmed" : "unconfirmed"}`;
  updateSlotFlow();
}

function updateSlotFlow() {
  const flow = document.querySelectorAll(".slot-flow span");
  if (!flow.length) return;
  const hasSlot = Boolean(selectedSlot());
  const confirmed = $("slot-user-confirmed").checked;
  flow.forEach((item, index) => {
    const active =
      index === 0 ||
      (hasSlot && index <= 2) ||
      (confirmed && index <= 4);
    item.className = active ? "active" : "";
  });
}

function renderSlotCards(slots) {
  const container = $("slot-cards");
  if (!container) return;
  container.innerHTML = "";
  const selectedId = $("selected-slot-id").value || "agent_auto";
  const autoCard = document.createElement("button");
  autoCard.type = "button";
  autoCard.className = `slot-card ${selectedId === "agent_auto" ? "active" : ""}`;
  autoCard.innerHTML = `
    <span>agent 自动 · 已接入 · 推荐</span>
    <strong>Agent 自动选择代码槽</strong>
    <small>根据需求文档、IO、算例特征和 slot 契约选择本轮最该修改的功能分区。</small>
    <em>启动后写入事件流和 slot_manifest</em>
  `;
  autoCard.addEventListener("click", () => selectSlot("agent_auto"));
  container.appendChild(autoCard);
  for (const slot of slots) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `slot-card ${slot.slot_id === selectedId ? "active" : ""}`;
    const workerStatus = slot.advisor?.worker_support_label || (slot.advisor?.worker_support === "available" ? "已接入" : "待接入");
    const significance = slot.advisor?.significance_label ? `重要性 ${slot.advisor.significance_label}` : "重要性待评估";
    const lineRange =
      slot.line_start && slot.line_end ? `${slot.target_file}:${slot.line_start}-${slot.line_end}` : slot.target_file;
    card.innerHTML = `
      <span>${escapeHtml(slot.slot_id)} · ${escapeHtml(workerStatus)} · ${escapeHtml(significance)}</span>
      <strong>${escapeHtml(slot.title || slot.slot_id)}</strong>
      <small>${escapeHtml(slot.purpose || "")}</small>
      <em>${escapeHtml(lineRange || "")}</em>
    `;
    card.addEventListener("click", () => selectSlot(slot.slot_id));
    container.appendChild(card);
  }
  renderSelectedSlotDetail();
  updateSlotConfirmationState();
}

function selectSlot(slotId) {
  $("selected-slot-id").value = slotId;
  $("slot-user-confirmed").checked = false;
  renderSlotCards(state.slotManifest?.slots || []);
  updateContractSummary();
  const slot = selectedSlot();
  const title = slotId === "agent_auto" ? "Agent 自动选择" : (slot?.title || slotId);
  appendChatMessage("assistant", `已选择代码槽策略：${title}。启动代码槽演进前还需要确认 IO 和评测器不变。`);
}

function renderSelectedSlotDetail() {
  const detail = $("slot-detail");
  if (!detail) return;
  if (($("selected-slot-id")?.value || "") === "agent_auto") {
    detail.innerHTML = `
      <div class="slot-detail-header">
        <div>
          <span>agent_auto · Python marked slots · fixed evaluator</span>
          <strong>Agent 自动选择代码槽</strong>
        </div>
        <span class="status-pill confirmed">已接入</span>
      </div>
      <div class="slot-tag-row">
        <span>自动选择</span>
        <span>可行性：可执行</span>
        <span>重要性：按算例与需求判断</span>
        <span>启动后解析</span>
      </div>
      <p>平台会先根据需求文档、IO 文档、算例是否含 SDST setup matrix、求解器和代码槽契约选择一个具体 slot，然后只确认并修改该 slot。</p>
      <section class="slot-advisor-note">
        <h4>顾问结论</h4>
        <p>用户不需要猜该改 zi、邻域、tabu 还是初始化；agent 负责选择，Core evaluator 负责验收。</p>
      </section>
    `;
    return;
  }
  const slot = selectedSlot();
  if (!slot) {
    detail.textContent = "正在读取代码槽契约。";
    return;
  }
  const advisor = slot.advisor || {};
  const ioSummary = [
    ["输入", slot.inputs || []],
    ["输出", slot.outputs || []],
    ["不变量", slot.invariants || []],
    ["禁止修改", slot.forbidden_edits || []],
  ]
    .map(([label, values]) => `<section><h4>${label}</h4><ul>${listItems(values)}</ul></section>`)
    .join("");
  const validation = listItems(slot.validation_commands || []);
  const allowed = listItems(slot.allowed_edits || []);
  const concerns = listItems(advisor.concerns || []);
  const suggestions = listItems(advisor.suggestions || []);
  const preview = String(slot.original_content || "").slice(0, 1800);
  detail.innerHTML = `
    <div class="slot-detail-header">
      <div>
        <span>${escapeHtml(slot.slot_kind || "marked_block")} · ${escapeHtml(slot.language || "plaintext")} · ${escapeHtml(slot.target_file || "")}</span>
        <strong>${escapeHtml(slot.title || slot.slot_id)}</strong>
      </div>
      <span class="status-pill ${advisor.worker_support === "available" ? "confirmed" : "unconfirmed"}">
        ${escapeHtml(advisor.worker_support_label || "未知")}
      </span>
    </div>
    <div class="slot-tag-row">
      <span>${escapeHtml(advisor.advisor_mode || "本地顾问初筛")}</span>
      <span>可行性：${escapeHtml(advisor.feasibility_label || advisor.feasibility || "-")}</span>
      <span>重要性：${escapeHtml(advisor.significance_label || advisor.significance || "-")}</span>
      <span>${escapeHtml(slot.line_start && slot.line_end ? `第 ${slot.line_start}-${slot.line_end} 行` : "行号待解析")}</span>
    </div>
    <p>${escapeHtml(advisor.feasibility_reason || "")}</p>
    <section class="slot-advisor-note">
      <h4>顾问结论</h4>
      <p>${escapeHtml(advisor.rationale || advisor.block_summary || "")}</p>
    </section>
    <section>
      <h4>可改范围</h4>
      <ul>${allowed}</ul>
    </section>
    <div class="slot-io-grid">${ioSummary}</div>
    <section>
      <h4>验证命令</h4>
      <ul>${validation}</ul>
    </section>
    <section class="slot-advisor-columns">
      <div><h4>主要风险</h4><ul>${concerns}</ul></div>
      <div><h4>操作建议</h4><ul>${suggestions}</ul></div>
    </section>
    <h4>代码片段</h4>
    <pre class="slot-preview">${escapeHtml(preview || "源码块暂不可读。")}</pre>
  `;
}

function listItems(values) {
  const items = Array.isArray(values) ? values : [];
  if (!items.length) return "<li>-</li>";
  return items.map((value) => `<li>${escapeHtml(value)}</li>`).join("");
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
    $("artifact-preview").textContent = "代码槽模式需要先确认选中的代码槽契约。";
    appendChatMessage("assistant", "请先勾选确认，或在对话里说“确认代码槽”。");
    return;
  }
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
    const label = $("selected-slot-id").value === "agent_auto" ? "agent 自动选择" : $("selected-slot-id").value;
    appendChatMessage("assistant", `已确认代码槽策略：${label}。本轮会锁住 IO、解析器和评测器。`);
    return;
  }
  if (["代码槽", "slot", "zi"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("solver").value = "awls";
    $("evolution-mode").value = "slot";
    $("selected-slot-id").value = "agent_auto";
    $("profile-mode").value = "deepseek";
    $("slot-user-confirmed").checked = false;
    $("worker-max-steps").value = Math.max(4, Number($("worker-max-steps").value || 4));
    updateContractSummary();
    appendChatMessage("assistant", "已切到代码槽模式，默认由 agent 自动选择槽。请说“确认代码槽”锁住 IO 和评测器。");
    return;
  }
  if (["策略", "strategy", "profile"].some((token) => normalized.includes(token))) {
    $("run-mode").value = "standard_loop";
    $("evolution-mode").value = "strategy";
    $("profile-mode").value = "deepseek";
    $("slot-user-confirmed").checked = false;
    updateContractSummary();
    appendChatMessage("assistant", "已切到策略层：生成策略参数，不直接改源码。");
    return;
  }
  if (["template", "本地", "兜底"].some((token) => normalized.includes(token))) {
    $("profile-mode").value = "template";
    if ($("evolution-mode").value !== "slot") {
      $("evolution-mode").value = "strategy";
    }
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
        ? `基线 ${workerSummary.baseline_valid ?? "-"}/${workerSummary.baseline_total ?? "-"} · 提升 ${workerSummary.promoted_rounds}/${workerSummary.round_count}`
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
    manifest: "运行清单",
    report: "演示报告",
    standard_agent_report: "Agent 报告",
    zi_evolution_summary: "AWLS-ZI 摘要",
    zi_evolution_report: "AWLS-ZI 报告",
    slot_manifest: "代码槽契约",
    hypothesis_graph: "假设图谱",
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
  $("artifact-path").textContent = payload.path;
  $("artifact-preview").textContent = payload.text + (payload.truncated ? "\n\n[内容过长，已截断预览]" : "");
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
  $("artifact-preview").textContent = "代码槽清单读取失败，代码槽模式暂不可用。";
});
loadDemo({silent: true}).catch(() => {
  $("artifact-preview").textContent = "内置示例读取失败，但仍可手动粘贴文档和算例。";
});
