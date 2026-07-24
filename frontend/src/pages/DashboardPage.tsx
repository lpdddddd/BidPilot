import { Alert, Button, Skeleton } from "antd";
import { ArrowRightOutlined, PlusOutlined, UploadOutlined } from "@ant-design/icons";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listProjects } from "../api/client";
import type { Project } from "../types/api";
import { usePageTitle } from "../components/usePageTitle";

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  parsing: "解析中",
  analyzing: "分析中",
  reviewing: "审查中",
  completed: "已完成",
  archived: "已归档",
};

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "上午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

function formatDateLabel(now = new Date()): string {
  return now.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });
}

function deadlineMs(p: Project): number | null {
  if (!p.bid_deadline) return null;
  const t = new Date(p.bid_deadline).getTime();
  return Number.isNaN(t) ? null : t;
}

function riskLevel(p: Project, now: number): "high" | "medium" | "low" {
  const t = deadlineMs(p);
  if (t != null && t < now) return "high";
  if (t != null && t - now < 7 * 86400000) return "medium";
  if (p.status === "draft") return "medium";
  return "low";
}

function progressOf(p: Project): number {
  const map: Record<string, number> = {
    draft: 18,
    parsing: 32,
    analyzing: 48,
    reviewing: 72,
    completed: 100,
    archived: 100,
  };
  return map[p.status] ?? 20;
}

function formatDeadline(p: Project): string {
  const t = deadlineMs(p);
  if (t == null) return "未设定截止";
  return new Date(t).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function relativeDeadline(p: Project, now: number): string | null {
  const t = deadlineMs(p);
  if (t == null) return null;
  const diff = t - now;
  const day = 86400000;
  if (diff < 0) return `已逾期 ${Math.ceil(-diff / day)} 天`;
  const d = Math.ceil(diff / day);
  if (d <= 1) return "即将截止";
  return `${d} 天后截止`;
}

type Focus = {
  project: Project;
  title: string;
  advice: string;
  reason: string;
  risk: "high" | "medium" | "low";
};

function pickFocus(projects: Project[], now: number): Focus | null {
  if (!projects.length) return null;
  const ranked = [...projects].sort((a, b) => {
    const score = (p: Project) => {
      const r = riskLevel(p, now);
      const base = r === "high" ? 0 : r === "medium" ? 1 : 2;
      const t = deadlineMs(p) ?? Number.POSITIVE_INFINITY;
      return base * 1e15 + t;
    };
    return score(a) - score(b);
  });
  const p = ranked[0]!;
  const risk = riskLevel(p, now);
  if (risk === "high") {
    return {
      project: p,
      title: "投标截止已过，需要确认案卷状态",
      advice: "核对已完成材料，更新状态或归档，避免误用过期信息。",
      reason: "由截止时间触发",
      risk,
    };
  }
  if (risk === "medium" && deadlineMs(p) != null) {
    return {
      project: p,
      title: "截止临近，建议优先补齐证据与要求",
      advice: "进入项目核对文档完整性，并推进条款确认。",
      reason: "由临近截止触发",
      risk,
    };
  }
  if (p.status === "draft") {
    return {
      project: p,
      title: "草稿项目待推进",
      advice: "上传招标文件，生成要求清单，再进入证据与审查。",
      reason: "由项目状态触发",
      risk,
    };
  }
  return {
    project: p,
    title: "继续推进当前项目空间",
    advice: "从概览进入文件、要求或审查，保持案卷可追溯。",
    reason: "最近活跃项目",
    risk,
  };
}

export default function DashboardPage() {
  usePageTitle("工作台");
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects, retry: 0 });
  const now = useMemo(() => Date.now(), []);
  const items = projects.data?.items ?? [];
  const focus = useMemo(() => pickFocus(items, now), [items, now]);
  const attentionCount = items.filter((p) => riskLevel(p, now) !== "low").length;

  const dated = useMemo(() => {
    return items
      .filter((p) => deadlineMs(p) != null)
      .sort((a, b) => (deadlineMs(a) ?? 0) - (deadlineMs(b) ?? 0))
      .slice(0, 6);
  }, [items]);

  const activity = useMemo(() => {
    return [...items]
      .sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at))
      .slice(0, 6)
      .map((p) => ({
        id: p.id,
        title: `${p.project_name} 最近有更新`,
        detail: `状态 ${STATUS_LABELS[p.status] ?? p.status} · ${new Date(p.updated_at).toLocaleString("zh-CN", { hour12: false })}`,
        href: `/projects/${p.id}`,
      }));
  }, [items]);

  return (
    <div className="bp-space-home">
      <header className="bp-space-hero">
        <div>
          <p className="bp-space-date">{formatDateLabel()}</p>
          <h1 className="bp-space-greet">
            {greeting()}
            {projects.isSuccess
              ? attentionCount > 0
                ? `，今天有 ${attentionCount} 项内容值得关注`
                : "，项目空间运行平稳"
              : ""}
          </h1>
          <p className="bp-space-summary">
            先处理当前焦点，再进入项目空间继续文档、条款、证据与审查。
          </p>
        </div>
        <div className="bp-space-hero-actions">
          <Link to="/projects?create=1">
            <Button type="primary" size="large" icon={<PlusOutlined />}>
              新建投标项目
            </Button>
          </Link>
          <Link to="/projects">
            <Button size="large" icon={<UploadOutlined />}>
              导入招标文件
            </Button>
          </Link>
        </div>
      </header>

      {projects.isError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 20 }}
          message="项目加载失败"
          description={(projects.error as Error).message}
          action={
            <Button size="small" onClick={() => projects.refetch()}>
              重试
            </Button>
          }
        />
      )}

      <section className="bp-focus" aria-label="当前焦点">
        {projects.isLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : focus ? (
          <div className={`bp-focus-panel is-${focus.risk}`}>
            <div className="bp-focus-copy">
              <p className="bp-focus-kicker">当前焦点</p>
              <h2>{focus.title}</h2>
              <p className="bp-focus-project">{focus.project.project_name}</p>
              <dl className="bp-focus-meta">
                <div>
                  <dt>截止</dt>
                  <dd>{formatDeadline(focus.project)}</dd>
                </div>
                <div>
                  <dt>风险</dt>
                  <dd>
                    {focus.risk === "high" ? "高" : focus.risk === "medium" ? "中" : "低"}
                  </dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{focus.reason}</dd>
                </div>
              </dl>
              <p className="bp-focus-advice">{focus.advice}</p>
              <Link to={`/projects/${focus.project.id}`}>
                <Button type="primary" icon={<ArrowRightOutlined />} iconPosition="end">
                  继续处理
                </Button>
              </Link>
            </div>
            <div className="bp-focus-visual" aria-hidden="true">
              <div className="bp-focus-orb" />
            </div>
          </div>
        ) : (
          <div className="bp-focus-empty">
            <h2>暂无待处理事项</h2>
            <p>创建一个投标项目，或导入招标文件开始整理案卷。</p>
            <Link to="/projects?create=1">
              <Button type="primary" icon={<PlusOutlined />}>
                新建投标项目
              </Button>
            </Link>
          </div>
        )}
      </section>

      <section className="bp-project-space" aria-label="项目空间">
        <div className="bp-section-row">
          <h2 className="bp-space-section-title">项目空间</h2>
          <Link to="/projects" className="bp-quiet-link">
            查看全部
          </Link>
        </div>
        {projects.isLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : items.length === 0 ? (
          <div className="bp-soft-empty">还没有项目。创建后将在此以空间卡片展示。</div>
        ) : (
          <div className="bp-project-rail">
            {items.slice(0, 8).map((p, idx) => {
              const risk = riskLevel(p, now);
              const prog = progressOf(p);
              return (
                <Link
                  key={p.id}
                  to={`/projects/${p.id}`}
                  className={`bp-project-card${idx === 0 ? " is-featured" : ""}`}
                >
                  <div className="bp-project-card-top">
                    <span className={`bp-risk-dot is-${risk}`} />
                    <span className="bp-project-status">{STATUS_LABELS[p.status] ?? p.status}</span>
                  </div>
                  <h3>{p.project_name}</h3>
                  <p className="bp-project-buyer">{p.purchaser || "招标单位未填写"}</p>
                  <div className="bp-project-progress">
                    <div className="bp-project-progress-track">
                      <span style={{ width: `${prog}%` }} />
                    </div>
                    <span>{prog}%</span>
                  </div>
                  <div className="bp-project-card-foot">
                    <span>{formatDeadline(p)}</span>
                    <span>{relativeDeadline(p, now) || "继续处理"}</span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </section>

      <div className="bp-home-split">
        <section aria-label="最近动态">
          <h2 className="bp-space-section-title">最近动态</h2>
          {projects.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : activity.length === 0 ? (
            <div className="bp-soft-empty">创建项目后，动态会出现在这里。</div>
          ) : (
            <ol className="bp-activity-stream">
              {activity.map((a) => (
                <li key={a.id}>
                  <Link to={a.href}>
                    <span className="bp-activity-title">{a.title}</span>
                    <span className="bp-activity-detail">{a.detail}</span>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-label="关键日期">
          <h2 className="bp-space-section-title">关键日期</h2>
          {dated.length === 0 ? (
            <div className="bp-soft-empty">项目设定投标截止后，将显示在时间带中。</div>
          ) : (
            <div className="bp-date-strip">
              {dated.map((p) => (
                <Link key={p.id} to={`/projects/${p.id}`} className="bp-date-chip">
                  <span className="bp-date-chip-when">{formatDeadline(p)}</span>
                  <span className="bp-date-chip-name">{p.project_name}</span>
                  <span className="bp-date-chip-kind">最终投标截止</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
