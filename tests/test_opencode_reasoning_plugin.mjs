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
  console.log("Reasoning plugin: increments before completion, final flush and session filtering passed.");
} finally {
  delete process.env.ALGOFORGE_REASONING_EVENTS_PATH;
  rmSync(root, { recursive: true, force: true });
}
