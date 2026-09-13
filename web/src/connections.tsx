import { useState, type FormEvent } from "react";
import {
  api,
  changed,
  formatDate,
  number,
  rawRequest,
  type Restore,
} from "./api";
import {
  Badge,
  Empty,
  Field,
  Heading,
  Icon,
  Loading,
  navigate,
  Note,
  useAction,
  useResource,
} from "./components";

const PUBLIC = "chinalaw:public:read";
const PRIVATE = "chinalaw:private:read";
type Credential = {
  id: string;
  name: string;
  kind: string;
  scopes: string[];
  created_at: number;
  expires_at: number;
  last_used_at?: number;
  revoked_at?: number;
};
export function ConnectionsPage() {
  const result = useResource<{ items: Credential[]; mcp_url: string }>(
    "/auth/credentials",
  );
  const [token, setToken] = useState("");
  const [restore, setRestore] = useState<Restore>();
  const [confirmed, setConfirmed] = useState(false);
  const action = useAction();
  const createToken = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void action.run(async () => {
      const created = await api<{ token: string }>(
        "/auth/credentials",
        "POST",
        {
          name: form.get("name"),
          days: Number(form.get("days")),
          scopes: form.get("private") ? [PUBLIC, PRIVATE] : [PUBLIC],
        },
      );
      setToken(created.token);
      changed();
    });
  };
  const download = () =>
    action.run(async () => {
      const response = await rawRequest("/backups", "POST");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `chinalaw-${new Date().toISOString().slice(0, 10)}.zip`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 5000);
      action.notify("备份已生成并开始下载。");
    });
  return (
    <>
      <Heading
        eyebrow="CONNECTIONS & BACKUP"
        title="连接与备份"
        text="让其他应用只读访问资料库，或将资料完整迁移到另一台设备。"
      />
      <section className="panel settings-section">
        <div className="panel-heading">
          <div>
            <h2>远程只读连接</h2>
            <p>连接的应用只能查询。资料导入、替换和恢复由你在面板中完成。</p>
          </div>
          <Icon name="plug" />
        </div>
        {!result.data ? (
          <Loading {...result} />
        ) : (
          <div className="settings-body">
            <Field label="MCP 连接地址">
              <div className="copy-field">
                <input value={result.data.mcp_url} readOnly />
                <button
                  className="button secondary compact"
                  onClick={() =>
                    void action.run(async () => {
                      await navigator.clipboard.writeText(result.data!.mcp_url);
                      action.notify("地址已复制。");
                    })
                  }
                >
                  复制
                </button>
              </div>
            </Field>
            <p className="muted">
              支持 OAuth 的客户端可通过此地址发起授权；支持 Bearer Token
              的客户端可使用下方访问令牌。各平台的接入入口请参阅部署说明。
            </p>
            <form className="token-form" onSubmit={createToken}>
              <Field label="连接名称">
                <input
                  name="name"
                  required
                  maxLength={80}
                  placeholder="例如：我的桌面客户端"
                />
              </Field>
              <Field label="有效期">
                <select name="days" defaultValue="90">
                  <option value="30">30 天</option>
                  <option value="90">90 天</option>
                  <option value="365">365 天</option>
                </select>
              </Field>
              <label className="checkbox-field">
                <input type="checkbox" name="private" />
                同时允许读取私域规范
              </label>
              <button className="button" disabled={action.busy}>
                生成只读令牌
              </button>
            </form>
            {token && (
              <div className="token-result">
                <strong>访问令牌仅在这里显示一次，请复制保存。</strong>
                <div className="copy-field">
                  <input aria-label="新生成的只读令牌" readOnly value={token} />
                  <button
                    className="button secondary compact"
                    onClick={() =>
                      void action.run(async () => {
                        await navigator.clipboard.writeText(token);
                        action.notify("令牌已复制。");
                      })
                    }
                  >
                    复制
                  </button>
                </div>
                <button className="text-button" onClick={() => setToken("")}>
                  我已保存，收起令牌
                </button>
              </div>
            )}
            {result.data.items.length ? (
              <div className="credentials">
                {result.data.items.map((item) => (
                  <div className="list-row" key={item.id}>
                    <span className="row-icon">
                      <Icon name="lock" />
                    </span>
                    <span className="row-body">
                      <strong>
                        {item.name}
                        <Badge tone={item.revoked_at ? "" : "green"}>
                          {item.revoked_at
                            ? "已撤销"
                            : item.expires_at * 1000 < Date.now()
                              ? "已过期"
                              : "有效"}
                        </Badge>
                      </strong>
                      <small>
                        {item.scopes.includes(PRIVATE)
                          ? "公开 + 私域只读"
                          : "仅公开法规只读"}{" "}
                        · 到期 {formatDate(item.expires_at)}
                        <br />
                        最近使用：{formatDate(item.last_used_at)}
                      </small>
                    </span>
                    {!item.revoked_at && (
                      <button
                        className="text-button danger-text"
                        disabled={action.busy}
                        onClick={() =>
                          void action.run(async () => {
                            await api("/auth/credentials/" + item.id, "DELETE");
                            changed();
                            action.notify("此授权已撤销。");
                          })
                        }
                      >
                        撤销
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="quiet-empty">
                暂无远程授权。默认不会向其他应用开放资料。
              </p>
            )}
          </div>
        )}
      </section>
      <section className="panel settings-section">
        <div className="panel-heading">
          <div>
            <h2>备份与恢复</h2>
            <p>备份包含公开、私域资料、版本、维护记录和来源附件。</p>
          </div>
          <Icon name="download" />
        </div>
        <div className="settings-body">
          <div className="backup-options">
            <div>
              <h3>下载完整资料库</h3>
              <p>
                备份可用于本机与服务器之间的迁移。登录密码、访问令牌和登录会话不包含在其中。
              </p>
              <button
                className="button secondary"
                disabled={action.busy}
                onClick={() => void download()}
              >
                <Icon name="download" size={17} />
                {action.busy ? "正在处理…" : "下载备份"}
              </button>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                void action.run(async () => {
                  const value = await api<Restore>(
                    "/backups/restore/preview",
                    "POST",
                    form,
                  );
                  setRestore(value);
                  setConfirmed(false);
                });
              }}
            >
              <h3>从备份恢复</h3>
              <p>先校验备份并查看替换范围，再确认恢复。</p>
              <input
                aria-label="选择资料库备份"
                name="file"
                type="file"
                accept=".zip"
                required
              />
              <button className="button secondary" disabled={action.busy}>
                上传并校验备份
              </button>
            </form>
          </div>
          {restore && (
            <div className="restore-preview">
              <h3>恢复范围预览</h3>
              <p className="muted">
                备份时间：{formatDate(restore.manifest.created_at)} · 附件{" "}
                {restore.artifact_count} 个
              </p>
              <table>
                <thead>
                  <tr>
                    <th>内容</th>
                    <th>当前资料库</th>
                    <th>恢复后</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["laws", "公开法规"],
                      ["articles", "公开条文"],
                      ["revisions", "法规版本"],
                      ["norm_sources", "私域规范"],
                      ["norm_clauses", "私域条款"],
                    ] as const
                  ).map(([key, title]) => (
                    <tr key={key}>
                      <td>{title}</td>
                      <td>{number(restore.current[key])}</td>
                      <td>{number(restore.incoming[key])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Note warning>
                恢复将用这份备份替换当前资料库。请先下载当前备份；若预览后资料发生变化，服务会拒绝恢复并提示重新核对。
              </Note>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                我已核对替换范围，并保存需要保留的当前资料
              </label>
              <div className="button-row">
                <button
                  className="button secondary"
                  onClick={() => setRestore(undefined)}
                >
                  取消恢复
                </button>
                <button
                  className="button danger"
                  disabled={!confirmed || action.busy}
                  onClick={() =>
                    void action.run(async () => {
                      await api(
                        `/backups/restore/${restore.id}/commit`,
                        "POST",
                        { fingerprint: restore.fingerprint },
                      );
                      setRestore(undefined);
                      changed();
                      action.notify("资料库已恢复，未完成的旧任务需手动重试。");
                      navigate("overview");
                    })
                  }
                >
                  确认恢复资料库
                </button>
              </div>
            </div>
          )}
        </div>
      </section>
      <section className="panel settings-section">
        <div className="panel-heading">
          <div>
            <h2>所有者登录密码</h2>
            <p>更改后需要重新登录。密码至少 12 个字符。</p>
          </div>
        </div>
        <form
          className="settings-body password-form"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            if (form.get("password") !== form.get("confirm")) {
              action.notify("两次密码不一致。", true);
              return;
            }
            void action.run(async () => {
              await api("/auth/password", "POST", {
                password: form.get("password"),
              });
              location.reload();
            });
          }}
        >
          <Field label="新密码">
            <input
              type="password"
              name="password"
              minLength={12}
              maxLength={1024}
              autoComplete="new-password"
              required
            />
          </Field>
          <Field label="再次输入新密码">
            <input
              type="password"
              name="confirm"
              minLength={12}
              maxLength={1024}
              autoComplete="new-password"
              required
            />
          </Field>
          <button className="button secondary" disabled={action.busy}>
            更新密码
          </button>
        </form>
      </section>
    </>
  );
}

export function ConsentPage({ id }: { id: string }) {
  const result = useResource<{
    client_name: string;
    redirect_uri: string;
    resource: string;
    scopes: string[];
  }>("/auth/consent/" + id);
  const [privateAllowed, setPrivateAllowed] = useState(false);
  const action = useAction();
  if (!result.data) return <Loading {...result} />;
  const data = result.data;
  const consent = (approved: boolean) =>
    action.run(async () => {
      const response = await api<{ redirect_url: string }>(
        "/auth/consent/" + id,
        "POST",
        { approved, scopes: privateAllowed ? [PUBLIC, PRIVATE] : [PUBLIC] },
      );
      location.assign(response.redirect_url);
    });
  return (
    <div className="consent-page">
      <section className="panel">
        <div className="consent-icon">
          <Icon name="plug" size={32} />
        </div>
        <Heading
          title="授权应用读取资料"
          text={`${data.client_name} 希望连接你的资料库。`}
        />
        <dl className="metadata-grid">
          <div className="wide">
            <dt>回调地址</dt>
            <dd className="wrap">{data.redirect_uri}</dd>
          </div>
          <div className="wide">
            <dt>资料库地址</dt>
            <dd>{data.resource}</dd>
          </div>
        </dl>
        <Note>应用可以检索和读取公开法规，不能导入、修改或恢复资料库。</Note>
        {data.scopes.includes(PRIVATE) && (
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={privateAllowed}
              onChange={(event) => setPrivateAllowed(event.target.checked)}
            />
            同时允许此应用读取私域规范
          </label>
        )}
        <div className="button-row">
          <button
            className="button secondary"
            disabled={action.busy}
            onClick={() => void consent(false)}
          >
            拒绝
          </button>
          <button
            className="button"
            disabled={action.busy}
            onClick={() => void consent(true)}
          >
            允许只读访问
          </button>
        </div>
      </section>
    </div>
  );
}

export function MissingPage() {
  return (
    <Empty title="此页面不存在">
      <button className="button" onClick={() => navigate("overview")}>
        返回概览
      </button>
    </Empty>
  );
}
