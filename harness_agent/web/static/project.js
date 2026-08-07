const PROJECT_DRAFT_DB = "algoforge-project-intake";
const PROJECT_DRAFT_STORE = "drafts";
const ACTIVE_DRAFT_KEY = "active";
const DEFAULT_SEEDS = "0,1,2,3,4,5,6,7,8,9";

const $ = (id) => document.getElementById(id);

function openDraftDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(PROJECT_DRAFT_DB, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(PROJECT_DRAFT_STORE)) {
        request.result.createObjectStore(PROJECT_DRAFT_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("无法打开项目草稿存储"));
  });
}

async function readDraft() {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(PROJECT_DRAFT_STORE, "readonly");
    const request = transaction.objectStore(PROJECT_DRAFT_STORE).get(ACTIVE_DRAFT_KEY);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error || new Error("无法读取项目草稿"));
    transaction.oncomplete = () => database.close();
  });
}

async function writeDraft(draft) {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(PROJECT_DRAFT_STORE, "readwrite");
    transaction.objectStore(PROJECT_DRAFT_STORE).put(draft, ACTIVE_DRAFT_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error || new Error("无法保存项目草稿"));
  });
}

async function deleteDraft() {
  const database = await openDraftDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(PROJECT_DRAFT_STORE, "readwrite");
    transaction.objectStore(PROJECT_DRAFT_STORE).delete(ACTIVE_DRAFT_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error || new Error("无法清除项目草稿"));
  });
}

function setMessage(text, kind = "") {
  const target = $("project-page-message");
  if (!target) return;
  target.textContent = text || "";
  target.className = `project-page-message ${kind}`.trim();
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function defaultContract() {
  return {
    entrypoint: "solver.py",
    targetFile: "solver.py",
    solverCommand: "python solver.py --input {instance} --output {solution} --seed {seed} --time-limit-sec {solver_time_limit_seconds}",
    useProjectInstances: true,
    edited: false,
  };
}

function draftFromImportControls(draft) {
  return {
    ...draft,
    contract: {
      entrypoint: $("starter-solver-entrypoint").value.trim(),
      targetFile: $("starter-target-file").value.trim(),
      solverCommand: $("starter-solver-command").value.trim(),
      useProjectInstances: $("starter-use-project-instances").checked,
      edited: Boolean(draft.contract?.edited),
    },
  };
}

function populateImportControls(draft) {
  const contract = draft?.contract || defaultContract();
  $("starter-solver-entrypoint").value = contract.entrypoint || "solver.py";
  $("starter-target-file").value = contract.targetFile || "solver.py";
  $("starter-solver-command").value = contract.solverCommand || defaultContract().solverCommand;
  $("starter-use-project-instances").checked = contract.useProjectInstances !== false;
  const loaded = Boolean(draft?.project?.base64);
  $("starter-project-status").textContent = loaded ? draft.project.name : "未选择";
  $("clear-starter-project").disabled = !loaded;
  $("continue-starter-project").disabled = !loaded;
}

async function initImportPage() {
  let draft = await readDraft();
  if (!draft) draft = {contract: defaultContract(), preview: null, reviewConfirmed: false};
  populateImportControls(draft);

  $("starter-project-file").addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    $("starter-project-status").textContent = "读取中...";
    try {
      draft = {
        contract: defaultContract(),
        project: {name: file.name, base64: arrayBufferToBase64(await file.arrayBuffer())},
        preview: null,
        reviewConfirmed: false,
      };
      await writeDraft(draft);
      populateImportControls(draft);
      $("starter-project-status").textContent = `${file.name} · ${formatFileSize(file.size)}`;
    } catch (error) {
      setMessage(error.message || "项目读取失败", "error");
    }
  });

  for (const id of ["starter-solver-entrypoint", "starter-target-file", "starter-solver-command"]) {
    $(id).addEventListener("input", async () => {
      draft = draftFromImportControls(draft);
      draft.contract.edited = true;
      draft.preview = null;
      draft.reviewConfirmed = false;
      await writeDraft(draft);
    });
  }
  $("starter-use-project-instances").addEventListener("change", async () => {
    draft = draftFromImportControls(draft);
    draft.preview = null;
    draft.reviewConfirmed = false;
    await writeDraft(draft);
  });

  $("clear-starter-project").addEventListener("click", async () => {
    await deleteDraft();
    draft = {contract: defaultContract(), preview: null, reviewConfirmed: false};
    $("starter-project-file").value = "";
    populateImportControls(draft);
    setMessage("");
  });

  $("continue-starter-project").addEventListener("click", async () => {
    if (!draft.project?.base64) return;
    const button = $("continue-starter-project");
    button.disabled = true;
    button.textContent = "正在检查...";
    setMessage("正在安全读取 ZIP 结构...");
    try {
      draft = draftFromImportControls(draft);
      const response = await fetch("/api/starter-projects/preview", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          starter_project: draft.project,
          starter_solver_entrypoint: draft.contract.entrypoint,
          starter_target_file: draft.contract.targetFile,
          starter_solver_command: draft.contract.solverCommand,
          starter_use_project_instances: draft.contract.useProjectInstances,
          auto_detect_contract: !draft.contract.edited,
        }),
      });
      const preview = await response.json();
      if (!response.ok) throw new Error(preview.error || response.statusText);
      draft.preview = preview;
      draft.reviewConfirmed = false;
      if (preview.contract?.auto_detected) {
        draft.contract.targetFile = preview.contract.target_file || draft.contract.targetFile;
        draft.contract.solverCommand = preview.contract.solver_command || draft.contract.solverCommand;
      }
      await writeDraft(draft);
      window.location.assign("/projects/import/review");
    } catch (error) {
      setMessage(`检查失败：${error.message || "无法读取项目"}`, "error");
      button.disabled = false;
      button.textContent = "检查项目结构";
    }
  });
}

function renderReviewCheck(id, valid, successText, failureText) {
  const element = $(id);
  element.textContent = valid ? successText : failureText;
  element.className = `review-check ${valid ? "valid" : "invalid"}`;
}

function renderReviewPage(draft) {
  const preview = draft.preview;
  const contract = preview.contract || {};
  $("review-project-name").textContent = preview.name || draft.project?.name || "-";
  $("review-project-stats").textContent = `${preview.file_count || 0} 个文件 · ${formatFileSize(preview.expanded_bytes)}`;
  $("review-project-root").textContent = preview.stripped_root || "无";
  $("review-file-count").textContent = `${preview.file_count || 0} 个文件`;
  $("review-entrypoint").textContent = contract.entrypoint || "-";
  $("review-target-file").textContent = contract.target_file || "-";
  $("review-command").textContent = contract.solver_command || "-";
  const detection = contract.detection || {};
  $("review-detected-target").textContent = detection.recommended_target_file || "未识别";
  $("review-detection-reason").textContent = detection.detection_reason || "-";
  $("review-project-instances").textContent = (preview.project_instances || []).map((item) => item.path).join("、") || "未发现";
  $("review-project-instances-status").textContent = contract.use_project_instances ? "正式 Core 输入" : "仅作项目材料";
  $("review-project-instances-status").className = `review-check ${contract.use_project_instances ? "valid" : "neutral"}`;
  renderReviewCheck("review-entrypoint-status", contract.entrypoint_exists, "文件存在", "文件不存在");
  renderReviewCheck("review-target-status", contract.target_exists, "文件存在", "文件不存在");
  renderReviewCheck("review-command-status", contract.command_valid, "入口参数匹配", "入口参数不匹配");
  renderReviewCheck("review-detection-status", !detection.entrypoint_is_wrapper || contract.target_file !== contract.entrypoint, detection.entrypoint_is_wrapper ? "已避开包装器" : "入口含实现", "错误指向包装器");
  $("review-contract-status").textContent = preview.can_continue ? "可确认" : "需要修正";
  $("review-contract-status").className = `status-pill ${preview.can_continue ? "confirmed" : "failed"}`;
  $("confirm-starter-project-review").disabled = !preview.can_continue;
  $("review-file-tree").innerHTML = (preview.files || []).map((file) => {
    const depth = Math.min(Math.max(String(file.path || "").split("/").length - 1, 0), 6);
    return `<li class="${file.sensitive ? "sensitive" : ""}" style="--tree-depth: ${depth}"><span>${escapeHtml(file.path || "-")}</span><small>${escapeHtml(String(file.suffix || "file").replace(/^\./, "").toUpperCase())} · ${escapeHtml(formatFileSize(file.size))}</small></li>`;
  }).join("");
  const blocks = [];
  if (preview.errors?.length) blocks.push(`<div class="review-issue-block error"><strong>需要修正</strong><ul>${preview.errors.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`);
  if (preview.warnings?.length) blocks.push(`<div class="review-issue-block warning"><strong>请人工确认</strong><ul>${preview.warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`);
  if (!blocks.length) blocks.push('<div class="review-issue-block success"><strong>自动预检通过</strong><span>请确认入口、编辑范围和命令符合项目实际约定。</span></div>');
  $("review-issues").innerHTML = blocks.join("");
}

async function initReviewPage() {
  const draft = await readDraft();
  if (!draft?.project || !draft?.preview) {
    window.location.replace("/projects/import");
    return;
  }
  renderReviewPage(draft);
  $("confirm-starter-project-review").addEventListener("click", async () => {
    if (!draft.preview?.can_continue) return;
    draft.reviewConfirmed = true;
    await writeDraft(draft);
    window.location.assign("/projects/import/setup");
  });
}

function selectedProvider(model) {
  return String(model || "").trim().split("/", 1)[0].toLowerCase();
}

function setSelectValue(select, value) {
  if (!select || !value) return;
  if (![...select.options].some((option) => option.value === value)) {
    select.add(new Option(value, value));
  }
  select.value = value;
}

function setupValues() {
  return {
    title: $("project-job-title").value.trim(),
    objective: $("project-evolution-objective").value.trim(),
    mainModel: $("project-main-model").value,
    mainVariant: $("project-main-variant").value,
    workerModel: $("project-worker-model").value,
    workerVariant: $("project-worker-variant").value,
    maxRounds: Number($("project-max-rounds").value || 6),
    competingWorkers: Number($("project-competing-workers").value || 3),
    mainSubagents: Number($("project-main-subagents").value || 4),
    maxWorkers: Number($("project-max-workers").value || 2),
    seeds: $("project-seeds").value || DEFAULT_SEEDS,
    timeout: Number($("project-timeout").value || 60),
    workerRuntime: Number($("project-worker-runtime").value || 420),
    workerSteps: Number($("project-worker-steps").value || 4),
    repairAttempts: Number($("project-repair-attempts").value || 3),
    promotionRepeats: Number($("project-promotion-repeats").value || 1),
    pauseRounds: $("project-pause-rounds").checked,
  };
}

function applySetupValues(values, draft, demo) {
  const defaults = demo.config || {};
  $("project-job-title").value = values?.title || `${draft.project.name.replace(/\.zip$/i, "")} 现有 solver 演进`;
  $("project-evolution-objective").value = values?.objective || "保持现有 CLI、输出协议和合法性，从审核通过的 solver 基线继续演进，并降低正式 Core 算例 makespan。";
  setSelectValue($("project-main-model"), values?.mainModel || defaults.main_agent_model || defaults.opencode_model);
  $("project-main-variant").value = values?.mainVariant ?? defaults.main_agent_variant ?? "";
  setSelectValue($("project-worker-model"), values?.workerModel || defaults.coding_worker_model || defaults.opencode_model);
  $("project-worker-variant").value = values?.workerVariant ?? defaults.coding_worker_variant ?? "";
  $("project-max-rounds").value = values?.maxRounds ?? 6;
  $("project-competing-workers").value = values?.competingWorkers ?? 3;
  $("project-main-subagents").value = values?.mainSubagents ?? 4;
  $("project-max-workers").value = values?.maxWorkers ?? defaults.max_workers ?? 2;
  $("project-seeds").value = values?.seeds || defaults.seeds || DEFAULT_SEEDS;
  $("project-timeout").value = values?.timeout ?? defaults.timeout_seconds ?? 60;
  $("project-worker-runtime").value = values?.workerRuntime ?? defaults.worker_max_runtime_seconds ?? 420;
  $("project-worker-steps").value = values?.workerSteps ?? defaults.worker_max_steps ?? 4;
  $("project-repair-attempts").value = values?.repairAttempts ?? defaults.in_round_repair_attempts ?? 3;
  $("project-promotion-repeats").value = values?.promotionRepeats ?? defaults.promotion_repeats ?? 1;
  $("project-pause-rounds").checked = values?.pauseRounds ?? defaults.pause_between_rounds ?? true;
}

function renderProviderStatus(status) {
  const models = [$("project-main-model").value, $("project-worker-model").value];
  const missing = models.filter((model) => !status?.provider_keys?.[selectedProvider(model)]);
  const target = $("project-provider-status");
  target.textContent = missing.length ? "模型凭据缺失" : "模型可用";
  target.className = `status-pill ${missing.length ? "failed" : "confirmed"}`;
  return missing;
}

async function initSetupPage() {
  const draft = await readDraft();
  if (!draft?.project || !draft?.preview) {
    window.location.replace("/projects/import");
    return;
  }
  if (!draft.reviewConfirmed) {
    window.location.replace("/projects/import/review");
    return;
  }
  const [demoResponse, providerResponse] = await Promise.all([
    fetch("/api/examples"),
    fetch("/api/deepseek-status"),
  ]);
  const demo = await demoResponse.json();
  const providerStatus = providerResponse.ok ? await providerResponse.json() : null;
  $("setup-project-name").textContent = draft.project.name;
  $("setup-target-file").textContent = draft.preview.contract?.target_file || draft.contract.targetFile;
  $("setup-project-instances").textContent = (draft.preview.project_instances || []).map((item) => item.path).join("、") || "使用外部算例";
  $("setup-solver-command").textContent = draft.preview.contract?.solver_command || draft.contract.solverCommand;
  applySetupValues(draft.setup, draft, demo);
  renderProviderStatus(providerStatus);
  for (const id of ["project-main-model", "project-worker-model"]) {
    $(id).addEventListener("change", () => renderProviderStatus(providerStatus));
  }
  $("project-job-form").addEventListener("change", async () => {
    draft.setup = setupValues();
    await writeDraft(draft);
  });
  $("project-job-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = setupValues();
    const missingProviders = renderProviderStatus(providerStatus);
    if (missingProviders.length) {
      setMessage(`缺少模型 provider 凭据：${[...new Set(missingProviders.map(selectedProvider))].join("、")}`, "error");
      return;
    }
    const button = $("start-project-job");
    button.disabled = true;
    button.textContent = "正在启动...";
    setMessage("正在固化项目基线与任务契约...");
    const objectiveSection = values.objective
      ? `\n\n## 已有项目本轮补充目标\n\n${values.objective}`
      : "";
    const payload = {
      title: values.title,
      requirement: {name: demo.requirement.name, text: demo.requirement.text + objectiveSection},
      io: demo.io,
      instance: demo.instance,
      best_known_csv: {name: "best_known.csv", text: ""},
      starter_project: draft.project,
      starter_solver_entrypoint: draft.preview.contract?.entrypoint || draft.contract.entrypoint,
      starter_target_file: draft.preview.contract?.target_file || draft.contract.targetFile,
      starter_solver_command: draft.preview.contract?.solver_command || draft.contract.solverCommand,
      starter_use_project_instances: draft.preview.contract?.use_project_instances !== false,
      max_rounds: values.maxRounds,
      seeds: values.seeds,
      max_workers: values.maxWorkers,
      coding_backend: "opencode",
      main_agent_model: values.mainModel,
      main_agent_variant: values.mainVariant,
      coding_worker_model: values.workerModel,
      coding_worker_variant: values.workerVariant,
      main_max_subagents: values.mainSubagents,
      max_competing_workers: values.competingWorkers,
      timeout_seconds: values.timeout,
      worker_max_steps: values.workerSteps,
      worker_max_runtime_seconds: values.workerRuntime,
      in_round_repair_attempts: values.repairAttempts,
      promotion_repeats: values.promotionRepeats,
      pause_between_rounds: values.pauseRounds,
    };
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || response.statusText);
      await deleteDraft();
      window.location.assign(`/?job=${encodeURIComponent(job.id)}`);
    } catch (error) {
      setMessage(`启动失败：${error.message || "未知错误"}`, "error");
      button.disabled = false;
      button.textContent = "从现有 solver 启动演进";
    }
  });
}

async function initializeProjectPage() {
  const step = document.body.dataset.projectStep;
  if (step === "import") await initImportPage();
  else if (step === "review") await initReviewPage();
  else if (step === "setup") await initSetupPage();
}

initializeProjectPage().catch((error) => {
  setMessage(error.message || "项目页面初始化失败", "error");
});
