import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ReviewQueuePanel from "./ReviewQueuePanel";

const getQueue = vi.fn();

vi.mock("../../api/client", () => ({
  getRequirementMatchReviewQueue: (...args: unknown[]) => getQueue(...args),
}));

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onOpenMatch = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ReviewQueuePanel projectId="proj-1" onOpenMatch={onOpenMatch} />
    </QueryClientProvider>,
  );
  return { onOpenMatch };
}

describe("ReviewQueuePanel", () => {
  beforeEach(() => {
    getQueue.mockReset();
    getQueue.mockResolvedValue({
      counts: {
        pending: 1,
        confirmed: 0,
        rejected: 0,
        needs_more_material: 0,
        total: 1,
        by_match_status: { supported: 1 },
        by_risk_level: { medium: 1 },
      },
      items: [
        {
          id: "m1",
          project_id: "proj-1",
          requirement_id: "r1",
          status: "supported",
          review_status: "pending",
          risk_level: "medium",
          needs_review: true,
          is_review_protected: false,
          review_lock_version: 0,
          lifecycle_status: "active",
          requirement_title: "资质要求",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
      total: 1,
      page: 1,
      limit: 20,
      offset: 0,
      include_superseded: false,
    });
  });

  it("loads queue with pending + include_superseded=false by default", async () => {
    renderPanel();
    await waitFor(() => expect(getQueue).toHaveBeenCalled());
    expect(getQueue).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({
        review_status: "pending",
        include_superseded: false,
      }),
    );
    expect(await screen.findByTestId("review-queue-panel")).toBeTruthy();
    expect(await screen.findByText("资质要求")).toBeTruthy();
  });

  it("toggles history and refetches with include_superseded=true", async () => {
    const user = userEvent.setup();
    renderPanel();
    await waitFor(() => expect(getQueue).toHaveBeenCalled());
    const toggle = await screen.findByTestId("review-queue-history-toggle");
    await user.click(toggle);
    await waitFor(() =>
      expect(getQueue).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ include_superseded: true }),
      ),
    );
  });

  it("opens detail when row title clicked", async () => {
    const user = userEvent.setup();
    const { onOpenMatch } = renderPanel();
    const title = await screen.findByText("资质要求");
    await user.click(title);
    expect(onOpenMatch).toHaveBeenCalledWith("m1");
  });
});
