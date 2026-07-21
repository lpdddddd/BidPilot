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

const sse = parseSseChunk(
  'event: retrieval\ndata: {"status":"ok"}\n\nevent: delta\ndata: {"text":"hi"}\n\npartial',
);
assert.equal(sse.events.length, 2);
assert.equal(sse.events[0].event, "retrieval");
assert.equal(sse.events[1].event, "delta");
assert.equal(sse.rest, "partial");
assert.deepEqual(extractCitationIds("见 [S1] 与 [S2]"), ["S1", "S2"]);
assert.equal(shouldIgnoreStaleSession(1, 2), true);
assert.equal(shouldIgnoreStaleSession(3, 3), false);
console.log("askUtils tests ok");
