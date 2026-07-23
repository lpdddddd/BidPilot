import { describe, expect, it } from "vitest";
import {
  compareMismatchWarnings,
  containsForbiddenLeak,
  capabilityOptionLabel,
  formatPercent,
  formatScore,
  friendlyCapabilityReason,
  isActiveEvaluationStatus,
  isTerminalEvaluationStatus,
  metricDisplayValue,
  parseEvaluationTab,
  sanitizeForDisplay,
  shortHash,
  validateEvaluationCitation,
} from "./evaluationParams";

describe("evaluationParams", () => {
  it("maps capability reason_codes to friendly Chinese (no raw codes)", () => {
    expect(
      friendlyCapabilityReason({
        available: false,
        reason_code: "service_not_wired",
        reason: "extraction case-level evaluation adapter is not wired",
      }),
    ).toBe("当前版本暂未开放");
    expect(
      friendlyCapabilityReason({
        available: false,
        reason_code: "provider_not_configured",
        reason: "LLM provider not configured",
      }),
    ).toBe("模型服务未配置");
    expect(
      friendlyCapabilityReason({
        available: false,
        reason_code: "project_dependency_missing:ConnectionError",
        reason: "Embedding/retrieval stack not available",
      }),
    ).toBe("检索依赖未就绪");
    expect(
      friendlyCapabilityReason({
        available: false,
        reason_code: "fake_disabled",
        reason: "deterministic_fake is not available",
      }),
    ).toBe("当前版本暂未开放");
    expect(
      capabilityOptionLabel({
        target_type: "extraction",
        available: false,
        label: "需求抽取",
        reason_code: "service_not_wired",
        reason: "not wired",
      }),
    ).toMatch(/不可用：当前版本暂未开放/);
    expect(
      capabilityOptionLabel({
        target_type: "extraction",
        available: false,
        label: "需求抽取",
        reason_code: "service_not_wired",
        reason: "not wired",
      }),
    ).not.toMatch(/service_not_wired|Exception|Error/);
  });
  it("parses tabs and terminal statuses", () => {
    expect(parseEvaluationTab("new")).toBe("new");
    expect(parseEvaluationTab("nope")).toBe("overview");
    expect(isTerminalEvaluationStatus("completed")).toBe(true);
    expect(isTerminalEvaluationStatus("running")).toBe(false);
    expect(isActiveEvaluationStatus("queued")).toBe(true);
  });

  it("formats scores and short hashes", () => {
    expect(formatScore(0.87654)).toBe("0.877");
    expect(formatScore(null)).toBe("—");
    expect(formatPercent(0.5)).toBe("50.0%");
    expect(shortHash("abcdef123456")).toBe("abcdef12…");
  });

  it("shows N/A for non-applicable metrics", () => {
    expect(
      metricDisplayValue({
        applicable: false,
        value: 1,
        reference_kind: "not_applicable",
      }),
    ).toBe("N/A");
    expect(
      metricDisplayValue({
        applicable: true,
        value: 0.42,
        reference_kind: "auto_reference",
      }),
    ).toBe("0.420");
  });

  it("sanitizes prompts, CoT and secrets from display", () => {
    const cleaned = sanitizeForDisplay({
      answer: "ok",
      prompt: "SECRET PROMPT",
      chain_of_thought: "think…",
      api_key: "sk-xxx",
      tool_params: { raw: true },
      reference_output: { gold: 1 },
      nested: { full_prompt: "nope", keep: 1 },
    }) as Record<string, unknown>;
    expect(cleaned.answer).toBe("ok");
    expect(cleaned.prompt).toBeUndefined();
    expect(cleaned.chain_of_thought).toBeUndefined();
    expect(cleaned.api_key).toBeUndefined();
    expect(cleaned.tool_params).toBeUndefined();
    expect(cleaned.reference_output).toBeUndefined();
    expect((cleaned.nested as Record<string, unknown>).full_prompt).toBeUndefined();
    expect((cleaned.nested as Record<string, unknown>).keep).toBe(1);
    expect(containsForbiddenLeak('"prompt": "x"')).toBe(true);
  });

  it("validates citation deep-links and marks invalid red cases", () => {
    const ok = validateEvaluationCitation("proj-1", {
      document_id: "doc-1",
      page: 3,
      chunk_id: "chunk-abc",
      valid: true,
    });
    expect(ok.valid).toBe(true);
    expect(ok.href).toContain("/projects/proj-1");
    expect(ok.href).toContain("chunk_id=chunk-abc");

    const cross = validateEvaluationCitation("proj-1", {
      document_id: "doc-1",
      project_id: "other",
    });
    expect(cross.valid).toBe(false);
    expect(cross.error).toMatch(/跨项目/);

    const serverBad = validateEvaluationCitation("proj-1", {
      document_id: "doc-1",
      valid: false,
      validation_error: "chunk 不匹配",
    });
    expect(serverBad.valid).toBe(false);
    expect(serverBad.error).toMatch(/chunk/);
  });

  it("detects compare dataset/evaluator mismatch warnings", () => {
    const w = compareMismatchWarnings([
      "dataset hash mismatch between left and right",
      "evaluator version differs",
    ]);
    expect(w.datasetHashMismatch).toBe(true);
    expect(w.evaluatorVersionMismatch).toBe(true);
  });
});
