export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
  ) {
    super(message);
  }
}

let csrf = "";
export function setCsrf(value: string | null) {
  csrf = value || "";
}

export async function rawRequest(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
) {
  const headers: Record<string, string> = {};
  let encoded: BodyInit | undefined;
  if (body instanceof FormData) encoded = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    encoded = JSON.stringify(body);
  }
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const response = await fetch("/api/v1" + path, {
    method,
    headers,
    body: encoded,
    signal,
    credentials: "same-origin",
  });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event("library:authentication-required"));
    }
    const error = await response.json().catch(() => ({}));
    throw new ApiError(
      error.message || `请求未完成（${response.status}）`,
      response.status,
      error.error || "request_failed",
    );
  }
  return response;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return (await rawRequest(path, method, body, signal)).json() as Promise<T>;
}

export const query = (values: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== undefined && value !== "") params.set(key, String(value));
  return params.toString();
};

export function changed() {
  window.dispatchEvent(new Event("library:changed"));
}

export type Kind = "law" | "norm";
export type Clause = {
  id?: string;
  number?: string;
  number_display?: string;
  part?: string;
  title?: string;
  text: string;
  position?: number;
};
export type Document = {
  id: string;
  title?: string;
  name?: string;
  level?: string;
  status?: string;
  source_type?: string;
  source_url?: string;
  source_name?: string;
  source_checked_at?: string;
  source_hash?: string;
  effective_at?: string;
  released_at?: string;
  repealed_at?: string;
  issuing_body?: string;
  authority?: string;
  binding_scope?: string;
  binding_note?: string;
  document_number?: string;
  articles?: Clause[];
  clauses?: Clause[];
};
export type Origin = {
  artifact_ids?: string[];
  text_artifact_id?: string;
  type?: string;
  source_url?: string;
  filename?: string;
  restore_operation?: string;
  restore_revision?: string;
};
export type DocumentResult = {
  document: Document;
  fingerprint: string;
  document_kind: Kind;
  review?: { actor: string; note: string; reviewed_at: string };
  latest_operation?: { id: string; origin: Origin; created_at: string };
  revision?: string;
};
export type Item = {
  id: string;
  title: string;
  kind: Kind;
  category: string;
  status?: string;
  source_name?: string;
  source_url?: string;
  source_checked_at?: string;
  effective_at?: string;
  clause_count: number;
  authority?: string;
  binding_scope?: string;
  binding_note?: string;
};
export type Inventory = {
  items: Item[];
  total: number;
  pages: number;
  page: number;
  page_size: number;
};
export type DraftSummary = {
  id: string;
  kind: Kind;
  target_id: string;
  title: string;
  created_at: string;
  expires_at: string;
};
export type Draft = {
  id: string;
  document_kind: Kind;
  target_id: string;
  document: Document;
  before?: Document;
  status: string;
  fingerprint: string;
  conflict: boolean;
  expired: boolean;
  created_at: string;
  expires_at: string;
  operation_id?: string;
  origin: Origin;
  warnings: { code?: string; message?: string; severity?: string }[];
  diff: {
    added: Clause[];
    removed: Clause[];
    modified: { number?: string; before: Clause; after: Clause }[];
    added_count: number;
    removed_count: number;
    modified_count: number;
    before_count: number;
    after_count: number;
    metadata: { field: string; before: unknown; after: unknown }[];
  };
};
export type Job = {
  id: string;
  action: string;
  state: string;
  message: string;
  phase: string;
  created_at: string;
  draft_id?: string;
  parent_id?: string;
  cancel_requested: boolean;
  error?: { code: string; message: string; detail?: string };
  result?: { title?: string; document_kind?: Kind; target_id?: string };
};
export type Counts = {
  laws: number;
  articles: number;
  revisions: number;
  norm_sources: number;
  norm_clauses: number;
  norm_packs: number;
};
export type System = Counts & {
  schema_version: number;
  library_id: string;
  jobs: Record<string, number>;
  pending_drafts: number;
  by_level: { level: string; count: number }[];
  last_sync_at?: string;
};
export type Options = {
  law_levels: string[];
  law_statuses: string[];
  norm_types: string[];
  binding_notes: Record<string, string>;
  fetch_sources: string[];
  pdf_text_available: boolean;
};
export type Session = {
  authenticated: boolean;
  csrf_token: string | null;
  local_mode?: boolean;
  password_configured?: boolean;
};
export type Restore = {
  id: string;
  fingerprint: string;
  current: Counts;
  incoming: Counts;
  artifact_count: number;
  expires_at: string;
  manifest: { created_at: string; library_id: string; app_version: string };
};

export const labels: Record<string, string> = {
  law: "法律",
  admin_regulation: "行政法规",
  judicial_interpretation: "司法解释",
  judicial_meeting_minutes: "司法会议纪要",
  judicial_policy: "司法政策",
  guiding_case: "指导性案例",
  department_rule: "部门规章",
  local_regulation: "地方性法规",
  local_government_rule: "地方政府规章",
  supervisory_regulation: "监察法规",
  self_regulatory_rule: "自律规则",
  other: "其他",
  current: "现行",
  amended: "已修改",
  repealed: "已废止",
  pending_effective: "尚未施行",
  seed: "待补全",
  unknown: "状态待核实",
  contractual_requirement: "合同要求",
  internal_governance: "内部制度",
  standard: "标准",
  trade_usage: "交易习惯",
  queued: "排队中",
  running: "处理中",
  awaiting_confirmation: "待确认",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  import: "文件导入",
  fetch: "来源抓取",
  replace: "替换正文",
  restore: "恢复版本",
  flk_npc: "国家法律法规数据库",
  court_gongbao: "最高人民法院公报",
  court_main: "最高人民法院",
  spp_gov_cn: "最高人民检察院",
  csrc_gov_cn: "中国证监会",
  gov_xzfgk: "国务院行政法规库",
  nfra_gov_cn: "国家金融监督管理总局",
  bse_cn: "北京证券交易所",
  sse_com_cn: "上海证券交易所",
  szse_cn: "深圳证券交易所",
  chinaclear_cn: "中国结算",
  sac_net_cn: "中国证券业协会",
};
export const label = (value?: string) =>
  value ? labels[value] || value : "未提供";
export const docTitle = (document: Document) =>
  document.title || document.name || document.id;
export const clausesOf = (document: Document) =>
  document.articles || document.clauses || [];
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const SQLITE_UTC = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;
// Library timestamps arrive in three shapes: date-only fields (施行/公布日期),
// SQLite CURRENT_TIMESTAMP text (UTC without a zone marker) and ISO-8601 with
// an explicit offset. Normalise them so the panel never shifts a calendar
// date by the local offset or shows a UTC clock as local time.
export const formatDate = (value?: string | number) => {
  if (!value) return "未记录";
  if (typeof value === "string") {
    if (DATE_ONLY.test(value)) return value.replace(/-/g, "/");
    if (SQLITE_UTC.test(value)) value = value.replace(" ", "T") + "Z";
  }
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
};
export const number = (value: number) => value.toLocaleString("zh-CN");
export const safeUrl = (value?: string) => {
  try {
    const url = new URL(value || "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
};
