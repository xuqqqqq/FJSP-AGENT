// Provider receipts are authoritative. Never estimate tokens from streamed text.
function usageNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}

function usageEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function usageRole(role) {
  return {main: "Main 规划", coding_worker: "Coding Worker", semantic_review: "语义审查",
    reflection: "轮后反思", subagent: "子 Agent", compaction: "上下文压缩", other: "其他调用"}[role] || role || "其他调用";
}

function tokenUsageText(usage) {
  if (!usage?.available) return "暂无运行期计量记录";
  const parts = [];
  if (usage.active_calls || usage.active_invocations) parts.push("模型调用进行中，收到 usage 后更新");
  if (usage.missing_usage_calls) parts.push(`${usage.missing_usage_calls} 次已结束调用缺少完整 usage`);
  if (usage.untracked_invocations) parts.push(`${usage.untracked_invocations} 次执行尚无调用回执`);
  if (usage.invalid_receipts) parts.push("有计量记录无法解析");
  if (!usage.call_count && !parts.length) parts.push("尚未发生模型调用");
  if (!parts.length) parts.push("已取得全部已观察调用的用量回执");
  return parts.join("；");
}

function tokenRoundUsage(job, index) {
  return job?.token_usage?.by_round?.find((row) => row.round_index === index);
}

function usageRoundLabel(index) {
  return index === null || index === undefined ? "基线 / 准备" : `第 ${index + 1} 轮`;
}

function usageRows(rows, label) {
  return rows.map((row) => `<tr><td>${usageEscape(label(row))}</td>
    <td>${usageNumber(row.recorded_total_tokens)}</td><td>${row.metered_calls ?? 0} / ${row.call_count ?? 0}</td>
    <td>${row.pending_usage_calls ?? 0} 待回执 · ${row.missing_usage_calls ?? 0} 缺失</td></tr>`).join("");
}

function renderTokenUsage(job) {
  const usage = job.token_usage || {};
  const total = usageNumber(usage.recorded_total_tokens);
  const badge = document.getElementById("workspace-token-usage");
  if (badge) {
    badge.textContent = `已记录 Token ${total}`;
    badge.title = tokenUsageText(usage);
  }
  const metric = document.getElementById("metric-tokens");
  if (metric) metric.textContent = total;
  const panel = document.getElementById("token-usage-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  document.getElementById("token-usage-total").textContent = total;
  document.getElementById("token-usage-status").textContent = tokenUsageText(usage);
  document.getElementById("token-usage-status").className = usage.completeness === "partial" ? "usage-warning" : "muted";
  document.getElementById("token-usage-calls").textContent = `${usage.metered_calls ?? 0} / ${usage.call_count ?? 0} 次调用已计量`;
  const fields = [["input_tokens", "输入"], ["output_tokens", "输出"], ["reasoning_tokens", "思考"],
    ["cache_read_tokens", "缓存读取"], ["cache_write_tokens", "缓存写入"]];
  document.getElementById("token-usage-fields").innerHTML = fields.map(([key, title]) =>
    `<div><span>${title}</span><strong>${usageNumber(usage[key])}</strong></div>`).join("");
  document.getElementById("token-usage-roles").innerHTML = usageRows(usage.by_role || [], (row) => usageRole(row.role));
  document.getElementById("token-usage-rounds").innerHTML = usageRows(usage.by_round || [], (row) => usageRoundLabel(row.round_index));
  document.getElementById("token-usage-candidates").innerHTML = usageRows(
    (usage.by_candidate || []).filter((row) => row.candidate_id),
    (row) => `${usageRoundLabel(row.round_index)} · ${row.candidate_id}`,
  );
  const recent = [...(usage.recent_calls || [])].reverse().slice(0, 10);
  document.getElementById("token-usage-recent").innerHTML = recent.map((call) => {
    const active = call.active ?? (!call.finished && ["running", "waiting_for_user", "stopping"].includes(job.status));
    const state = call.total_tokens !== null && call.total_tokens !== undefined
      ? `${usageNumber(call.total_tokens)} Token${active ? " · 进行中" : ""}` : active ? "等待 usage" : "用量未返回";
    return `<li><span>${usageEscape(usageRole(call.role))} · ${usageEscape(usageRoundLabel(call.round_index))}</span>
      <strong>${usageEscape(state)}</strong><small>${usageEscape(call.model || "")}</small></li>`;
  }).join("");
}

function tokenTimelineItems(job) {
  return (job.token_usage?.recent_calls || []).filter((call) => call.finished && call.total_tokens != null).map((call) => ({
    type: call.role === "coding_worker" ? "coding" : "main",
    id: `token:${call.request_id}:${call.total_tokens}`,
    timestamp: Number(call.timestamp || 0) * 1000,
    value: {kind: "usage", attempt: usageRoundLabel(call.round_index), timestamp: Number(call.timestamp || 0) * 1000,
      text: `${usageRole(call.role)} · 本次调用 ${usageNumber(call.total_tokens)} Token`,
      id: `token:${call.request_id}:${call.total_tokens}`, agent_key: call.candidate_id || call.role},
  }));
}
