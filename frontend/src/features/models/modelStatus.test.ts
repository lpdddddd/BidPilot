import { describe, expect, it } from "vitest";
import type { ModelCatalogItem } from "../../api/client";
import {
  CAP_GROUNDED_QA,
  CAP_STRUCTURED_EXTRACTION,
  formatAskGenerationModelLine,
  modelHasCapability,
  modelOnlineStatusLabel,
  modelSelectLabel,
  modelsForCapability,
  pickBaseModel,
  pickCourseLora,
} from "./modelStatus";

function item(partial: Partial<ModelCatalogItem> & Pick<ModelCatalogItem, "model_id" | "model_type">): ModelCatalogItem {
  return {
    display_name: partial.display_name ?? partial.model_id,
    registered: partial.registered ?? true,
    adapter_exists: partial.adapter_exists ?? true,
    served: partial.served ?? false,
    served_model_name: partial.served_model_name ?? null,
    version: partial.version ?? null,
    train_track: partial.train_track ?? null,
    reason_codes: partial.reason_codes ?? [],
    notes: partial.notes ?? null,
    status_label: partial.status_label ?? "unavailable",
    capabilities: partial.capabilities,
    ...partial,
  };
}

describe("modelOnlineStatusLabel", () => {
  it("shows mismatch Chinese copy", () => {
    expect(
      modelOnlineStatusLabel(
        item({
          model_id: "qwen3-8b-lora-course",
          model_type: "lora",
          served: false,
          adapter_exists: false,
          reason_codes: ["base_model_mismatch"],
        }),
      ),
    ).toBe("微调权重与基座模型不匹配");
  });

  it("shows 在线 only when served=true", () => {
    expect(
      modelOnlineStatusLabel(
        item({ model_id: "qwen3-8b-base", model_type: "base", served: true }),
      ),
    ).toBe("在线");
  });

  it("never claims LoRA online when only adapter is ready", () => {
    const label = modelOnlineStatusLabel(
      item({
        model_id: "qwen3-8b-lora-course",
        model_type: "lora",
        served: false,
        adapter_exists: true,
        registered: true,
      }),
    );
    expect(label).toBe("已注册 · Adapter 已就绪 · 当前未启动在线服务");
  });
});

describe("capabilities", () => {
  const catalog = [
    item({
      model_id: "qwen3-8b-base",
      model_type: "base",
      served: true,
      capabilities: [CAP_GROUNDED_QA, CAP_STRUCTURED_EXTRACTION],
    }),
    item({
      model_id: "qwen3-8b-lora-course",
      model_type: "lora",
      display_name: "BidPilot Course LoRA",
      train_track: "course_pilot",
      served: true,
      capabilities: [CAP_STRUCTURED_EXTRACTION],
    }),
  ];

  it("filters grounded ask models without Course LoRA", () => {
    const grounded = modelsForCapability(catalog, CAP_GROUNDED_QA);
    expect(grounded.map((m) => m.model_id)).toEqual(["qwen3-8b-base"]);
    expect(modelHasCapability(catalog[1]!, CAP_GROUNDED_QA)).toBe(false);
  });

  it("allows Course LoRA for structured extraction", () => {
    const structs = modelsForCapability(catalog, CAP_STRUCTURED_EXTRACTION);
    expect(structs).toHaveLength(2);
    expect(pickCourseLora(structs)?.model_id).toBe("qwen3-8b-lora-course");
    expect(pickBaseModel(structs)?.model_id).toBe("qwen3-8b-base");
  });

  it("labels unserved LoRA option", () => {
    expect(
      modelSelectLabel(
        item({
          model_id: "qwen3-8b-lora-course",
          model_type: "lora",
          served: false,
          display_name: "BidPilot Course LoRA",
        }),
      ),
    ).toMatch(/模型尚未启动在线服务/);
  });
});

describe("formatAskGenerationModelLine", () => {
  it("shows Base served name", () => {
    expect(
      formatAskGenerationModelLine({
        served_model_name: "bidpilot-qwen3-8b",
        resolved_model_id: "qwen3-8b-base",
        requested_model_id: "qwen3-8b-base",
        fallback_used: false,
      }),
    ).toBe("bidpilot-qwen3-8b · qwen3-8b-base");
  });
});
