import { appendFileSync } from "node:fs";

const displayLanguagePolicy = [
  "输出语言约定：应用的使用者使用简体中文。请使用简体中文组织本次分析与说明。",
  "本约定同时适用于返回给应用的推理文本（reasoning/reasoning_content）、阶段说明、子任务描述、最终报告和 JSON 中的自然语言值。即使输入材料或旧会话使用英文，后续自然语言分析与说明也使用简体中文。",
  "代码、命令、文件路径、变量和函数名、API 名称、JSON 字段名、固定枚举值、方法及技能标识符和工具返回的原始日志保持原样，不得翻译或改写；解释这些内容时使用中文。",
  "遵守原有输出格式和任务预算，不额外调用模型或工具进行翻译。",
].join("\n");
const taskLanguageReminder = "【语言要求】请用简体中文进行本次分析，并用中文返回推理文本和说明。代码、命令、路径、标识符、JSON 字段名和固定枚举值保持原样。";

export const ReasoningCapture = async () => {
  const outputPath = process.env.ALGOFORGE_REASONING_EVENTS_PATH;
  const usagePath = process.env.ALGOFORGE_USAGE_EVENTS_PATH;
  const metadata = JSON.parse(process.env.ALGOFORGE_USAGE_METADATA || "{}");
  if (!outputPath && !usagePath) return {};
  const calls = new Map();
  const usageEvent = (type, sessionID, messageID, extra = {}) => {
    if (!usagePath || !sessionID || !messageID) return;
    const key = `${sessionID}:${messageID}`;
    const base = {...metadata, request_id: `opencode:${key}`, backend: "opencode",
      session_id: sessionID, message_id: messageID};
    if (!calls.has(key)) {
      calls.set(key, {usage: null, finished: false});
      appendFileSync(usagePath, JSON.stringify({...base, type: "request_started", timestamp: Date.now() / 1000}) + "\n", "utf8");
    }
    const state = calls.get(key);
    if (type === "request_started") return;
    if (type === "request_usage") {
      if (!extra.usage || typeof extra.usage !== "object") return;
      const fingerprint = JSON.stringify({usage: extra.usage, role: extra.role || metadata.role, model: extra.model || metadata.model});
      if (fingerprint === state.usage) return;
      state.usage = fingerprint;
    }
    if (type === "request_finished") {
      if (state.finished) return;
      state.finished = true;
    }
    appendFileSync(usagePath, JSON.stringify({...base, type, timestamp: Date.now() / 1000, ...extra}) + "\n", "utf8");
  };
  const parts = new Map();
  let writable = true;
  const emit = (event) => {
    if (!outputPath || !writable) return;
    try {
      appendFileSync(outputPath, JSON.stringify({ ...event, timestamp: Date.now() }) + "\n", "utf8");
    } catch {
      writable = false;
    }
  };
  const flush = (state) => {
    if (!state.pending) return;
    emit({ type: "reasoning_delta", sessionID: state.sessionID, partID: state.id, delta: state.pending });
    state.pending = "";
    state.flushedAt = Date.now();
  };
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      if (output.system.some((text) => text.includes(displayLanguagePolicy))) return;
      if (output.system.length) output.system[0] += "\n\n" + displayLanguagePolicy;
      else output.system.push(displayLanguagePolicy);
    },
    "experimental.chat.messages.transform": async (_input, output) => {
      const latestUser = output.messages.findLast((message) => message.info?.role === "user");
      const textPart = latestUser?.parts.find((part) => part.type === "text" && !part.synthetic && typeof part.text === "string");
      if (textPart && !textPart.text.includes(taskLanguageReminder)) textPart.text += "\n\n" + taskLanguageReminder;
    },
    event: async ({ event }) => {
      const type = event.type.replace(/\.\d+$/, "");
      const properties = event.properties || {};
      if (type === "message.updated" && properties.info?.role === "assistant") {
        const info = properties.info;
        let role = metadata.role || "other";
        if (info.agent === "compaction" || info.summary === true) role = "compaction";
        else if (info.agent && !["algoforge-main", "algoforge-worker"].includes(info.agent)) role = "subagent";
        const extra = {model: info.modelID || metadata.model, provider: info.providerID, role};
        usageEvent("request_started", info.sessionID, info.id);
        const tokens = info.tokens || {};
        const measured = Object.values(tokens).some((x) => typeof x === "number" && x > 0)
          || Object.values(tokens.cache || {}).some((x) => typeof x === "number" && x > 0);
        // Initial assistant messages contain placeholder zero counters, even
        // when the eventual request fails. They are not provider usage.
        if (measured) usageEvent("request_usage", info.sessionID, info.id, {...extra, usage: tokens});
        if (info.finish || info.time?.completed || info.error) {
          usageEvent("request_finished", info.sessionID, info.id,
            {...extra, outcome: info.error ? "request_error" : "response_received"});
        }
      }
      if (type === "message.part.updated" && properties.part?.type === "step-start") {
        const part = properties.part;
        usageEvent("request_started", part.sessionID, part.messageID);
      }
      if (type === "message.part.updated" && properties.part?.type === "step-finish") {
        const part = properties.part;
        usageEvent("request_usage", part.sessionID, part.messageID, {usage: part.tokens});
        usageEvent("request_finished", part.sessionID, part.messageID, {outcome: "response_received"});
      }
      if (type === "message.part.updated" && properties.part?.type === "reasoning") {
        const part = properties.part;
        let state = parts.get(part.id);
        if (!state) {
          state = { id: part.id, sessionID: part.sessionID, pending: "", flushedAt: Date.now() };
          parts.set(part.id, state);
          emit({ type: "reasoning", sessionID: part.sessionID, part: {
            id: part.id, type: "reasoning", text: part.text || "", time: part.time,
          } });
        }
        if (part.time?.end) {
          flush(state);
          emit({ type: "reasoning", sessionID: part.sessionID, part: {
            id: part.id, type: "reasoning", text: part.text || "", time: part.time,
          } });
        }
      }
      if (type === "message.part.delta" && properties.field === "text") {
        const state = parts.get(properties.partID);
        if (!state || state.sessionID !== properties.sessionID || typeof properties.delta !== "string") return;
        state.pending += properties.delta;
        if (state.pending.length >= 256 || Date.now() - state.flushedAt >= 300) flush(state);
      }
    },
    dispose: async () => {
      for (const state of parts.values()) flush(state);
    },
  };
};
