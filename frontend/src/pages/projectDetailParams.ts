/** Parse / sync project detail deep-link query params (citations → documents). */

export type ProjectDocumentFocus = {
  tab: string;
  documentId: string | null;
  page: number | null;
  chunkId: string | null;
  evidenceLinkId: string | null;
};

const DOC_TABS = new Set([
  "overview",
  "documents",
  "search",
  "requirements",
  "matching",
  "proposal-drafts",
  "agent-loop",
]);

export function parseProjectSearchParams(
  params: URLSearchParams,
): ProjectDocumentFocus {
  const tabRaw = params.get("tab") || "overview";
  const tab = DOC_TABS.has(tabRaw) ? tabRaw : "overview";
  const documentId =
    params.get("document_id") || params.get("documentId") || null;
  const pageRaw = params.get("page");
  let page: number | null = null;
  if (pageRaw != null && pageRaw !== "") {
    const n = Number(pageRaw);
    if (Number.isFinite(n) && n >= 1) page = Math.floor(n);
  }
  const chunkId = params.get("chunk_id") || params.get("chunkId") || null;
  const evidenceLinkId =
    params.get("evidence_link_id") || params.get("evidenceLinkId") || null;
  return { tab, documentId, page, chunkId, evidenceLinkId };
}

export function buildProjectSearchParams(focus: {
  tab?: string;
  documentId?: string | null;
  page?: number | null;
  chunkId?: string | null;
  evidenceLinkId?: string | null;
}): URLSearchParams {
  const params = new URLSearchParams();
  if (focus.tab && focus.tab !== "overview") params.set("tab", focus.tab);
  if (focus.documentId) {
    params.set("document_id", focus.documentId);
    params.set("documentId", focus.documentId);
  }
  if (focus.page != null) params.set("page", String(focus.page));
  if (focus.chunkId) params.set("chunk_id", focus.chunkId);
  if (focus.evidenceLinkId) params.set("evidence_link_id", focus.evidenceLinkId);
  return params;
}
