/** Pure helpers for model catalog status (Dashboard / Ask / Evaluation). */

import type { ModelCatalogItem } from "../../api/client";

export const BASE_MODEL_ID = "qwen3-8b-base";
export const COURSE_LORA_MODEL_ID = "qwen3-8b-lora-course";

export type ModelStatusFields = Pick<
  ModelCatalogItem,
  "served" | "adapter_exists" | "model_type" | "registered" | "display_name" | "reason_codes"
>;

/**
 * Truthful Chinese status for Base / LoRA chips.
 * Never claim 「在线」 unless served === true.
 */
export function modelOnlineStatusLabel(item: ModelStatusFields): string {
  if (item.served) return "在线";
  const codes = item.reason_codes || [];
  if (codes.includes("base_model_mismatch")) {
    return "微调权重与基座模型不匹配";
  }
  if (codes.includes("base_model_unverified")) {
    return "无法确认微调权重与基座是否匹配";
  }
  if (item.model_type === "lora") {
    if (item.adapter_exists) {
      return "已注册 · Adapter 已就绪 · 当前未启动在线服务";
    }
    return "Adapter 尚未就绪（文件缺失或不完整）";
  }
  return "当前未启动在线服务";
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

export function modelSelectLabel(item: ModelCatalogItem): string {
  const kind = item.model_type === "lora" ? "Course LoRA" : "Base";
  const name = item.display_name || item.model_id;
  if (item.served) return `${name}（${kind} · 在线）`;
  if (item.model_type === "lora" && !item.served) {
    return `${name}（${kind} · 模型尚未启动在线服务）`;
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
