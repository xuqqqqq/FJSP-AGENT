const state = {
  currentJobId: null,
  pollTimer: null,
  deepseekStatus: null,
};
const DEFAULT_STANDARD_SEEDS = "0,1,2,3,4,5,6,7,8,9";

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

async function loadDemo() {
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

async function submitJob(event) {
  event.preventDefault();
  const payload = buildPayload();
  if (!payload.requirement.text.trim() || !payload.io.text.trim() || !payload.instance.text.trim()) {
    $("artifact-preview").textContent = "请先提供需求文档、IO 文档和算例。";
    return;
  }
  const needsDeepSeek =
    payload.run_mode === "awls_zi" ||
    payload.evolution_mode === "code" ||
    payload.profile_mode === "deepseek";
  if (needsDeepSeek && state.deepseekStatus && !state.deepseekStatus.configured) {
    $("artifact-preview").textContent =
      "当前选择需要 DeepSeek API，但本地没有检测到 DEEPSEEK_API_KEY / DEEPSEEK_API_KEY_FILE。若只想跑通流程，可把策略来源切到 template；若要 DeepSeek 写策略或写代码，请先配置本地密钥。";
    return;
  }
  $("artifact-preview").textContent = "任务已提交，等待后端启动...";
  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const job = await response.json();
  if (!response.ok) {
    $("artifact-preview").textContent = `提交失败：${job.error || response.statusText}`;
    return;
  }
  state.currentJobId = job.id;
  renderJob(job);
  $("artifact-preview").textContent =
    "任务已启动，进度会持续刷新到右侧事件流。完成后会自动载入报告预览。";
  startPolling();
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
        ? `${workerSummary.promoted_rounds}/${workerSummary.round_count}`
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

loadDeepSeekStatus().catch(() => {
  $("deepseek-status").textContent = "DeepSeek API：状态读取失败";
  $("deepseek-status").className = "api-status missing";
});
loadDemo().catch(() => {
  $("artifact-preview").textContent = "内置示例读取失败，但仍可手动粘贴文档和算例。";
});
