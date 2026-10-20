import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ReasoningCapture } from "../harness_agent/workers/opencode_reasoning_plugin.mjs";

const root = mkdtempSync(join(tmpdir(), "algoforge-reasoning-"));
try {
  const output = join(root, "reasoning.jsonl");
  process.env.ALGOFORGE_REASONING_EVENTS_PATH = output;
  const capture = await ReasoningCapture();
  const originalSystem = 'Use solve(input_path), --time-limit-sec and {"method_family":"exact_hybrid"} unchanged.';
  const systemOutput = { system: [originalSystem] };
  await capture["experimental.chat.system.transform"]({}, systemOutput);
  assert.equal(systemOutput.system.length, 1);
  assert.ok(systemOutput.system[0].startsWith(originalSystem));
  assert.ok(systemOutput.system[0].includes("推理文本（reasoning/reasoning_content）"));
  assert.ok(systemOutput.system[0].includes("简体中文"));
  assert.ok(systemOutput.system[0].includes("JSON 字段名、固定枚举值"));
  assert.ok(systemOutput.system[0].includes("不额外调用模型或工具进行翻译"));
  const firstSystem = systemOutput.system[0];
  await capture["experimental.chat.system.transform"]({}, systemOutput);
  assert.equal(systemOutput.system[0], firstSystem);
  const emptySystem = { system: [] };
  await capture["experimental.chat.system.transform"]({}, emptySystem);
  assert.equal(emptySystem.system.length, 1);
  assert.ok(emptySystem.system[0].includes("简体中文"));
  const currentTask = 'Implement solve(input_path) and return {"method_family":"exact_hybrid"}.';
  const historyOutput = { messages: [
    { info: { role: "user" }, parts: [{ type: "text", text: "Earlier task" }] },
    { info: { role: "assistant" }, parts: [{ type: "tool", text: "Original tool output" }] },
    { info: { role: "user" }, parts: [
      { type: "text", text: "Attachment source", synthetic: true },
      { type: "text", text: currentTask },
    ] },
  ] };
  await capture["experimental.chat.messages.transform"]({}, historyOutput);
  assert.equal(historyOutput.messages[0].parts[0].text, "Earlier task");
  assert.equal(historyOutput.messages[1].parts[0].text, "Original tool output");
  assert.equal(historyOutput.messages[2].parts[0].text, "Attachment source");
  const updatedTask = historyOutput.messages[2].parts[1].text;
  assert.ok(updatedTask.startsWith(currentTask));
  assert.ok(updatedTask.includes("用中文返回推理文本和说明"));
  await capture["experimental.chat.messages.transform"]({}, historyOutput);
  assert.equal(historyOutput.messages[2].parts[1].text, updatedTask);
  const syntheticOnly = { messages: [{ info: { role: "user" }, parts: [{ type: "text", text: "Original source", synthetic: true }] }] };
  await capture["experimental.chat.messages.transform"]({}, syntheticOnly);
  assert.equal(syntheticOnly.messages[0].parts[0].text, "Original source");
  await capture["experimental.chat.messages.transform"]({}, { messages: [] });
  const send = (type, properties) => capture.event({ event: { type, properties } });
  await send("message.part.updated.1", { part: {
    id: "tool", sessionID: "s", type: "tool", text: "unrelated source body",
  } });
  await send("message.part.delta", { sessionID: "s", partID: "tool", field: "text", delta: "private output" });
  await send("message.part.updated", { part: {
    id: "p", sessionID: "s", type: "reasoning", text: "", time: { start: 100 },
  } });
  await send("message.part.delta", { sessionID: "wrong", partID: "p", field: "text", delta: "wrong session" });
  await send("message.part.delta", { sessionID: "s", partID: "p", field: "text", delta: "a".repeat(300) });
  let records = readFileSync(output, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(records.length, 2);
  assert.equal(records[1].type, "reasoning_delta");
  assert.equal(records[1].delta.length, 300);
  assert.equal(records[0].part.time.end, undefined);
  await send("message.part.delta", { sessionID: "s", partID: "p", field: "text", delta: "tail" });
  await send("message.part.updated", { part: {
    id: "p", sessionID: "s", type: "reasoning", text: "a".repeat(300) + "tail", time: { start: 100, end: 200 },
  } });
  await capture.dispose();
  records = readFileSync(output, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(records.length, 4);
  assert.equal(records[2].delta, "tail");
  assert.equal(records[3].part.time.end, 200);
  assert.ok(!readFileSync(output, "utf8").includes("private output"));
  assert.ok(!readFileSync(output, "utf8").includes("wrong session"));
  console.log("Reasoning plugin: Chinese system policy, original technical text, increments, final flush and session filtering passed.");
} finally {
  delete process.env.ALGOFORGE_REASONING_EVENTS_PATH;
  rmSync(root, { recursive: true, force: true });
}
