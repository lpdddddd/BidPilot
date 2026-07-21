import assert from "node:assert/strict";

function parseSseChunk(buffer) {
  const events = [];
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  let currentEvent = "message";
  let dataLines = [];
  for (const line of parts) {
    if (line.startsWith("event:")) {
      currentEvent = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    } else if (line === "") {
      if (dataLines.length) {
        events.push({ event: currentEvent, data: dataLines.join("\n") });
        dataLines = [];
        currentEvent = "message";
      }
    }
  }
  return { events, rest };
}

function extractCitationIds(text) {
  const matches = text.match(/\[(S\d+)\]/g) ?? [];
  return matches.map((m) => m.slice(1, -1));
}

function shouldIgnoreStaleSession(active, current) {
  return active !== current;
}

/** Mirrors GroundedAsk error handling: clear draft on error. */
function applyAskErrorState(state) {
  return {
    ...state,
    answerText: "",
    citations: [],
    result: null,
    error: state.errorMessage || "问答失败",
    phase: "error",
  };
}

/** Only final may populate confirmed answer text. */
function applyAskFinalState(state, finalResult) {
  return {
    ...state,
    answerText: finalResult.answer,
    citations: finalResult.citations,
    result: finalResult,
    error: null,
    phase: "done",
  };
}

const sse = parseSseChunk(
  'event: retrieval\ndata: {"status":"ok"}\n\nevent: generation_started\ndata: {"message":"核验"}\n\nevent: final\ndata: {"result":{"answer":"ok [S1]"}}\n\npartial',
);
assert.equal(sse.events.length, 3);
assert.equal(sse.events[0].event, "retrieval");
assert.equal(sse.events[1].event, "generation_started");
assert.equal(sse.events[2].event, "final");
assert.equal(sse.rest, "partial");

// delta must not be required for a valid stream
assert.ok(!sse.events.some((e) => e.event === "delta"));

assert.deepEqual(extractCitationIds("见 [S1] 与 [S2]"), ["S1", "S2"]);
assert.equal(shouldIgnoreStaleSession(1, 2), true);
assert.equal(shouldIgnoreStaleSession(3, 3), false);

const afterError = applyAskErrorState({
  answerText: "草稿 [S99]",
  citations: [{ source_id: "S1" }],
  result: { answer: "草稿" },
  errorMessage: "未知引用",
  phase: "verifying",
});
assert.equal(afterError.answerText, "");
assert.deepEqual(afterError.citations, []);
assert.equal(afterError.result, null);
assert.equal(afterError.phase, "error");

const afterFinal = applyAskFinalState(
  { answerText: "", citations: [], result: null, error: "x", phase: "verifying" },
  { answer: "结论 [S1]", citations: [{ source_id: "S1" }] },
);
assert.equal(afterFinal.answerText, "结论 [S1]");
assert.equal(afterFinal.phase, "done");
assert.equal(afterFinal.error, null);

console.log("askUtils tests ok");
