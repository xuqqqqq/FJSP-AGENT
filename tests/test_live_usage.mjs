import assert from "node:assert/strict";
import {mkdtempSync, readFileSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import vm from "node:vm";
import {ReasoningCapture} from "../harness_agent/workers/opencode_reasoning_plugin.mjs";

const root = mkdtempSync(join(tmpdir(), "usage-plugin-"));
try {
  const path = join(root, "usage.jsonl");
  process.env.ALGOFORGE_USAGE_EVENTS_PATH = path;
  process.env.ALGOFORGE_USAGE_METADATA = JSON.stringify({run_id: "run", invocation_id: "inv", role: "main", round_index: 0});
  const capture = await ReasoningCapture();
  const send = (type, properties) => capture.event({event: {type, properties}});
  await send("message.updated", {info: {id: "m1", sessionID: "s", role: "assistant", tokens: {total: 0, input: 0, output: 0}}});
  let events = readFileSync(path, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(events.filter(e => e.type === "request_usage").length, 0);
  await send("message.part.updated", {part: {type: "step-finish", sessionID: "s", messageID: "m1", tokens: {total: 100, input: 80, output: 20}}});
  await send("message.updated", {info: {id: "m1", sessionID: "s", role: "assistant", agent: "algoforge-main", finish: "stop", tokens: {total: 100, input: 80, output: 20}}});
  await send("message.updated", {info: {id: "m2", sessionID: "child", role: "assistant", agent: "specialist", finish: "stop", tokens: {total: 20}}});
  await send("message.updated", {info: {id: "m3", sessionID: "s", role: "assistant", error: {name: "timeout"}, tokens: {total: 0}}});
  events = readFileSync(path, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(events.filter(e => e.type === "request_started").length, 3);
  assert.equal(events.filter(e => e.type === "request_usage" && e.request_id === "opencode:s:m1").length, 1);
  assert.equal(events.find(e => e.type === "request_usage" && e.message_id === "m2").role, "subagent");
  assert.equal(events.filter(e => e.type === "request_usage" && e.message_id === "m3").length, 0);
  await capture.dispose();

  const elements = new Map();
  const sandbox = {document: {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {textContent: "", innerHTML: "", classList: {remove() {}}});
    return elements.get(id);
  }}};
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL("../harness_agent/web/static/usage.js", import.meta.url), "utf8"), sandbox);
  assert.equal(sandbox.usageNumber(null), "—");
  assert.equal(sandbox.usageNumber(0), "0");
  assert.match(sandbox.tokenUsageText({available: true, missing_usage_calls: 1}), /缺少/);
  sandbox.renderTokenUsage({status: "running", token_usage: {available: true, recorded_total_tokens: 1234,
    call_count: 2, metered_calls: 1, active_calls: 1, by_role: [{role: "<script>", recorded_total_tokens: 1234}],
    recent_calls: [{role: "main", model: "<img src=x>", round_index: 0}]}});
  assert.equal(elements.get("token-usage-total").textContent, "1,234");
  assert.ok(!elements.get("token-usage-roles").innerHTML.includes("<script>"));
  assert.ok(!elements.get("token-usage-recent").innerHTML.includes("<img"));
  assert.match(elements.get("token-usage-status").textContent, /进行中/);
  console.log("Live usage: plugin receipts, deduplication, missing usage, subagents and frontend updates passed.");
} finally {
  delete process.env.ALGOFORGE_USAGE_EVENTS_PATH;
  delete process.env.ALGOFORGE_USAGE_METADATA;
  rmSync(root, {recursive: true, force: true});
}
