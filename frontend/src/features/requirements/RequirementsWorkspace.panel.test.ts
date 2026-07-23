import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Lightweight source contract: empty requirements state must still mount the
 * structured clause panel (same component as the populated workspace).
 */
describe("RequirementsWorkspace empty + structured panel", () => {
  it("renders StructuredClausePanel in showEmpty and main branches", () => {
    const src = readFileSync(
      resolve(__dirname, "./RequirementsWorkspace.tsx"),
      "utf8",
    );
    expect(src).toContain("export function StructuredClausePanel");
    expect(src).toContain('data-testid="structured-clause-panel"');
    expect(src).toMatch(/if \(showEmpty\)[\s\S]*\{structuredPanel\}/);
    expect(src).toMatch(/bp-req-workspace[\s\S]*\{structuredPanel\}/);
  });
});
