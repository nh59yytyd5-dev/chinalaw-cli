"""Agent-driven alias backfill.

Why this module exists: ``aliases.common_law_aliases`` is rule-based, so
every newly-published law / unforeseen naming format needs a code edit.
Long-term we want an agent step at fetch time — let an LLM look at the
official law title once and write reasonable community abbreviations
back into ``laws.aliases``. New formats get covered without editing
this codebase.

Design constraints kept from chinalaw's runtime principles:

- **Zero runtime deps.** Talks to an OpenAI-compatible chat completions
  endpoint over stdlib ``urllib.request``. No requests / openai / anthropic
  SDK in the import path.
- **Opt-in (at the fetch layer).** The fetch wrapper gates on
  ``CHINALAW_USE_ALIAS_AGENT`` (see ``fetch._maybe_enrich_aliases``);
  callers of ``derive_aliases`` directly are responsible for their own
  gating.
- **Tiered errors.** Known recoverable failures (missing config, network
  / HTTP errors, JSON parse) raise ``AliasAgentRecoverableError`` so the
  fetch wrapper can record a structured warning. Unknown exceptions
  propagate so internal bugs are not silently swallowed.
- **Cheap.** One short call per *new* law, low temperature, capped output.

Provider configuration (env, all optional except the URL+key when
enabled at the fetch layer):

- ``CHINALAW_ALIAS_AGENT_BASE_URL``  — e.g. ``https://api.deepseek.com/v1``
- ``CHINALAW_ALIAS_AGENT_API_KEY``   — bearer token
- ``CHINALAW_ALIAS_AGENT_MODEL``     — e.g. ``deepseek-chat``
- ``CHINALAW_ALIAS_AGENT_TIMEOUT``   — seconds (float), default ``20``
- ``CHINALAW_ALIAS_AGENT_MAX``       — max aliases returned (default 12)

详见 ``docs/FETCH_LAYER_SPEC.md`` §3。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable

# Module-level seam used by tests so the network is never hit during the
# unit suite. Swap to a stub like ``alias_agent._http_post = _fake_post``
# in test setup.
HttpPost = Callable[[str, dict, dict, float], str]


class AliasAgentRecoverableError(Exception):
    """alias_agent 已知的可恢复错误：缺 key / 网络 / 限流 / 解析。

    fetch 主流程会把这种错误写到 response 的 ``warnings`` 字段，不挂主流程。
    其他 ``Exception`` 视为内部 bug，调用方应原样上抛由调用层决定。

    ``reason`` 取值：
        ``missing_api_key``    — 缺 base_url / api_key / model 配置
        ``network``            — OSError / urllib HTTPError / 超时
        ``invalid_response``   — provider 返回非 JSON 数组 / 内容不可解析
    """

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> str:
    """POST JSON body, return decoded body text. Raises on non-2xx."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


_PROMPT_TEMPLATE = """\
你是一名中国法律实务别名整理员。任务：给出本部法规在律师 / 法官 / 学者
社区的常用缩写、简称、别称。要求：

1. 只输出 JSON 数组，不要解释、不要前缀、不要 markdown。
2. 数组元素是字符串，每个 1-15 字。
3. 必须是真实存在的圈内简称（"民法典" / "九民纪要" / "公司法解释二"
   等），不要生造、不要拼词、不要把全名复制一遍。
4. 不要包含数字以外的标点，不要包含书名号。
5. 数量上限 {max_n}；如果想不出可信的简称，宁可少给也不要硬凑。
6. 不要包含已是法规正式名 / 已含"中华人民共和国"前缀的写法。

法规正式名：{title}

输出（JSON 数组）："""


_ALIAS_OK = re.compile(r"^[一-鿿（）()0-9一二三四五六七八九十]{1,15}$")


def _normalize(title: str, raw: list) -> list[str]:
    """Filter LLM output to legitimate aliases."""
    seen: set[str] = set()
    out: list[str] = []
    title_norm = (title or "").strip()
    title_no_country = re.sub(r"^中华人民共和国", "", title_norm)
    for item in raw:
        if not isinstance(item, str):
            continue
        cand = item.strip()
        if not cand or len(cand) > 15:
            continue
        if cand in (title_norm, title_no_country):
            continue
        if not _ALIAS_OK.match(cand):
            continue
        if cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
    return out


def derive_aliases(
    title: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    max_aliases: int | None = None,
    http_post: HttpPost = _http_post,
) -> list[str]:
    """Ask an LLM for community abbreviations of ``title``.

    Raises ``AliasAgentRecoverableError`` on:
    - missing config (no base_url / api_key / model after merging env)
    - HTTP / network failure (``OSError``, ``urllib.error.HTTPError``,
      ``urllib.error.URLError``)
    - provider returned content that is not a JSON array of strings

    Unknown exceptions propagate — they signal internal bugs that the
    fetch caller must see, not silently drop.

    Returns ``[]`` only when the title is empty (legitimate no-op).
    """
    title_clean = (title or "").strip()
    if not title_clean:
        return []

    base = (base_url or os.environ.get("CHINALAW_ALIAS_AGENT_BASE_URL", "")).rstrip("/")
    key = api_key or os.environ.get("CHINALAW_ALIAS_AGENT_API_KEY", "")
    mdl = model or os.environ.get("CHINALAW_ALIAS_AGENT_MODEL", "")
    if not (base and key and mdl):
        raise AliasAgentRecoverableError(
            "missing_api_key",
            "alias_agent 需要 CHINALAW_ALIAS_AGENT_BASE_URL / "
            "CHINALAW_ALIAS_AGENT_API_KEY / CHINALAW_ALIAS_AGENT_MODEL "
            "三项环境变量都配齐才能调用。",
        )

    cap = max_aliases if max_aliases is not None else int(
        os.environ.get("CHINALAW_ALIAS_AGENT_MAX", "12") or 12
    )
    cap = max(1, min(cap, 30))
    timeout_s = (
        timeout
        if timeout is not None
        else float(os.environ.get("CHINALAW_ALIAS_AGENT_TIMEOUT", "20") or 20)
    )

    body = {
        "model": mdl,
        "messages": [
            {
                "role": "user",
                "content": _PROMPT_TEMPLATE.format(title=title_clean, max_n=cap),
            }
        ],
        "temperature": 0.1,
        "max_tokens": 256,
    }
    headers = {"Authorization": f"Bearer {key}"}

    try:
        raw_body = http_post(f"{base}/chat/completions", headers, body, timeout_s)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
        raise AliasAgentRecoverableError("network", str(exc)) from exc

    try:
        envelope = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise AliasAgentRecoverableError(
            "invalid_response", f"provider 返回非 JSON: {exc}"
        ) from exc

    choices = envelope.get("choices") or []
    if not choices:
        raise AliasAgentRecoverableError(
            "invalid_response", "provider 返回 choices 为空"
        )
    msg = (choices[0] or {}).get("message") or {}
    text = msg.get("content") or ""
    if not isinstance(text, str):
        raise AliasAgentRecoverableError(
            "invalid_response", "provider message.content 不是字符串"
        )
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        raise AliasAgentRecoverableError(
            "invalid_response", "provider content 内未找到 JSON 数组"
        )
    try:
        arr = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AliasAgentRecoverableError(
            "invalid_response", f"JSON 数组解析失败: {exc}"
        ) from exc
    if not isinstance(arr, list):
        raise AliasAgentRecoverableError(
            "invalid_response", "provider 返回的不是数组"
        )
    return _normalize(title_clean, arr)[:cap]
