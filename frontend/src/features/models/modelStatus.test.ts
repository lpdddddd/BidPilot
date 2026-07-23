import { describe, expect, it } from "vitest";
import type { ModelCatalogItem } from "../../api/client";
import {
  modelOnlineStatusLabel,
  modelSelectLabel,
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
    ...partial,
  };
}

describe("modelOnlineStatusLabel", () => {
  it("shows 在线 only when served=true", () => {
    expect(
      modelOnlineStatusLabel(
        item({ model_id: "qwen3-8b-base", model_type: "base", served: true }),
      ),
    ).toBe("在线");
    expect(
      modelOnlineStatusLabel(
        item({
          model_id: "qwen3-8b-lora-course",
          model_type: "lora",
          served: true,
          adapter_exists: true,
        }),
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
    expect(label).not.toBe("在线");
    expect(label.startsWith("在线")).toBe(false);
  });

  it("shows friendly missing-adapter copy for LoRA", () => {
    expect(
      modelOnlineStatusLabel(
        item({
          model_id: "qwen3-8b-lora-course",
          model_type: "lora",
          served: false,
          adapter_exists: false,
        }),
      ),
    ).toMatch(/Adapter 尚未就绪/);
  });
});

describe("pickBaseModel / pickCourseLora", () => {
  const catalog = [
    item({
      model_id: "qwen3-8b-base",
      model_type: "base",
      display_name: "Qwen3-8B Base",
      served: true,
    }),
    item({
      model_id: "qwen3-8b-lora-course",
      model_type: "lora",
      display_name: "BidPilot Course LoRA",
      train_track: "course_pilot",
      served: false,
      adapter_exists: true,
    }),
  ];

  it("picks base and course LoRA", () => {
    expect(pickBaseModel(catalog)?.model_id).toBe("qwen3-8b-base");
    expect(pickCourseLora(catalog)?.model_id).toBe("qwen3-8b-lora-course");
  });

  it("labels unserved LoRA option with helper phrase", () => {
    expect(modelSelectLabel(catalog[1]!)).toMatch(/模型尚未启动在线服务/);
  });
});
