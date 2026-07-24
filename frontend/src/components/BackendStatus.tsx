import { Badge, Drawer, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getHealth, getReady, listModels } from "../api/client";
import {
  modelOnlineStatusLabel,
  pickBaseModel,
  pickCourseLora,
} from "../features/models/modelStatus";

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

function quietConnectionLabel(
  health: ReturnType<typeof useBackendHealth>,
): { status: "success" | "error" | "processing"; text: string } {
  if (health.isLoading) return { status: "processing", text: "连接中" };
  if (health.isSuccess) return { status: "success", text: "服务可用" };
  return { status: "error", text: "服务不可用" };
}

function quietReadyLabel(
  ready: ReturnType<typeof useBackendReady>,
): { status: "success" | "warning" | "error" | "default"; text: string } {
  if (ready.isSuccess) {
    if (ready.data.status === "ok") return { status: "success", text: "运行正常" };
    if (ready.data.status === "degraded") return { status: "warning", text: "部分能力受限" };
    return { status: "error", text: "运行异常" };
  }
  if (ready.isError) return { status: "error", text: "状态检查失败" };
  return { status: "default", text: "状态未知" };
}

type Props = {
  forceOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
};

/** System verification drawer — product surfaces keep tech detail here. */
export default function BackendStatus({
  forceOpen,
  onOpenChange,
  hideTrigger = false,
}: Props = {}) {
  const [open, setOpen] = useState(false);
  const health = useBackendHealth();
  const ready = useBackendReady();
  const models = useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    enabled: open || Boolean(forceOpen),
    retry: 0,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (typeof forceOpen === "boolean") setOpen(forceOpen);
  }, [forceOpen]);

  const setBoth = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };

  const conn = quietConnectionLabel(health);
  const run = quietReadyLabel(ready);
  const base = models.data ? pickBaseModel(models.data.items) : undefined;
  const lora = models.data ? pickCourseLora(models.data.items) : undefined;
  const services = ready.data?.services ?? [];

  return (
    <>
      {!hideTrigger && (
        <button
          type="button"
          className="bp-sys-trigger"
          onClick={() => setBoth(true)}
          aria-label="打开系统状态"
        >
          <Badge status={conn.status === "processing" ? "processing" : conn.status} />
          <span>系统状态</span>
        </button>
      )}

      <Drawer
        title="系统验证"
        placement="right"
        width={360}
        open={open}
        onClose={() => setBoth(false)}
        destroyOnClose={false}
      >
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <section className="bp-sys-block">
            <h3 className="bp-sys-block-title">连接</h3>
            <p className="bp-sys-line">
              <Badge status={conn.status === "processing" ? "processing" : conn.status} text={conn.text} />
            </p>
            <p className="bp-sys-line">
              <Badge status={run.status} text={run.text} />
            </p>
          </section>

          <section className="bp-sys-block">
            <h3 className="bp-sys-block-title">依赖服务</h3>
            {ready.isLoading && <Typography.Text type="secondary">检查中…</Typography.Text>}
            {ready.isError && (
              <Typography.Text type="danger">{(ready.error as Error).message}</Typography.Text>
            )}
            {services.length > 0 ? (
              <ul className="bp-sys-list">
                {services.map((s) => (
                  <li key={s.name}>
                    <span>{s.name}</span>
                    <span className={`bp-sys-pill is-${s.status}`}>
                      {s.status === "ok" ? "正常" : String(s.status) === "degraded" ? "降级" : "异常"}
                    </span>
                    {s.detail && s.status !== "ok" ? (
                      <span className="bp-sys-detail">{s.detail}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              !ready.isLoading && (
                <Typography.Text type="secondary">暂无依赖明细</Typography.Text>
              )
            )}
          </section>

          <section className="bp-sys-block">
            <h3 className="bp-sys-block-title">推理服务</h3>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8, fontSize: 12 }}>
              仅供排查。业务页面不展示实现细节。
            </Typography.Paragraph>
            {models.isLoading && <Typography.Text type="secondary">加载中…</Typography.Text>}
            {models.isError && (
              <Typography.Text type="danger">{(models.error as Error).message}</Typography.Text>
            )}
            {models.isSuccess && (
              <ul className="bp-sys-list">
                <li>
                  <span>基础模型</span>
                  <span className="bp-sys-pill">
                    {base ? modelOnlineStatusLabel(base) : "未登记"}
                  </span>
                </li>
                <li>
                  <span>领域适配</span>
                  <span className="bp-sys-pill">
                    {lora ? modelOnlineStatusLabel(lora) : "未登记"}
                  </span>
                </li>
              </ul>
            )}
          </section>
        </Space>
      </Drawer>
    </>
  );
}
