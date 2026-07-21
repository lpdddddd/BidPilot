/** Pure helpers for grounded ask SSE / citation UI (unit-tested). */

export function parseSseChunk(buffer: string): {
  events: Array<{ event: string; data: string }>;
  rest: string;
} {
  const events: Array<{ event: string; data: string }> = [];
  const parts = buffer.split("\n");
  const rest = parts.pop() ?? "";
  let currentEvent = "message";
  let dataLines: string[] = [];
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

export function extractCitationIds(text: string): string[] {
  const matches = text.match(/\[(S\d+)\]/g) ?? [];
  return matches.map((m) => m.slice(1, -1));
}

export function shouldIgnoreStaleSession(active: number, current: number): boolean {
  return active !== current;
}
