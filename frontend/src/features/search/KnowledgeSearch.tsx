import { useState } from "react";
import { Segmented } from "antd";
import GroundedAsk from "./GroundedAsk";
import EvidenceSearch from "./EvidenceSearch";

export default function KnowledgeSearch({
  projectId,
  onOpenSource,
}: {
  projectId: string;
  onOpenSource?: (documentId: string, chunkId?: string) => void;
}) {
  const [mode, setMode] = useState<"search" | "ask">("search");

  return (
    <div className="bp-knowledge-workspace">
      <div className="bp-mode-switch">
        <Segmented
          value={mode}
          onChange={(v) => setMode(v as "search" | "ask")}
          options={[
            { label: "检索证据", value: "search" },
            { label: "带来源问答", value: "ask" },
          ]}
        />
      </div>
      {mode === "search" ? (
        <EvidenceSearch projectId={projectId} onOpenSource={onOpenSource} />
      ) : (
        <GroundedAsk projectId={projectId} onOpenSource={onOpenSource} />
      )}
    </div>
  );
}
