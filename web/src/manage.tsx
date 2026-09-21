import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  changed,
  clausesOf,
  docTitle,
  formatDate,
  label,
  safeUrl,
  type Clause,
  type Document,
  type DocumentResult,
  type Draft,
  type DraftSummary,
  type Job,
  type Kind,
  type Options,
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
import { FullText, SourcePanel } from "./library";

export function ImportPage({ parameter = "" }: { parameter?: string }) {
  const initialKind = parameter.startsWith("norm") ? "norm" : "law";
  const target = parameter.includes(":")
    ? parameter.slice(parameter.indexOf(":") + 1)
    : "";
  const [kind, setKind] = useState<Kind>(initialKind);
  const [mode, setMode] = useState("file");
  const options = useResource<Options>("/options");
  const pending = useResource<{ items: DraftSummary[] }>("/drafts", 5000);
  // Re-imports replace an existing record: pre-fill its metadata so the owner
  // only has to pick the new file instead of retyping name, type and scope.
  const [existing, setExisting] = useState<Document | null | undefined>(
    target ? undefined : null,
  );
  useEffect(() => {
    if (!target) {
      setExisting(null);
      return;
    }
    let active = true;
    setExisting(undefined);
    api<DocumentResult>(
      `/document?kind=${initialKind}&id=${encodeURIComponent(target)}`,
    )
      .then((result) => active && setExisting(result.document))
      .catch(() => active && setExisting(null));
    return () => {
      active = false;
    };
  }, [target, initialKind]);
  return (
    <>
      <Heading
        eyebrow="IMPORT & REVIEW"
        title="导入与核对"
        text="取得资料后先核对完整正文与差异，再确认写入资料库。"
      />
      <div className="steps">
        <span className="active">
          <i>1</i>取得资料
        </span>
        <b />
        <span>
          <i>2</i>核对全文与差异
        </span>
        <b />
        <span>
          <i>3</i>确认入库
        </span>
      </div>
      {target && (
        <Note>
          正在重新导入已有资料。目标标识：
          <span className="mono wrap">{target}</span>；新旧正文将在确认前比较。
        </Note>
      )}
      <div className="import-grid">
        <section className="panel import-form">
          <div className="panel-heading">
            <h2>添加资料</h2>
          </div>
          <div className="segmented">
            {(["law", "norm"] as Kind[]).map((value) => (
              <button
                disabled={!!target}
                className={kind === value ? "active" : ""}
                onClick={() => {
                  setKind(value);
                  if (value === "norm") setMode("file");
                }}
                key={value}
              >
                <Icon name={value === "law" ? "globe" : "lock"} size={17} />
                {value === "law" ? "公开法规" : "私域规范"}
              </button>
            ))}
          </div>
          <div className="tabs inner">
            <button
              className={mode === "file" ? "active" : ""}
              onClick={() => setMode("file")}
            >
              上传文件
            </button>
            {kind === "law" && (
              <button
                className={mode === "fetch" ? "active" : ""}
                onClick={() => setMode("fetch")}
              >
                从来源获取
              </button>
            )}
          </div>
          {!options.data || existing === undefined ? (
            <Loading {...options} />
          ) : mode === "file" ? (
            <ImportFile
              key={kind}
              kind={kind}
              options={options.data}
              target={target}
              existing={existing}
            />
          ) : (
            <FetchForm options={options.data} />
          )}
        </section>
        <aside className="import-aside">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>待确认资料</h2>
                <p>关闭页面后，预览仍会保留。</p>
              </div>
            </div>
            {!pending.data ? (
              <Loading {...pending} />
            ) : pending.data.items.length ? (
              pending.data.items.map((item) => (
                <button
                  className="list-row"
                  key={item.id}
                  onClick={() => navigate("draft", item.id)}
                >
                  <Icon name="file" />
                  <span className="row-body">
                    <strong>{item.title}</strong>
                    <small>
                      {new Date(item.expires_at) <= new Date()
                        ? "预览已过期，请重新导入"
                        : "等待人工核对"}
                    </small>
                  </span>
                  <Icon name="arrow" size={16} />
                </button>
              ))
            ) : (
              <Empty title="没有待确认资料">
                <p>文件解析完成后，会在这里出现。</p>
              </Empty>
            )}
          </section>
          <div className="import-help">
            <h3>一份原件，一份可核对的正文</h3>
            <p>
              上传的原文件会保存在资料库中。解析完成后，可以查看原文、章节目次和逐条差异。
            </p>
            <p>
              扫描版 PDF 需要先取得可复制的文字。这里支持带文本层的
              PDF，不进行模型 OCR。
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}

function ImportFile({
  kind,
  options,
  target,
  existing,
}: {
  kind: Kind;
  options: Options;
  target: string;
  existing?: Document | null;
}) {
  const [file, setFile] = useState<File>();
  const preset = (field: keyof Document) => {
    const value = existing?.[field];
    return typeof value === "string" ? value : "";
  };
  const action = useAction();
  const isJson = file?.name.toLowerCase().endsWith(".json");
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!file) {
      action.notify("请选择来源文件。", true);
      return;
    }
    void action.run(async () => {
      if (file.size > 20 * 1024 * 1024)
        throw new Error("单个来源文件最多 20 MiB。");
      const upload = new FormData();
      upload.set("file", file);
      const artifact = await api<{ id: string }>("/uploads", "POST", upload);
      const metadata: Record<string, string> = {};
      for (const [key, value] of form.entries())
        if (key !== "file" && typeof value === "string" && value.trim())
          metadata[
            key === "display_name" ? (kind === "law" ? "title" : "name") : key
          ] = value.trim();
      const job = await api<Job>("/jobs", "POST", {
        action: "import",
        arguments: { artifact_id: artifact.id, kind, metadata },
      });
      changed();
      navigate("jobs", job.id);
    });
  };
  return (
    <form className="form-body" onSubmit={submit}>
      <label className={`upload-zone ${file ? "selected" : ""}`}>
        <Icon name={file ? "file" : "upload"} size={30} />
        <strong>{file ? file.name : "选择来源文件"}</strong>
        <span>JSON、TXT、Markdown、DOCX 或文本型 PDF · 最多 20 MiB</span>
        <input
          type="file"
          name="file"
          accept=".json,.txt,.md,.docx,.pdf"
          required
          aria-label="选择来源文件"
          onChange={(event) => setFile(event.target.files?.[0])}
        />
      </label>
      {!options.pdf_text_available && (
        <Note warning>
          当前环境没有 PDF 文本提取工具。可先上传 TXT / DOCX，或按部署说明安装
          Poppler 后使用 PDF。
        </Note>
      )}
      <div className="form-grid">
        <Field
          label="资料名称"
          hint={isJson ? "留空时沿用 JSON 中的名称。" : "留空时使用文件名。"}
        >
          <input
            name="display_name"
            maxLength={500}
            defaultValue={existing ? docTitle(existing) : ""}
            placeholder={
              kind === "law"
                ? "例如：某法规的正式名称"
                : "例如：公司费用管理办法"
            }
          />
        </Field>
        <Field
          label="资料标识"
          hint={
            target
              ? "已绑定要替换的资料。"
              : "新资料可留空；重新导入时使用已有标识。"
          }
        >
          <input
            name="id"
            defaultValue={target}
            readOnly={!!target}
            maxLength={500}
            placeholder="自动生成或沿用 JSON 标识"
          />
        </Field>
      </div>
      <div className="form-grid">
        {kind === "law" ? (
          <>
            <Field label="效力层级">
              <select
                name="level"
                required={!isJson}
                defaultValue={preset("level")}
              >
                <option value="">
                  {isJson ? "沿用 JSON 内容" : "请选择效力层级"}
                </option>
                {options.law_levels.map((value) => (
                  <option value={value} key={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="法规状态">
              <select
                name="status"
                required={!isJson}
                defaultValue={preset("status")}
              >
                <option value="">
                  {isJson ? "沿用 JSON 内容" : "请选择已核实的状态"}
                </option>
                {options.law_statuses.map((value) => (
                  <option value={value} key={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : (
          <>
            <Field label="私域规范类型">
              <select
                name="source_type"
                required={!isJson}
                defaultValue={preset("source_type")}
              >
                <option value="">
                  {isJson ? "沿用 JSON 内容" : "请选择资料类型"}
                </option>
                {options.norm_types.map((value) => (
                  <option value={value} key={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="制定主体">
              <input
                name="authority"
                defaultValue={preset("authority")}
                placeholder="例如：资料所属公司"
                maxLength={500}
              />
            </Field>
            <Field label="约束范围">
              <input
                name="binding_scope"
                defaultValue={preset("binding_scope")}
                placeholder="例如：某公司内的费用审批"
                maxLength={2000}
              />
            </Field>
          </>
        )}
      </div>
      <details className="additional-fields">
        <summary>补充来源信息</summary>
        <div className="form-grid">
          <Field label="来源名称">
            <input
              name="source_name"
              maxLength={500}
              placeholder="原件的提供方"
            />
          </Field>
          <Field label="来源地址">
            <input
              name="source_url"
              type="url"
              maxLength={2000}
              placeholder="https://…"
            />
          </Field>
        </div>
      </details>
      <div className="form-submit">
        <span>下一步会生成完整预览。</span>
        <button className="button" disabled={action.busy || !file}>
          {action.busy ? "正在上传…" : "上传并生成预览"}
          <Icon name="arrow" size={17} />
        </button>
      </div>
    </form>
  );
}

type Candidate = {
  id: string;
  title?: string;
  name?: string;
  status?: string;
  released_at?: string;
  effective_at?: string;
  source_url?: string;
};
function FetchForm({ options }: { options: Options }) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("flk_npc");
  const [candidates, setCandidates] = useState<Candidate[]>();
  const [selected, setSelected] = useState("");
  const [searched, setSearched] = useState({ query: "", source: "" });
  const action = useAction();
  return (
    <div className="form-body">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void action.run(async () => {
            const result = await api<{ candidates: Candidate[] }>(
              "/sources/search",
              "POST",
              { query, source },
            );
            setCandidates(result.candidates);
            setSelected("");
            setSearched({ query, source });
          });
        }}
      >
        <Field label="官方来源">
          <select
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            {options.fetch_sources.map((value) => (
              <option value={value} key={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="法规名称">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入正式名称或关键词"
            required
            minLength={1}
            maxLength={200}
          />
        </Field>
        <button className="button secondary" disabled={action.busy}>
          {action.busy ? "正在连接来源…" : "查找来源候选"}
        </button>
      </form>
      {candidates && (
        <div className="candidates">
          <h3>选择名称与版本相符的资料</h3>
          {candidates.length ? (
            candidates.map((candidate) => (
              <label
                className={`candidate ${selected === candidate.id ? "selected" : ""}`}
                key={candidate.id}
              >
                <input
                  type="radio"
                  name="candidate"
                  value={candidate.id}
                  checked={selected === candidate.id}
                  onChange={() => setSelected(candidate.id)}
                />
                <span>
                  <strong>
                    {candidate.title || candidate.name || candidate.id}
                  </strong>
                  <small>
                    {label(candidate.status)} ·{" "}
                    {candidate.effective_at ||
                      candidate.released_at ||
                      "日期未提供"}
                  </small>
                  {safeUrl(candidate.source_url) && (
                    <a
                      href={safeUrl(candidate.source_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      查看来源
                    </a>
                  )}
                </span>
              </label>
            ))
          ) : (
            <Empty title="没有找到候选资料" />
          )}
          <div className="form-submit">
            <span>抓取只生成预览，等待你确认。</span>
            <button
              className="button"
              disabled={action.busy || !selected}
              onClick={() =>
                void action.run(async () => {
                  const job = await api<Job>("/jobs", "POST", {
                    action: "fetch",
                    arguments: {
                      query: searched.query,
                      source: searched.source,
                      prefer_id: selected,
                    },
                  });
                  changed();
                  navigate("jobs", job.id);
                })
              }
            >
              获取并生成预览
              <Icon name="arrow" size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const metadataLabels: Record<string, string> = {
  categories: "关联分类目录",
  category_ids: "分类关联",
  revision_id: "版本标识",
  version_label: "版本名称",
  revision_released_at: "版本公布日期",
  revision_notes: "版本备注",
  title: "名称",
  name: "名称",
  short_title: "简称",
  short_name: "简称",
  source_name: "来源名称",
  source_url: "来源地址",
  source_hash: "来源指纹",
  source_checked_at: "取得时间",
  level: "效力层级",
  status: "法规状态",
  source_type: "私域规范类型",
  authority: "制定主体",
  binding_scope: "约束范围",
  aliases: "其他名称",
  effective_at: "施行日期",
  repealed_at: "废止日期",
  released_at: "公布日期",
  issuing_body: "发布机关",
  document_number: "文号",
  metadata: "其他元数据",
  jurisdiction: "适用地域",
  id: "资料标识",
};
// Fields the importer rewrites on every run. They are kept for audit but are
// not something the reviewer has to read to decide whether the text is right.
const BOOKKEEPING_FIELDS = new Set([
  "source_hash",
  "source_checked_at",
  "metadata",
]);
const displayValue = (field: string, value: unknown) => {
  if (value == null) return "未提供";
  if (typeof value === "string") {
    if (value.startsWith("local-file://")) return "面板上传的文件";
    if (field === "source_checked_at") return formatDate(value);
    if (field === "source_hash") return value.slice(0, 16) + "…";
    return label(value);
  }
  if (field === "metadata" && typeof value === "object") {
    const ingest = (
      value as { ingest?: { original_filename?: string; format?: string } }
    ).ingest;
    if (ingest?.original_filename)
      return `上传文件 ${ingest.original_filename}${ingest.format ? `（${ingest.format}）` : ""}`;
  }
  return JSON.stringify(value);
};
function MetadataRows({ items }: { items: Draft["diff"]["metadata"] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>字段</th>
            <th>当前值</th>
            <th>待入库值</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.field}>
              <td>{metadataLabels[item.field] || item.field}</td>
              <td>{displayValue(item.field, item.before)}</td>
              <td>{displayValue(item.field, item.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function DiffClause({ clause }: { clause: Clause }) {
  return (
    <>
      <strong>
        {clause.number_display || clause.number} {clause.title}
      </strong>
      {clause.part && <small>{clause.part}</small>}
      <p>{clause.text}</p>
    </>
  );
}
function DraftDiff({ draft }: { draft: Draft }) {
  const diff = draft.diff;
  const substantive = diff.metadata.filter(
    (item) => !BOOKKEEPING_FIELDS.has(item.field),
  );
  const bookkeeping = diff.metadata.filter((item) =>
    BOOKKEEPING_FIELDS.has(item.field),
  );
  return (
    <section className="panel diff-panel">
      <div className="panel-heading">
        <div>
          <h2>与当前入库内容比较</h2>
          <p>
            当前 {diff.before_count} 条，确认后 {diff.after_count}{" "}
            条。正文、条号、位置和章节目次均参与比较。
          </p>
        </div>
      </div>
      {substantive.length > 0 && (
        <details className="metadata-diff" open>
          <summary>资料信息变化（{substantive.length} 项）</summary>
          <MetadataRows items={substantive} />
        </details>
      )}
      {bookkeeping.length > 0 && (
        <details className="metadata-diff">
          <summary>
            来源记录更新（{bookkeeping.length} 项，每次导入都会刷新）
          </summary>
          <MetadataRows items={bookkeeping} />
        </details>
      )}
      {diff.modified.map((item, index) => (
        <div className="diff-pair" key={index}>
          <div className="diff-before">
            <Badge tone="red">修改前</Badge>
            <DiffClause clause={item.before} />
          </div>
          <div className="diff-after">
            <Badge tone="green">修改后</Badge>
            <DiffClause clause={item.after} />
          </div>
        </div>
      ))}
      {diff.added.map((clause, index) => (
        <div className="diff-single diff-after" key={"add" + index}>
          <Badge tone="green">新增</Badge>
          <DiffClause clause={clause} />
        </div>
      ))}
      {diff.removed.map((clause, index) => (
        <div className="diff-single diff-before" key={"remove" + index}>
          <Badge tone="red">移除</Badge>
          <DiffClause clause={clause} />
        </div>
      ))}
      {!diff.modified_count && !diff.added_count && !diff.removed_count && (
        <Empty title="条文内容与结构没有变化" />
      )}
    </section>
  );
}

export function DraftPage({ id }: { id: string }) {
  const result = useResource<Draft>("/drafts/" + id);
  const action = useAction();
  const [tab, setTab] = useState("text");
  const [confirmed, setConfirmed] = useState(false);
  if (!result.data)
    return (
      <Loading
        {...result}
        back={{ label: "返回导入与核对", onClick: () => navigate("import") }}
      />
    );
  const draft = result.data;
  const ready = draft.status === "ready";
  const canCommit = ready && !draft.expired && !draft.conflict;
  return (
    <>
      <button className="breadcrumb" onClick={() => navigate("import")}>
        <Icon name="back" size={16} />
        导入与核对
      </button>
      <Heading
        eyebrow="REVIEW BEFORE IMPORT"
        title={docTitle(draft.document)}
        text="这是等待你确认的完整内容。当前资料库将在确认后更新。"
      >
        <KindBadge kind={draft.document_kind} />
      </Heading>
      <div className="draft-summary">
        <span>
          <b>{draft.diff.after_count}</b> 条待入库正文
        </span>
        <Badge tone="green">新增 {draft.diff.added_count}</Badge>
        <Badge tone="amber">修改 {draft.diff.modified_count}</Badge>
        <Badge tone="red">移除 {draft.diff.removed_count}</Badge>
        <small>预览有效至 {formatDate(draft.expires_at)}</small>
      </div>
      {!ready && (
        <Note warning>
          这份预览已经{draft.status === "committed" ? "确认入库" : "取消"}。
          {draft.status === "committed" && (
            <button
              className="text-button"
              onClick={() => openDocument(draft.document_kind, draft.target_id)}
            >
              查看入库正文
            </button>
          )}
        </Note>
      )}
      {ready && draft.conflict && (
        <Note warning>
          资料库中的内容在预览后发生了变化。请重新导入并核对差异。
        </Note>
      )}
      {ready && draft.expired && (
        <Note warning>预览已过期，请重新取得资料并核对。</Note>
      )}
      {draft.warnings.length > 0 && (
        <div className="note warning">
          <Icon name="alert" />
          <div>
            <strong>请核对以下结构提示</strong>
            {draft.warnings.map((warning, i) => (
              <p key={i}>
                {warning.message || warning.code || JSON.stringify(warning)}
              </p>
            ))}
          </div>
        </div>
      )}
      <div className="tabs" role="tablist">
        {[
          ["text", "待入库全文"],
          ["diff", "完整差异"],
          ["source", "来源与原件"],
        ].map(([value, title]) => (
          <button
            role="tab"
            aria-selected={tab === value}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
            key={value}
          >
            {title}
          </button>
        ))}
      </div>
      {tab === "text" && (
        <FullText clauses={clausesOf(draft.document)} title="待入库正文" />
      )}
      {tab === "diff" && <DraftDiff draft={draft} />}
      {tab === "source" && (
        <SourcePanel data={draft.document} origin={draft.origin} />
      )}
      {ready && (
        <div className="confirmation-bar">
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              disabled={!canCommit}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>我已核对全文与来源，确认按以上内容入库</span>
          </label>
          <div className="button-row">
            <button
              className="button secondary"
              disabled={action.busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "取消后这份预览会被丢弃，需要重新导入才能再次核对。确定取消？",
                  )
                )
                  return;
                void action.run(async () => {
                  await api("/drafts/" + id + "/cancel", "POST");
                  changed();
                  navigate("import");
                });
              }}
            >
              取消此预览
            </button>
            <button
              className="button"
              disabled={action.busy || !canCommit || !confirmed}
              onClick={() =>
                void action.run(async () => {
                  try {
                    await api("/drafts/" + id + "/commit", "POST", {
                      fingerprint: draft.fingerprint,
                    });
                  } catch (error) {
                    // 409/410 mean the draft state changed under us; refetch so
                    // the conflict/expired banner replaces the confirm button.
                    setConfirmed(false);
                    result.reload();
                    throw error;
                  }
                  changed();
                  action.notify("已按确认的内容入库。");
                  openDocument(draft.document_kind, draft.target_id);
                })
              }
            >
              {action.busy ? "正在处理…" : "确认入库"}
              <Icon name="check" size={17} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export function JobsPage({ selected }: { selected?: string }) {
  const result = useResource<{ items: Job[] }>("/jobs", 2000);
  const action = useAction();
  return (
    <>
      <Heading
        eyebrow="MAINTENANCE"
        title="维护任务"
        text="任务保存在资料库中。关闭页面不会丢失；服务中断后可手动重试。"
      />
      {!result.data ? (
        <Loading {...result} />
      ) : result.data.items.length ? (
        <div className="job-list">
          {result.data.items.map((job) => (
            <section
              className={`panel job-card ${selected === job.id ? "selected" : ""}`}
              key={job.id}
            >
              <div className="job-heading">
                <span className="row-icon">
                  <Icon name={job.action === "fetch" ? "globe" : "upload"} />
                </span>
                <div className="row-body">
                  <h2>{job.result?.title || label(job.action)}</h2>
                  <small>
                    {formatDate(job.created_at)}
                    {job.parent_id ? " · 重试任务" : ""}
                  </small>
                </div>
                <Badge
                  tone={
                    job.state === "failed"
                      ? "red"
                      : job.state === "completed"
                        ? "green"
                        : job.state === "awaiting_confirmation"
                          ? "amber"
                          : ""
                  }
                >
                  {job.state === "running" && (
                    <span className="spinner small" />
                  )}
                  {label(job.state)}
                </Badge>
              </div>
              <p className="job-message">{job.message}</p>
              {job.error && (
                <Note warning>
                  {job.error.message}
                  {job.error.detail && (
                    <details>
                      <summary>技术细节</summary>
                      <code className="wrap">{job.error.detail}</code>
                    </details>
                  )}
                </Note>
              )}
              <div className="job-footer">
                <span className="mono muted">{job.id.slice(0, 12)}</span>
                <div className="button-row">
                  {["failed", "interrupted", "cancelled"].includes(
                    job.state,
                  ) && (
                    <button
                      className="button secondary compact"
                      disabled={action.busy}
                      onClick={() =>
                        void action.run(async () => {
                          const retry = await api<Job>(
                            `/jobs/${job.id}/retry`,
                            "POST",
                          );
                          changed();
                          navigate("jobs", retry.id);
                        })
                      }
                    >
                      重试
                    </button>
                  )}
                  {["queued", "running", "awaiting_confirmation"].includes(
                    job.state,
                  ) && (
                    <button
                      className="button secondary compact"
                      disabled={action.busy || job.cancel_requested}
                      onClick={() =>
                        void action.run(async () => {
                          await api(`/jobs/${job.id}/cancel`, "POST");
                          changed();
                        })
                      }
                    >
                      {job.cancel_requested ? "正在取消…" : "取消任务"}
                    </button>
                  )}
                  {job.draft_id && (
                    <button
                      className="button compact"
                      onClick={() => navigate("draft", job.draft_id)}
                    >
                      {job.state === "awaiting_confirmation"
                        ? "核对并确认"
                        : "查看预览"}
                      <Icon name="arrow" size={15} />
                    </button>
                  )}
                </div>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <section className="panel">
          <Empty title="尚无维护任务">
            <p>上传文件或从官方来源获取资料后，可以在这里查看进度。</p>
            <button className="button" onClick={() => navigate("import")}>
              导入资料
            </button>
          </Empty>
        </section>
      )}
    </>
  );
}
