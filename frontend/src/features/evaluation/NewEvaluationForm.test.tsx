import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import NewEvaluationForm from "./NewEvaluationForm";

const listModels = vi.fn();

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    listModels: (...args: unknown[]) => listModels(...args),
  };
});

const SUITE = {
  id: "suite-1",
  name: "reference_dataset",
  version: "1.0.0",
  dataset_hash: "abc",
  evaluator_profile_version: "bidpilot-eval-1.0.0",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const CAPS = {
  items: [
    { target_type: "rag", available: true, label: "RAG" },
    { target_type: "deterministic_fake", available: true, label: "确定性假目标" },
  ],
  evaluator_version: "bidpilot-eval-1.0.0",
  profiles: [{ id: "default", version: "bidpilot-eval-1.0.0", name: "默认" }],
  dataset: {
    name: "reference_dataset",
    version: "1.0.0",
    dataset_hash: "abc",
    direct_reference_coverage: 1,
  },
  task_families: ["rag"],
  splits: ["test"],
};

describe("NewEvaluationForm model select", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("passes model_id when creating a RAG run and disables unserved LoRA", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    listModels.mockResolvedValue({
      llm_enabled: true,
      default_model_id: "qwen3-8b-base",
      active_finetune_model_id: "qwen3-8b-lora-course",
      items: [
        {
          model_id: "qwen3-8b-base",
          display_name: "Qwen3-8B Base",
          model_type: "base",
          registered: true,
          adapter_exists: true,
          served: true,
          served_model_name: "bidpilot-qwen3-8b",
          version: "base",
          train_track: null,
          reason_codes: [],
          notes: null,
          status_label: "online",
        },
        {
          model_id: "qwen3-8b-lora-course",
          display_name: "BidPilot Course LoRA",
          model_type: "lora",
          registered: true,
          adapter_exists: true,
          served: false,
          served_model_name: "bidpilot-qwen3-8b-course-lora",
          version: "course-1.0",
          train_track: "course_pilot",
          reason_codes: [],
          notes: null,
          status_label: "adapter_ready",
        },
      ],
    });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <NewEvaluationForm
          suites={[SUITE]}
          capabilities={CAPS}
          submitting={false}
          error={null}
          onSubmit={onSubmit}
        />
      </QueryClientProvider>,
    );

    const target = screen.getByTestId("eval-target-select");
    await user.click(within(target).getByRole("combobox"));
    await user.click(await screen.findByText(/^RAG$/));

    expect(await screen.findByTestId("eval-model-select")).toBeTruthy();
    expect(await screen.findByTestId("eval-lora-unserved-hint")).toBeTruthy();

    await user.click(await screen.findByTestId("eval-start-btn"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.target).toBe("rag");
    expect(payload.target_config).toEqual({ model_id: "qwen3-8b-base" });
  });
});
