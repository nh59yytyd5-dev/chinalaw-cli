import { useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { api, setCsrf, type Session } from "./api";
import { Heading, Icon, Loading, navigate, ToastContext } from "./components";
import { ConnectionsPage, ConsentPage, MissingPage } from "./connections";
import { DocumentPage, InventoryPage, Overview, SearchPage } from "./library";
import { DraftPage, ImportPage, JobsPage } from "./manage";
import "./style.css";

function Login({
  error: initialError,
  session,
  onLogin,
}: {
  error: string;
  session?: Session;
  onLogin: (value: Session) => void;
}) {
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<Session>("/auth/login", "POST", {
        password: form.get("password"),
      });
      setCsrf(result.csrf_token);
      onLogin({ ...session, ...result });
    } catch (error) {
      setError(error instanceof Error ? error.message : "登录未完成");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login-page">
      <section className="login-intro">
        <div className="brand">
          <span className="brand-symbol">
            <Icon name="book" size={29} />
          </span>
          <strong>chinalaw</strong>
        </div>
        <div>
          <span className="eyebrow">YOUR SOURCES, WITH CONFIDENCE</span>
          <h1>
            每一条依据，
            <br />
            都清晰可查。
          </h1>
          <p>
            在自己的资料库中浏览法规、核对全文、
            <br />
            保存来源，让人工与工具使用同一份可靠资料。
          </p>
        </div>
        <small>公开法规 · 私域规范 · 可追溯来源</small>
      </section>
      <section className="login-form">
        <div>
          <Heading
            eyebrow="LIBRARY ACCESS"
            title="打开你的资料库"
            text="使用所有者密码登录，继续浏览与维护。"
          />
          {error && (
            <div className="inline-error" role="alert">
              {error}
            </div>
          )}
          {session?.local_mode && !session.password_configured ? (
            <div className="pairing-help">
              <Icon name="lock" size={30} />
              <p>
                请从启动命令显示的本地配对链接打开面板。配对链接仅使用一次，有效期
                3 分钟。
              </p>
              <p className="muted">
                进入面板后，可在“连接与备份”中设置登录密码。
              </p>
            </div>
          ) : (
            <form onSubmit={(event) => void submit(event)}>
              <label className="field">
                <span>所有者密码</span>
                <input
                  aria-label="所有者密码"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  maxLength={1024}
                />
              </label>
              <button className="button" disabled={busy}>
                {busy ? "正在登录…" : "登录资料库"}
                <Icon name="arrow" size={18} />
              </button>
            </form>
          )}
          <p className="login-note">
            <Icon name="lock" size={14} />
            资料存储在你配置的本机或服务器中
          </p>
        </div>
      </section>
    </main>
  );
}

function App() {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState("");
  const [route, setRoute] = useState(location.hash.slice(1) || "overview");
  const [toast, setToast] = useState<{ message: string; error: boolean }>();
  const [search, setSearch] = useState("");
  useEffect(() => {
    const update = () => {
      setRoute(location.hash.slice(1) || "overview");
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", update);
    const expired = () => {
      setCsrf(null);
      setSession((value) =>
        value ? { ...value, authenticated: false } : value,
      );
      setError("登录会话已失效，请重新登录。");
    };
    window.addEventListener("library:authentication-required", expired);
    const load = async () => {
      try {
        const fragment = location.hash.slice(1);
        if (fragment.startsWith("pair=")) {
          const token = new URLSearchParams(fragment).get("pair");
          history.replaceState(
            null,
            "",
            location.pathname + location.search + "#overview",
          );
          setRoute("overview");
          await api<Session>("/auth/pair", "POST", { token });
        }
        const result = await api<Session>("/auth/session");
        setCsrf(result.csrf_token);
        setSession(result);
      } catch (error) {
        setError(error instanceof Error ? error.message : "打开资料库失败");
        setSession({ authenticated: false, csrf_token: null });
      }
    };
    void load();
    return () => {
      window.removeEventListener("hashchange", update);
      window.removeEventListener("library:authentication-required", expired);
    };
  }, []);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(undefined), 6500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  if (!session) return <Loading error={error} />;
  if (!session.authenticated)
    return <Login error={error} session={session} onLogin={setSession} />;
  const split = route.indexOf("/");
  const page = split < 0 ? route : route.slice(0, split);
  let id = "";
  try {
    id = split < 0 ? "" : decodeURIComponent(route.slice(split + 1));
  } catch {
    /* malformed bookmark falls back to empty route */
  }
  const consent = new URLSearchParams(location.search).get("consent");
  const navigation = [
    ["overview", "grid", "资料概览"],
    ["law", "book", "公开法规"],
    ["norm", "folder", "私域规范"],
    ["import", "upload", "导入与核对"],
    ["jobs", "task", "维护任务"],
    ["connections", "plug", "连接与备份"],
  ];
  return (
    <ToastContext.Provider
      value={(message, error = false) => setToast({ message, error })}
    >
      <div className="app-shell">
        <aside className="sidebar">
          <a className="brand" href="#overview">
            <span className="brand-symbol">
              <Icon name="book" size={25} />
            </span>
            <span>
              <strong>chinalaw</strong>
              <small>规范资料库</small>
            </span>
          </a>
          <div className="sidebar-label">我的资料库</div>
          <nav aria-label="主要导航">
            {navigation.map(([value, icon, name]) => (
              <button
                className={
                  page === value || (value === "import" && page === "draft")
                    ? "active"
                    : ""
                }
                key={value}
                onClick={() => navigate(value)}
              >
                <Icon name={icon} size={19} />
                {name}
                {page === value && <span className="nav-dot" />}
              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="library-status">
              <span className="status-dot" />
              <div>
                <strong>
                  {session.local_mode ? "本机资料库" : "服务器资料库"}
                </strong>
                <small>单一所有者 · 自主管理</small>
              </div>
            </div>
            <button
              className="logout"
              onClick={() => {
                void api("/auth/logout", "POST")
                  .then(() => {
                    setCsrf(null);
                    setSession({ ...session, authenticated: false });
                  })
                  .catch((error) =>
                    setToast({ message: error.message, error: true }),
                  );
              }}
            >
              <Icon name="exit" size={17} />
              退出登录
            </button>
          </div>
        </aside>
        <div className="main-area">
          <header className="topbar">
            <span className="topbar-title">资料管理工作台</span>
            <form
              className="global-search"
              onSubmit={(event) => {
                event.preventDefault();
                if (search.trim()) navigate("search", search.trim());
              }}
            >
              <Icon name="search" size={17} />
              <input
                aria-label="检索全部正文"
                placeholder="检索全部正文…"
                value={search}
                maxLength={200}
                onChange={(event) => setSearch(event.target.value)}
              />
              <button aria-label="开始全文检索">
                <Icon name="arrow" size={15} />
              </button>
            </form>
            <span className="owner-avatar" title="资料库所有者">
              我
            </span>
          </header>
          <main id="main-content" className="main-content">
            {consent ? (
              <ConsentPage id={consent} />
            ) : page === "overview" ? (
              <Overview />
            ) : page === "law" || page === "norm" ? (
              id ? (
                <DocumentPage key={page + id} kind={page} id={id} />
              ) : (
                <InventoryPage key={page} kind={page} />
              )
            ) : page === "import" ? (
              <ImportPage key={id} parameter={id} />
            ) : page === "draft" ? (
              <DraftPage key={id} id={id} />
            ) : page === "jobs" ? (
              <JobsPage selected={id} />
            ) : page === "connections" ? (
              <ConnectionsPage />
            ) : page === "search" ? (
              <SearchPage key={id} initial={id} />
            ) : (
              <MissingPage />
            )}
          </main>
        </div>
      </div>
      {toast && (
        <div
          className={`toast ${toast.error ? "error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          <Icon name={toast.error ? "alert" : "check"} size={19} />
          <span>{toast.message}</span>
          <button aria-label="关闭提示" onClick={() => setToast(undefined)}>
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
    </ToastContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
