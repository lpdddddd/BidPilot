import { Badge, Tooltip } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getHealth, getReady } from "../api/client";

export function useBackendHealth() {
  return useQuery({
    queryKey: ["system", "health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: 0,
  });
}

export function useBackendReady() {
  return useQuery({
    queryKey: ["system", "ready"],
    queryFn: getReady,
    refetchInterval: 60_000,
    retry: 0,
  });
}

export default function BackendStatus() {
  const health = useBackendHealth();
  const ready = useBackendReady();

  const connected = health.isSuccess;
  const connectionText = health.isLoading ? "连接中" : connected ? "后端已连接" : "后端未连接";

  let readyStatus: "success" | "warning" | "error" | "default" = "default";
  let readyText = "依赖状态未知";
  if (ready.isSuccess) {
    if (ready.data.status === "ok") {
      readyStatus = "success";
      readyText = "依赖全部就绪";
    } else if (ready.data.status === "degraded") {
      readyStatus = "warning";
      readyText = "部分依赖未就绪";
    } else {
      readyStatus = "error";
      readyText = "依赖未就绪";
    }
  } else if (ready.isError) {
    readyStatus = "error";
    readyText = "依赖检查失败";
  }

  const failedServices = ready.data?.services.filter((s) => s.status === "error") ?? [];
  const readyTooltip =
    failedServices.length > 0
      ? failedServices.map((s) => `${s.name}: ${s.detail ?? "unavailable"}`).join("；")
      : readyText;

  return (
    <div className="bp-topbar-status" role="status" aria-label="后端连接状态">
      <Badge
        status={health.isLoading ? "processing" : connected ? "success" : "error"}
        text={connectionText}
      />
      <Tooltip title={readyTooltip}>
        <Badge status={readyStatus} text={readyText} />
      </Tooltip>
    </div>
  );
}
