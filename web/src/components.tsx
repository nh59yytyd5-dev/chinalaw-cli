import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";

const paths: Record<string, ReactNode> = {
  book: (
    <>
      <path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-3H4z" />
      <path d="M13 7a3 3 0 0 1 3-3h5v14h-4a4 4 0 0 0-4 3" />
    </>
  ),
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.4" />
      <rect x="14" y="3" width="7" height="7" rx="1.4" />
      <rect x="3" y="14" width="7" height="7" rx="1.4" />
      <rect x="14" y="14" width="7" height="7" rx="1.4" />
    </>
  ),
  folder: <path d="M3 7V5h6l2 3h10v12H3V7Z" />,
  upload: (
    <>
      <path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6" />
    </>
  ),
  task: (
    <>
      <rect x="5" y="4" width="15" height="17" rx="2" />
      <path d="M9 3h7v4H9zM9 12l2 2 5-5M9 18h6" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v4m6-4v4M7 7h10v4a5 5 0 0 1-10 0V7Zm5 9v5" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  back: <path d="M20 12H4m6-6-6 6 6 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  plus: <path d="M12 4v16M4 12h16" />,
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <ellipse cx="12" cy="12" rx="4" ry="9" />
      <path d="M3 12h18" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2" />
    </>
  ),
  link: (
    <>
      <path d="m10 14 4-4M8 15l-1 1a3.5 3.5 0 0 1-5-5l5-5a3.5 3.5 0 0 1 5 0m0 12a3.5 3.5 0 0 0 5 0l5-5a3.5 3.5 0 0 0-5-5l-1 1" />
    </>
  ),
  file: (
    <>
      <path d="M14 3H5v18h14V8l-5-5Z" />
      <path d="M14 3v5h5M8 13h8M8 17h6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6l4 2" />
    </>
  ),
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5" />,
  alert: (
    <>
      <path d="m12 3 10 18H2L12 3Z" />
      <path d="M12 9v5m0 3v1" />
    </>
  ),
  exit: (
    <>
      <path d="M10 4H4v16h6m5-12 4 4-4 4m-6-4h10" />
    </>
  ),
};
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.file}
    </svg>
  );
}

export const ToastContext = createContext<
  (message: string, error?: boolean) => void
>(() => {});
export function useAction() {
  const notify = useContext(ToastContext);
  const [busy, setBusy] = useState(false);
  const run = async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    if (busy) return;
    setBusy(true);
    try {
      return await action();
    } catch (error) {
      notify(
        error instanceof Error ? error.message : "操作未完成，请重试。",
        true,
      );
    } finally {
      setBusy(false);
    }
  };
  return { busy, run, notify };
}

export function useResource<T>(path: string, poll = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setData(undefined);
    setError("");
    const load = async () => {
      try {
        const value = await api<T>(path, "GET", undefined, controller.signal);
        if (active) {
          setData(value);
          setError("");
        }
      } catch (error) {
        if (
          active &&
          !(error instanceof DOMException && error.name === "AbortError")
        )
          setError(error instanceof Error ? error.message : "读取失败");
      }
    };
    void load();
    const update = () => {
      void load();
    };
    window.addEventListener("library:changed", update);
    const timer = poll
      ? window.setInterval(() => {
          if (!document.hidden) void load();
        }, poll)
      : undefined;
    return () => {
      active = false;
      controller.abort();
      window.removeEventListener("library:changed", update);
      clearInterval(timer);
    };
  }, [path, version, poll]);
  return { data, error, reload };
}

export function Loading({
  error,
  reload,
}: {
  error?: string;
  reload?: () => void;
}) {
  return error ? (
    <div className="empty" role="alert">
      <Icon name="alert" size={30} />
      <p>{error}</p>
      <button
        className="button secondary"
        onClick={reload || (() => location.reload())}
      >
        重新加载
      </button>
    </div>
  ) : (
    <div className="loading" role="status">
      <span className="spinner" />
      正在读取资料…
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon name="folder" size={30} />
      </div>
      <h3>{title}</h3>
      {children}
    </div>
  );
}
export function Heading({
  eyebrow,
  title,
  text,
  children,
}: {
  eyebrow?: string;
  title: string;
  text?: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {text && <p>{text}</p>}
      </div>
      {children && <div className="heading-actions">{children}</div>}
    </header>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function KindBadge({ kind }: { kind: "law" | "norm" }) {
  return (
    <Badge tone={kind === "norm" ? "private" : "green"}>
      <Icon name={kind === "norm" ? "lock" : "globe"} size={12} />
      {kind === "norm" ? "私域规范" : "公开法规"}
    </Badge>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="field-label">
        <span>{label}</span>
        {children}
      </label>
      {hint && <small>{hint}</small>}
    </div>
  );
}
export function Note({
  children,
  warning = false,
}: {
  children: ReactNode;
  warning?: boolean;
}) {
  return (
    <div className={`note ${warning ? "warning" : ""}`}>
      <Icon name={warning ? "alert" : "check"} size={18} />
      <div>{children}</div>
    </div>
  );
}
export function navigate(page: string, id?: string) {
  const url = new URL(location.href);
  const consent = url.searchParams.has("consent");
  if (consent) {
    url.searchParams.delete("consent");
    history.replaceState(null, "", url);
  }
  location.hash = page + (id ? "/" + encodeURIComponent(id) : "");
  if (consent) window.dispatchEvent(new HashChangeEvent("hashchange"));
}
export function openDocument(kind: "law" | "norm", id: string) {
  navigate(kind, id);
}
