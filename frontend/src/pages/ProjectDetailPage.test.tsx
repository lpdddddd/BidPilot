import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProjectDetailPage from "./ProjectDetailPage";
import { parseProjectSearchParams } from "./projectDetailParams";

const listDocuments = vi.fn();
const getChunkSummary = vi.fn();
const listDocumentChunks = vi.fn();
const getProject = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getProject: (...args: unknown[]) => getProject(...args),
    listDocuments: (...args: unknown[]) => listDocuments(...args),
    getChunkSummary: (...args: unknown[]) => getChunkSummary(...args),
    listDocumentChunks: (...args: unknown[]) => listDocumentChunks(...args),
  };
});

vi.mock("../features/agent/AgentLoopPanel", () => ({
  default: () => <div data-testid="agent-loop-stub">Agent</div>,
}));

vi.mock("../features/search/KnowledgeSearch", () => ({
  default: () => <div>search</div>,
}));
vi.mock("../features/requirements/RequirementsWorkspace", () => ({
  default: () => <div>requirements</div>,
}));
vi.mock("../features/matching/MatchingWorkspace", () => ({
  default: () => <div>matching</div>,
}));
vi.mock("../features/proposalDrafts/ProposalDraftsWorkspace", () => ({
  default: () => <div>drafts</div>,
}));

const DOC = {
  id: "doc-1",
  project_id: "proj-1",
  file_name: "招标文件.pdf",
  document_type: "tender",
  parse_status: "success",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  metadata_json: { chunking: { status: "success" }, indexing: { status: "success" } },
};

const CHUNK = {
  id: "chunk-abc",
  document_id: "doc-1",
  chunk_index: 2,
  content: "需具备一级资质",
  page_start: 3,
  page_end: 3,
  section: "3.1",
  token_count: 12,
  content_hash: "abc123",
  metadata_json: {},
};

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("projectDetailParams", () => {
  it("parses tab/document_id/page/chunk_id", () => {
    const p = parseProjectSearchParams(
      new URLSearchParams(
        "tab=documents&document_id=doc-1&page=3&chunk_id=chunk-abc",
      ),
    );
    expect(p.tab).toBe("documents");
    expect(p.documentId).toBe("doc-1");
    expect(p.page).toBe(3);
    expect(p.chunkId).toBe("chunk-abc");
  });
});

describe("ProjectDetailPage citation deep-link", () => {
  beforeEach(() => {
    getProject.mockReset();
    listDocuments.mockReset();
    getChunkSummary.mockReset();
    listDocumentChunks.mockReset();
    getProject.mockResolvedValue({
      id: "proj-1",
      project_name: "测试项目",
      project_code: "P-1",
      status: "draft",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
    listDocuments.mockResolvedValue({ items: [DOC], total: 1 });
    getChunkSummary.mockResolvedValue({
      status: "success",
      chunk_count: 1,
      section_count: 1,
      total_tokens: 12,
      chunker_version: "1",
      chunker_name: "c",
      tokenizer: "t",
    });
    listDocumentChunks.mockResolvedValue({ items: [CHUNK], total: 1 });
  });

  afterEach(() => {
    cleanup();
  });

  it("opens documents tab, document drawer, page and chunk from URL", async () => {
    renderAt(
      "/projects/proj-1?tab=documents&document_id=doc-1&page=3&chunk_id=chunk-abc",
    );
    expect(await screen.findByTestId("project-detail-page")).toBeTruthy();
    expect(await screen.findByTestId("documents-tab-panel")).toBeTruthy();
    expect(await screen.findByTestId("chunk-viewer-drawer")).toBeTruthy();
    expect((await screen.findByTestId("chunk-focus-page")).textContent).toContain("3");
    await waitFor(() => {
      const card = screen.getByTestId("chunk-card-chunk-abc");
      expect(card.getAttribute("data-highlighted")).toBe("true");
    });
  });

  it("shows safe alert for invalid document_id", async () => {
    renderAt("/projects/proj-1?tab=documents&document_id=missing-doc");
    const alert = await screen.findByTestId("source-link-alert");
    expect(alert.textContent).toMatch(/不存在|不属于/);
    expect(screen.queryByTestId("chunk-viewer-drawer")).toBeNull();
  });

  it("rejects cross-project document_id not in list", async () => {
    listDocuments.mockResolvedValue({ items: [], total: 0 });
    renderAt("/projects/proj-1?tab=documents&document_id=other-project-doc");
    expect(await screen.findByTestId("source-link-alert")).toBeTruthy();
  });

  it("restores focus after remount (refresh simulation)", async () => {
    const { unmount } = renderAt(
      "/projects/proj-1?tab=documents&document_id=doc-1&page=3&chunk_id=chunk-abc",
    );
    expect(await screen.findByTestId("chunk-viewer-drawer")).toBeTruthy();
    unmount();
    renderAt(
      "/projects/proj-1?tab=documents&document_id=doc-1&page=3&chunk_id=chunk-abc",
    );
    expect(await screen.findByTestId("chunk-viewer-drawer")).toBeTruthy();
    expect((await screen.findByTestId("chunk-focus-page")).textContent).toContain("3");
  });

  it("supports browser back/forward via MemoryRouter history", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[
            "/projects/proj-1",
            "/projects/proj-1?tab=documents&document_id=doc-1&page=3&chunk_id=chunk-abc",
          ]}
          initialIndex={1}
        >
          <Routes>
            <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("chunk-viewer-drawer")).toBeTruthy();
    const overviewTab = await screen.findByRole("tab", { name: "项目概览" });
    await user.click(overviewTab);
    await waitFor(() => {
      expect(overviewTab.getAttribute("aria-selected")).toBe("true");
    });
    const docsTab = await screen.findByRole("tab", { name: "文档中心" });
    await user.click(docsTab);
    await waitFor(() => {
      expect(docsTab.getAttribute("aria-selected")).toBe("true");
    });
    expect(await screen.findByTestId("documents-tab-panel")).toBeTruthy();
  });
});
