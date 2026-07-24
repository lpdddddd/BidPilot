/** Pure helpers for model catalog status (Dashboard / Ask / Evaluation). */

import type { ModelCatalogItem } from "../../api/client";

export const BASE_MODEL_ID = "qwen3-8b-base";
export const COURSE_LORA_MODEL_ID = "qwen3-8b-lora-course";

export const CAP_GROUNDED_QA = "grounded_qa";
export const CAP_STRUCTURED_EXTRACTION = "structured_extraction";
export const CAP_AGENT_PIPELINE = "agent_pipeline";

/** Fallback when evaluation-capabilities omits required_capability. */
export const TARGET_REQUIRED_CAPABILITY_FALLBACK: Record<string, string> = {
  rag: CAP_GROUNDED_QA,
  extraction: CAP_STRUCTURED_EXTRACTION,
  agent_pipeline: CAP_AGENT_PIPELINE,
};

export function requiredCapabilityForTarget(
  targetType: string,
  fromApi?: string | null,
): string | undefined {
  if (fromApi) return fromApi;
  return TARGET_REQUIRED_CAPABILITY_FALLBACK[targetType];
}

export type ModelStatusFields = Pick<
  ModelCatalogItem,
  | "served"
  | "adapter_exists"
  | "model_type"
  | "registered"
  | "display_name"
  | "reason_codes"
  | "capabilities"
>;

/**
 * Truthful Chinese status for model chips (系统状态 / 实验区).
 * Never claim 「可用」 unless served === true.
 */
export function modelOnlineStatusLabel(item: ModelStatusFields): string {
  if (item.served) return "可用";
  const codes = item.reason_codes || [];
  if (codes.includes("base_model_mismatch")) {
    return "微调权重与基座模型不匹配";
  }
  if (codes.includes("base_model_unverified")) {
    return "无法确认微调权重与基座是否匹配";
  }
  if (item.model_type === "lora") {
    if (item.adapter_exists) {
      return "已注册 · 权重已就绪 · 推理服务未启动";
    }
    return "权重尚未就绪（文件缺失或不完整）";
  }
  return "推理服务未启动";
}

export function modelHasCapability(item: ModelCatalogItem, capability: string): boolean {
  const caps = item.capabilities || [];
  return caps.includes(capability);
}

export function pickBaseModel(items: ModelCatalogItem[]): ModelCatalogItem | undefined {
  return items.find((m) => m.model_type === "base") ?? items.find((m) => m.model_id === BASE_MODEL_ID);
}

export function pickCourseLora(items: ModelCatalogItem[]): ModelCatalogItem | undefined {
  return (
    items.find((m) => m.model_id === COURSE_LORA_MODEL_ID) ??
    items.find((m) => m.model_type === "lora" && m.train_track === "course_pilot") ??
    items.find((m) => m.model_type === "lora")
  );
}

export function modelsForCapability(
  items: ModelCatalogItem[],
  capability: string,
): ModelCatalogItem[] {
  return items.filter((m) => modelHasCapability(m, capability));
}

export function modelSelectLabel(item: ModelCatalogItem): string {
  const kind = item.model_type === "lora" ? "领域适配" : "基础模型";
  const raw = item.display_name || item.model_id;
  const name =
    item.model_type === "lora"
      ? raw.replace(/Course\s*LoRA/gi, "领域适配").replace(/\s{2,}/g, " ").trim() || "领域适配模型"
      : raw;
  if (item.served) return `${name}（${kind} · 可用）`;
  if (item.model_type === "lora" && !item.served) {
    return `${name}（${kind} · 推理服务未启动）`;
  }
  return `${name}（${kind}）`;
}

/** Compact Ask result line: actual served model (never invent LoRA when Base ran). */
export function formatAskGenerationModelLine(trace: {
  served_model_name?: string | null;
  model?: string | null;
  resolved_model_id?: string | null;
  requested_model_id?: string | null;
  fallback_used?: boolean | null;
}): string {
  const served = trace.served_model_name || trace.model || "—";
  const resolved = trace.resolved_model_id ? ` · ${trace.resolved_model_id}` : "";
  const requested =
    trace.requested_model_id &&
    trace.requested_model_id !== trace.resolved_model_id
      ? `（请求 ${trace.requested_model_id}）`
      : "";
  const fallback = trace.fallback_used ? " · 已回退基座" : "";
  return `${served}${resolved}${requested}${fallback}`;
}
