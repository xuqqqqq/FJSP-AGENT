import { appendFileSync } from "node:fs";

export const ReasoningCapture = async () => {
  const outputPath = process.env.ALGOFORGE_REASONING_EVENTS_PATH;
  if (!outputPath) return {};
  const parts = new Map();
  let writable = true;
  const emit = (event) => {
    if (!writable) return;
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
    event: async ({ event }) => {
      const type = event.type.replace(/\.\d+$/, "");
      const properties = event.properties || {};
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
