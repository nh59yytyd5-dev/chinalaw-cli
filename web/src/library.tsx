import { useState } from "react";
import {
  api,
  changed,
  clausesOf,
  docTitle,
  formatDate,
  label,
  number,
  query,
  safeUrl,
  type Clause,
  type DocumentResult,
  type Draft,
  type DraftSummary,
  type Inventory,
  type Job,
  type Kind,
  type Options,
  type Origin,
  type System,
} from "./api";
import {
  Badge,
  Empty,
  Field,
  Heading,
  Icon,
  KindBadge,
  Loading,
  navigate,
  Note,
  openDocument,
  useAction,
  useResource,
} from "./components";

export function Overview() {
  const system = useResource<System>("/system", 10000);
  const pending = useResource<{ items: DraftSummary[] }>("/drafts", 10000);
  const jobs = useResource<{ items: Job[] }>("/jobs", 10000);
  if (!system.data) return <Loading {...system} />;
  const data = system.data;
  const cards = [
    {
      title: "公开法规",
      value: data.laws,
      note: "独立法规记录",
      icon: "globe",
      route: "law",
    },
    {
      title: "当前条文",
      value: data.articles,
      note: "完整入库的公开条文",
      icon: "file",
      route: "law",
    },
    {
      title: "私域规范",
      value: data.norm_sources,
      note: `${number(data.norm_clauses)} 条私域条款`,
      icon: "lock",
      route: "norm",
    },
    {
      title: "待确认资料",
      value: data.pending_drafts,
      note: "核对正文后确认入库",
      icon: "task",
      route: "import",
    },
  ];
  return (
    <>
      <Heading
        eyebrow="LIBRARY OVERVIEW"
        title="资料库概览"
        text="看清资料、核对来源，让每一次引用都有依据。"
      >
        <button className="button" onClick={() => navigate("import")}>
          <Icon name="plus" size={18} />
          导入资料
        </button>
      </Heading>
      <section className="stats-grid">
        {cards.map((card) => (
          <button
            className="stat-card"
            onClick={() => navigate(card.route)}
            key={card.title}
          >
            <span className="stat-label">
              {card.title}
              <Icon name={card.icon} />
            </span>
            <strong>{number(card.value)}</strong>
            <small>{card.note}</small>
          </button>
        ))}
      </section>
      <div className="overview-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>等待你的核对</h2>
              <p>确认前，当前资料库的正文不会改变。</p>
            </div>
            <Badge tone="amber">{data.pending_drafts} 项</Badge>
          </div>
          {!pending.data ? (
            <Loading {...pending} />
          ) : pending.data.items.length ? (
            <div className="item-list">
              {pending.data.items.slice(0, 6).map((item) => (
                <button
                  key={item.id}
                  className="list-row"
                  onClick={() => navigate("draft", item.id)}
                >
                  <span className="row-icon">
                    <Icon name="file" />
                  </span>
                  <span className="row-body">
                    <strong>{item.title}</strong>
                    <small>
                      {item.kind === "law" ? "公开法规" : "私域规范"} ·{" "}
                      {formatDate(item.created_at)}
                    </small>
                  </span>
                  <Icon name="arrow" size={17} />
                </button>
              ))}
            </div>
          ) : (
            <Empty title="暂无待确认资料">
              <p>
                导入文件或从官方来源获取资料，
                <br />
                在这里核对全文与差异。
              </p>
              <button
                className="text-button"
                onClick={() => navigate("import")}
              >
                添加第一份资料 <Icon name="arrow" size={16} />
              </button>
            </Empty>
          )}
        </section>
        <section className="panel coverage">
          <div className="panel-heading">
            <div>
              <h2>公开资料构成</h2>
              <p>
                {number(data.revisions)} 份版本记录 · {number(data.laws)} 部法规
              </p>
            </div>
          </div>
          {data.by_level.length ? (
            <div className="bars">
              {data.by_level.slice(0, 8).map((item) => (
                <div className="bar-row" key={item.level}>
                  <div>
                    <span>{label(item.level)}</span>
                    <b>{item.count}</b>
                  </div>
                  <div className="bar-track">
                    <i
                      style={{
                        width: `${Math.max(2, (item.count / Math.max(data.laws, 1)) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="资料库还是空的">
              <p>从导入资料开始建立你的资料库。</p>
            </Empty>
          )}
          <div className="panel-foot">版本记录与独立法规分别统计。</div>
        </section>
      </div>
      <section className="panel recent">
        <div className="panel-heading">
          <div>
            <h2>最近的维护任务</h2>
            <p>查看处理结果，接着完成上次的工作。</p>
          </div>
          <button className="text-button" onClick={() => navigate("jobs")}>
            全部任务 <Icon name="arrow" size={16} />
          </button>
        </div>
        {!jobs.data ? (
          <Loading {...jobs} />
        ) : jobs.data.items.length ? (
          jobs.data.items.slice(0, 4).map((job) => (
            <div className="list-row" key={job.id}>
              <Icon name="clock" />
              <span className="row-body">
                <strong>{job.result?.title || label(job.action)}</strong>
                <small>{job.message}</small>
              </span>
              <Badge
                tone={
                  job.state === "failed"
                    ? "red"
                    : job.state === "completed"
                      ? "green"
                      : ""
                }
              >
                {label(job.state)}
              </Badge>
              {job.draft_id && (
                <button
                  className="text-button"
                  onClick={() => navigate("draft", job.draft_id)}
                >
                  查看
                </button>
              )}
            </div>
          ))
        ) : (
          <div className="quiet-empty">
            还没有维护任务。每次导入和抓取的处理记录都会保留在这里。
          </div>
        )}
      </section>
      <div className="subtle-footer">
        <span className="status-dot" />
        资料保存在当前资料库中<span>资料结构 v{data.schema_version}</span>
      </div>
    </>
  );
}

export function InventoryPage({ kind }: { kind: Kind }) {
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const options = useResource<Options>("/options");
  const result = useResource<Inventory>(
    "/documents?" +
      query({ kind, q: search, category, status, source, page, page_size: 20 }),
  );
  const applySearch = () => {
    setSearch(input);
    setPage(1);
  };
  return (
    <>
      <Heading
        eyebrow={kind === "law" ? "PUBLIC SOURCES" : "PRIVATE SOURCES"}
        title={kind === "law" ? "公开法规" : "私域规范"}
        text={
          kind === "law"
            ? "按名称、类型和来源找到法规，查看完整入库条文。"
            : "单独管理制度、合同要求和其他私域资料，保留制定主体与约束范围。"
        }
      >
        <button className="button" onClick={() => navigate("import", kind)}>
          <Icon name="plus" size={18} />
          导入{kind === "law" ? "法规" : "规范"}
        </button>
      </Heading>
      <section className="panel inventory-panel">
        <form
          className="filters"
          onSubmit={(event) => {
            event.preventDefault();
            applySearch();
          }}
        >
          <div className="search-input">
            <Icon name="search" size={18} />
            <input
              aria-label="搜索资料名称"
              placeholder="搜索名称或简称…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </div>
          <select
            aria-label="规范类型"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部类型</option>
            {(kind === "law"
              ? options.data?.law_levels
              : options.data?.norm_types
            )?.map((item) => (
              <option key={item} value={item}>
                {label(item)}
              </option>
            ))}
          </select>
          {kind === "law" && (
            <select
              aria-label="法规状态"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部状态</option>
              {options.data?.law_statuses.map((item) => (
                <option key={item} value={item}>
                  {label(item)}
                </option>
              ))}
            </select>
          )}
          <input
            className="source-filter"
            aria-label="来源名称"
            placeholder="来源名称（精确）"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setPage(1);
            }}
          />
          <button className="button secondary" type="submit">
            查询
          </button>
        </form>
        {!result.data ? (
          <Loading {...result} />
        ) : (
          <>
            <div className="table-caption">
              共 <strong>{number(result.data.total)}</strong> 份资料
              <span>正文与版本可在详情页核对</span>
            </div>
            {result.data.items.length ? (
              <div className="table-scroll">
                <table className="inventory-table">
                  <thead>
                    <tr>
                      <th>资料名称</th>
                      <th>类型 / 状态</th>
                      <th>条文数</th>
                      <th>来源与取得时间</th>
                      <th>
                        <span className="sr-only">查看</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <button
                            className="document-title"
                            onClick={() => openDocument(kind, item.id)}
                          >
                            {item.title}
                          </button>
                          <small className="cell-subtitle">
                            {kind === "norm"
                              ? item.authority || "制定主体未提供"
                              : item.effective_at
                                ? `${item.effective_at} 起施行`
                                : "施行日期未提供"}
                          </small>
                        </td>
                        <td>
                          <span>{label(item.category)}</span>
                          <div className="cell-subtitle">
                            {kind === "law" ? (
                              <Badge
                                tone={
                                  item.status === "current"
                                    ? "green"
                                    : item.status === "repealed"
                                      ? "red"
                                      : ""
                                }
                              >
                                {label(item.status)}
                              </Badge>
                            ) : (
                              <KindBadge kind="norm" />
                            )}
                          </div>
                        </td>
                        <td className="tabular">{number(item.clause_count)}</td>
                        <td>
                          <span>{item.source_name || "未提供来源名称"}</span>
                          <small className="cell-subtitle">
                            {formatDate(item.source_checked_at)}
                          </small>
                        </td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`查看${item.title}`}
                            onClick={() => openDocument(kind, item.id)}
                          >
                            <Icon name="arrow" size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title={
                  search || category || status || source
                    ? "没有匹配的资料"
                    : "尚未添加资料"
                }
              >
                <p>调整筛选条件，或导入需要管理的文件。</p>
              </Empty>
            )}
            <div className="pagination">
              <span>
                第 {result.data.pages ? page : 0} / {result.data.pages} 页
              </span>
              <div>
                <button
                  className="button secondary compact"
                  disabled={page <= 1}
                  onClick={() => setPage((v) => v - 1)}
                >
                  上一页
                </button>
                <button
                  className="button secondary compact"
                  disabled={page >= result.data.pages}
                  onClick={() => setPage((v) => v + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}

export function FullText({
  clauses,
  title = "入库正文",
}: {
  clauses: Clause[];
  title?: string;
}) {
  const [filter, setFilter] = useState("");
  const [jump, setJump] = useState("");
  const filtered = clauses
    .map((clause, index) => ({ clause, index }))
    .filter(
      ({ clause }) =>
        !filter ||
        clause.text.includes(filter) ||
        clause.number_display?.includes(filter) ||
        clause.title?.includes(filter),
    );
  const parts = clauses
    .map((clause, index) => ({ part: clause.part, index }))
    .filter(
      (item, index, list) =>
        item.part && (index === 0 || item.part !== list[index - 1].part),
    );
  const scroll = (index: number) =>
    document
      .getElementById("clause-" + index)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <div className="reader-layout">
      <aside className="outline panel">
        <div className="panel-heading">
          <h3>章节目次</h3>
          <span>{clauses.length} 条</span>
        </div>
        <form
          className="jump-form"
          onSubmit={(event) => {
            event.preventDefault();
            const value = jump.replace(/[第条\s]/g, "");
            const index = clauses.findIndex(
              (clause) =>
                clause.number === value || clause.number_display === jump,
            );
            if (index >= 0) {
              setFilter("");
              requestAnimationFrame(() => scroll(index));
            }
          }}
        >
          <input
            aria-label="定位条号"
            placeholder="输入条号定位"
            value={jump}
            onChange={(e) => setJump(e.target.value)}
          />
          <button className="icon-button" aria-label="跳转条文">
            <Icon name="arrow" size={16} />
          </button>
        </form>
        <nav aria-label="章节目次">
          {parts.length ? (
            parts.map((item) => (
              <button key={item.index} onClick={() => scroll(item.index)}>
                {item.part}
              </button>
            ))
          ) : (
            <p className="muted">
              这份资料没有章节目次。
              <br />
              可以按条号定位或检索正文。
            </p>
          )}
        </nav>
      </aside>
      <section className="panel reader">
        <div className="reader-toolbar">
          <div>
            <strong>{title}</strong>
            <span>
              {filtered.length} / {clauses.length} 条
            </span>
          </div>
          <div className="search-input">
            <Icon name="search" size={16} />
            <input
              aria-label="筛选正文"
              placeholder="在全文中查找…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
        </div>
        <div className="articles">
          {filtered.length ? (
            filtered.map(({ clause, index }) => (
              <article id={"clause-" + index} className="article" key={index}>
                {clause.part &&
                  (index === 0 || clauses[index - 1]?.part !== clause.part) && (
                    <h2 className="part-title">{clause.part}</h2>
                  )}
                <div className="article-heading">
                  <span>
                    {clause.number_display ||
                      clause.number ||
                      `第 ${index + 1} 段`}
                  </span>
                  {clause.title && <strong>{clause.title}</strong>}
                </div>
                <p>{clause.text}</p>
              </article>
            ))
          ) : (
            <Empty title="正文中没有匹配内容">
              <p>试试其他关键词。</p>
            </Empty>
          )}
        </div>
      </section>
    </div>
  );
}

function ArtifactText({ id }: { id: string }) {
  const result = useResource<{ text: string; filename: string }>(
    `/artifacts/${id}/text`,
  );
  if (!result.data) return <Loading {...result} />;
  return <pre className="source-text">{result.data.text}</pre>;
}
export function SourcePanel({
  data,
  origin,
}: {
  data: DocumentResult["document"];
  origin?: Origin;
}) {
  const [showText, setShowText] = useState(false);
  const url = safeUrl(data.source_url);
  return (
    <section className="panel source-panel">
      <div className="panel-heading">
        <div>
          <h2>来源与原件</h2>
          <p>结合原件核对入库内容、条号和章节目次。</p>
        </div>
      </div>
      <dl className="metadata-grid">
        <div>
          <dt>来源名称</dt>
          <dd>{data.source_name || "未提供"}</dd>
        </div>
        <div>
          <dt>取得时间</dt>
          <dd>{formatDate(data.source_checked_at)}</dd>
        </div>
        <div className="wide">
          <dt>来源地址</dt>
          <dd>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                {url} <Icon name="link" size={14} />
              </a>
            ) : (
              data.source_url || "未提供"
            )}
          </dd>
        </div>
        <div className="wide">
          <dt>来源指纹</dt>
          <dd className="mono wrap">{data.source_hash || "未提供"}</dd>
        </div>
      </dl>
      {origin?.artifact_ids?.length ? (
        <div className="artifact-actions">
          {origin.artifact_ids.map((id, index) => (
            <a
              key={id}
              className="button secondary"
              href={`/api/v1/artifacts/${id}`}
              download
            >
              <Icon name="download" size={16} />
              {index === 0 ? "下载上传原件" : "下载提取文本"}
            </a>
          ))}
          {origin.text_artifact_id && (
            <button
              className="button secondary"
              onClick={() => setShowText((v) => !v)}
            >
              {showText ? "收起原文" : "查看原文文本"}
            </button>
          )}
        </div>
      ) : (
        <Note>
          这份记录未保存可下载的来源原件。可以通过来源地址核对；链接打开的是来源当前提供的内容。
        </Note>
      )}
      {showText && origin?.text_artifact_id && (
        <ArtifactText id={origin.text_artifact_id} />
      )}
    </section>
  );
}

type Revision = {
  id?: string;
  revision?: number;
  version_label?: string;
  released_at?: string;
  created_at?: string;
  clause_count?: number;
  effective_at?: string;
};
type Operation = {
  id: string;
  action: string;
  actor: string;
  created_at: string;
  before_fingerprint?: string;
  after_fingerprint: string;
  origin: Origin;
};
function History({
  kind,
  id,
  onRevision,
}: {
  kind: Kind;
  id: string;
  onRevision: (id: string) => void;
}) {
  const revisions = useResource<{ revisions: Revision[] }>(
    "/revisions?" + query({ kind, id }),
  );
  const operations = useResource<{ items: Operation[] }>(
    "/operations?" + query({ kind, id }),
  );
  const action = useAction();
  const restore = (operation: string, side: string) =>
    action.run(async () => {
      const draft = await api<Draft>(
        `/operations/${operation}/restore`,
        "POST",
        { side },
      );
      navigate("draft", draft.id);
    });
  return (
    <div className="history-grid">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>{kind === "law" ? "法规版本记录" : "导入版本记录"}</h2>
            <p>打开历史完整快照后，可以预览恢复。</p>
          </div>
        </div>
        {!revisions.data ? (
          <Loading {...revisions} />
        ) : revisions.data.revisions.length ? (
          <div className="item-list">
            {[...revisions.data.revisions].reverse().map((revision, i) => (
              <div className="list-row" key={i}>
                <span className="row-icon">
                  <Icon name="book" />
                </span>
                <span className="row-body">
                  <strong>
                    {revision.version_label || `版本 ${revision.revision}`}
                  </strong>
                  <small>
                    {formatDate(revision.created_at || revision.released_at)}
                    {revision.effective_at
                      ? ` · ${revision.effective_at} 起施行`
                      : ""}
                  </small>
                </span>
                <button
                  className="text-button"
                  onClick={() =>
                    onRevision(String(revision.id || revision.revision))
                  }
                >
                  查看全文
                </button>
              </div>
            ))}
          </div>
        ) : (
          <Empty title="暂无版本快照" />
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>人工维护记录</h2>
            <p>维护操作与法律修订分别记录。</p>
          </div>
        </div>
        {!operations.data ? (
          <Loading {...operations} />
        ) : operations.data.items.length ? (
          operations.data.items.map((operation) => (
            <div className="operation-row" key={operation.id}>
              <div>
                <Badge>{label(operation.action)}</Badge>
                <span className="muted">
                  {formatDate(operation.created_at)}
                </span>
              </div>
              <p className="mono muted">
                版本指纹 {operation.after_fingerprint.slice(0, 16)}…
              </p>
              <div className="button-row">
                {operation.before_fingerprint && (
                  <button
                    disabled={action.busy}
                    className="text-button"
                    onClick={() => void restore(operation.id, "before")}
                  >
                    预览恢复操作前内容
                  </button>
                )}
                <button
                  disabled={action.busy}
                  className="text-button"
                  onClick={() => void restore(operation.id, "after")}
                >
                  预览此操作版本
                </button>
              </div>
            </div>
          ))
        ) : (
          <Empty title="暂无人工维护记录">
            <p>已有 CLI 入库内容保留原有版本记录。</p>
          </Empty>
        )}
      </section>
    </div>
  );
}

export function DocumentPage({ kind, id }: { kind: Kind; id: string }) {
  const [tab, setTab] = useState("text");
  const [revision, setRevision] = useState("");
  const [note, setNote] = useState("");
  const result = useResource<DocumentResult>(
    (revision ? "/revision?" : "/document?") +
      query({ kind, id, revision: revision || undefined }),
  );
  const action = useAction();
  if (!result.data) return <Loading {...result} />;
  const data = result.data;
  const doc = data.document;
  const mark = () =>
    action.run(async () => {
      await api("/reviews", "POST", {
        kind,
        id,
        fingerprint: data.fingerprint,
        note,
      });
      changed();
      action.notify("已记录对这个内容版本的人工核对。");
    });
  return (
    <>
      <button
        className="breadcrumb"
        onClick={() => (revision ? setRevision("") : navigate(kind))}
      >
        <Icon name="back" size={16} />
        {revision ? "返回当前版本" : kind === "law" ? "公开法规" : "私域规范"}
      </button>
      <div className="document-heading">
        <div className="button-row">
          <KindBadge kind={kind} />
          {doc.status && <Badge>{label(doc.status)}</Badge>}
          <Badge tone={data.review ? "green" : "amber"}>
            {revision
              ? "历史快照"
              : data.review
                ? "已人工核对"
                : "尚未人工核对"}
          </Badge>
        </div>
        <h1>{docTitle(doc)}</h1>
        <p>
          {label(doc.level || doc.source_type)}
          <span>·</span>
          {clausesOf(doc).length} 条
          {doc.document_number && (
            <>
              <span>·</span>
              {doc.document_number}
            </>
          )}
        </p>
      </div>
      {kind === "norm" && (
        <Note>
          {doc.binding_note || "私域规范的约束范围以具体资料为准。"}
          <br />
          制定主体：{doc.authority || "未提供"}；约束范围：
          {doc.binding_scope || "未提供"}。
        </Note>
      )}
      {revision ? (
        <Note warning>
          正在查看历史快照。
          <button
            className="text-button"
            disabled={action.busy}
            onClick={() =>
              void action.run(async () => {
                const draft = await api<Draft>("/revisions/restore", "POST", {
                  kind,
                  id,
                  revision,
                });
                navigate("draft", draft.id);
              })
            }
          >
            预览恢复这个版本
          </button>
        </Note>
      ) : (
        <div className="review-bar">
          <div>
            <Icon name={data.review ? "check" : "task"} />
            <span>
              {data.review
                ? `人工核对时间：${formatDate(data.review.reviewed_at)}`
                : "核对全文与来源后，可为这个版本记录人工核对状态。"}
              {data.review?.note && <small>{data.review.note}</small>}
            </span>
          </div>
          <div className="button-row">
            <input
              aria-label="人工核对备注"
              placeholder="核对备注（可选）"
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
            />
            <button
              className="button secondary compact"
              disabled={action.busy}
              onClick={() => void mark()}
            >
              标记已核对
            </button>
          </div>
        </div>
      )}
      <div className="tabs" role="tablist">
        {[
          ["text", "完整正文"],
          ["source", "来源与原件"],
          ...(!revision ? [["history", "版本与维护记录"]] : []),
        ].map(([key, value]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            key={key}
            onClick={() => setTab(key)}
          >
            {value}
          </button>
        ))}
        {!revision && (
          <button
            className="text-button tab-action"
            onClick={() => navigate("import", `${kind}:${id}`)}
          >
            <Icon name="upload" size={16} />
            重新导入
          </button>
        )}
      </div>
      {tab === "text" && <FullText key={revision} clauses={clausesOf(doc)} />}
      {tab === "source" && (
        <SourcePanel
          data={doc}
          origin={revision ? undefined : data.latest_operation?.origin}
        />
      )}
      {tab === "history" && (
        <History
          kind={kind}
          id={id}
          onRevision={(value) => {
            setRevision(value);
            setTab("text");
          }}
        />
      )}
    </>
  );
}

export function SearchPage({ initial }: { initial: string }) {
  const [input, setInput] = useState(initial);
  const [search, setSearch] = useState(initial);
  type Hit = {
    id?: string;
    law_id?: string;
    law_title?: string;
    title?: string;
    norm_source_id?: string;
    source_id?: string;
    source_name?: string;
    norm_source_name?: string;
    name?: string;
    number_display?: string;
    snippet?: string;
    text?: string;
  };
  const result = useResource<{
    law_hits: Hit[];
    article_hits: Hit[];
    norm_source_hits: Hit[];
    norm_clause_hits: Hit[];
  }>("/search?" + query({ q: search, limit: 40 }));
  const rows = result.data
    ? [
        ...result.data.law_hits.map((hit) => ({
          hit,
          kind: "law" as Kind,
          id: hit.id || hit.law_id,
        })),
        ...result.data.article_hits.map((hit) => ({
          hit,
          kind: "law" as Kind,
          id: hit.law_id,
        })),
        ...result.data.norm_source_hits.map((hit) => ({
          hit,
          kind: "norm" as Kind,
          id: hit.id || hit.norm_source_id,
        })),
        ...result.data.norm_clause_hits.map((hit) => ({
          hit,
          kind: "norm" as Kind,
          id: hit.norm_source_id || hit.source_id,
        })),
      ]
    : [];
  return (
    <>
      <Heading
        title="全文检索"
        text="同时检索公开法规与私域资料；点开结果阅读完整正文。"
      />
      <form
        className="panel filters"
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(input);
        }}
      >
        <Field label="检索关键词">
          <input
            value={input}
            required
            maxLength={200}
            onChange={(e) => setInput(e.target.value)}
          />
        </Field>
        <button className="button">检索全文</button>
      </form>
      {!result.data ? (
        <Loading {...result} />
      ) : rows.length ? (
        <section className="panel search-results">
          {rows.map(({ hit, kind, id }, i) => (
            <div className="search-result" key={i}>
              <KindBadge kind={kind} />
              <button
                className="document-title"
                onClick={() => id && openDocument(kind, id)}
              >
                {hit.law_title ||
                  hit.title ||
                  hit.norm_source_name ||
                  hit.name ||
                  hit.source_name}{" "}
                {hit.number_display}
              </button>
              <p>{hit.snippet || hit.text || "打开资料查看正文与来源。"}</p>
            </div>
          ))}
        </section>
      ) : (
        <Empty title="未找到匹配内容">
          <p>尝试较短的关键词，或先将资料导入当前库。</p>
        </Empty>
      )}
    </>
  );
}
