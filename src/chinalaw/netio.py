"""Network policy and bounded HTTP reads for official source adapters."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, replace
from email.message import Message
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from chinalaw.resource_limits import (
    MAX_BINARY_BYTES,
    MAX_TEXT_BYTES,
    ResourceLimitError,
    read_limited,
)


class NetworkPolicyError(ValueError):
    """A URL or redirect violates a source allowlist."""


@dataclass(frozen=True)
class SourcePolicy:
    source: str
    allowed_hosts: frozenset[str]
    allow_subdomains: bool = False
    max_text_bytes: int = MAX_TEXT_BYTES
    max_binary_bytes: int = MAX_BINARY_BYTES
    max_redirects: int = 5
    timeout: float = 15
    resolve_hosts: bool = True
    allowed_ports: tuple[int, ...] | None = (443,)


@dataclass(frozen=True)
class NetworkResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


_SOURCE_HOSTS: dict[str, tuple[tuple[str, ...], bool]] = {
    "flk_npc": (("flk.npc.gov.cn",), True),
    "court_gongbao": (("gongbao.court.gov.cn",), False),
    "court_main": (("www.court.gov.cn",), False),
    "spp_gov_cn": (("www.spp.gov.cn",), False),
    "csrc_gov_cn": (("csrc.gov.cn",), True),
    "nfra_gov_cn": (("nfra.gov.cn",), True),
    "gov_xzfgk": (("xzfg.moj.gov.cn", "www.gov.cn"), False),
    "bse_cn": (("bse.cn",), True),
    "sse_com_cn": (("sse.com.cn",), True),
    "szse_cn": (("szse.cn",), True),
    "chinaclear_cn": (("chinaclear.cn",), True),
    "sac_net_cn": (("sac.net.cn",), True),
}


def source_policy(
    source: str,
    *,
    timeout: float | None = None,
    fallback_hosts: tuple[str, ...] = (),
    resolve_hosts: bool = True,
) -> SourcePolicy:
    normalized = (source or "").strip().lower().replace("-", "_")
    configured = _SOURCE_HOSTS.get(normalized)
    if configured is None:
        hosts = tuple(host.lower().rstrip(".") for host in fallback_hosts if host)
        if not hosts:
            raise NetworkPolicyError(f"no network policy registered for source {source!r}")
        allow_subdomains = False
    else:
        hosts, allow_subdomains = configured
    policy = SourcePolicy(
        source=normalized or source,
        allowed_hosts=frozenset(hosts),
        allow_subdomains=allow_subdomains,
        resolve_hosts=resolve_hosts,
    )
    return replace(policy, timeout=timeout) if timeout is not None else policy


def policy_for_endpoint(
    source: str,
    url: str,
    *,
    timeout: float,
    max_text_bytes: int = MAX_TEXT_BYTES,
) -> SourcePolicy:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_port = parsed.port or 443
    return SourcePolicy(
        source=source,
        allowed_hosts=frozenset({host}) if host else frozenset(),
        timeout=timeout,
        max_text_bytes=max_text_bytes,
        max_binary_bytes=max_text_bytes,
        allowed_ports=(allowed_port,),
    )


def _host_allowed(host: str, policy: SourcePolicy) -> bool:
    for allowed in policy.allowed_hosts:
        if host == allowed:
            return True
        if policy.allow_subdomains and host.endswith(f".{allowed}"):
            return True
    return False


def _reject_non_public_ip(address: str, *, url: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return
    if not ip.is_global:
        raise NetworkPolicyError(f"URL resolves to non-public address {ip}: {url}")


def validate_url(
    url: str,
    policy: SourcePolicy,
    *,
    resolver: Any = socket.getaddrinfo,
) -> str:
    parsed = urlsplit(str(url))
    if parsed.scheme.lower() != "https":
        raise NetworkPolicyError(f"only HTTPS URLs are allowed for {policy.source}: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkPolicyError(f"URL userinfo is not allowed for {policy.source}: {url}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise NetworkPolicyError(f"URL is missing a hostname for {policy.source}: {url}")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise NetworkPolicyError(f"local hostname is not allowed for {policy.source}: {url}")
    if not _host_allowed(host, policy):
        raise NetworkPolicyError(
            f"host {host!r} is not allowed for source {policy.source!r}"
        )
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise NetworkPolicyError(f"invalid URL port for {policy.source}: {url}") from exc
    if policy.allowed_ports is not None and port not in policy.allowed_ports:
        raise NetworkPolicyError(
            f"port {port} is not allowed for source {policy.source!r}"
        )

    _reject_non_public_ip(host, url=url)
    if policy.resolve_hosts:
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise NetworkPolicyError(f"unable to resolve allowed host {host!r}: {exc}") from exc
        for record in records:
            sockaddr = record[4]
            if sockaddr:
                _reject_non_public_ip(str(sockaddr[0]), url=url)
    return url


class PolicyRedirectHandler(HTTPRedirectHandler):
    def __init__(self, policy: SourcePolicy):
        super().__init__()
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        count = int(getattr(req, "_chinalaw_redirect_count", 0)) + 1
        if count > self.policy.max_redirects:
            raise NetworkPolicyError(
                f"too many redirects for source {self.policy.source!r}"
            )
        validate_url(newurl, self.policy)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._chinalaw_redirect_count = count
        return redirected


def build_policy_opener(policy: SourcePolicy, *handlers: object) -> OpenerDirector:
    return build_opener(PolicyRedirectHandler(policy), *handlers)


def response_charset(headers: dict[str, str], default: str = "utf-8") -> str:
    content_type = headers.get("Content-Type") or headers.get("content-type")
    if not content_type:
        return default
    message = Message()
    message["Content-Type"] = content_type
    return message.get_content_charset() or default


def request_bytes(
    request: Request,
    *,
    policy: SourcePolicy,
    max_bytes: int | None = None,
    timeout: float | None = None,
    opener: OpenerDirector | None = None,
) -> NetworkResponse:
    validate_url(request.full_url, policy)
    client = opener or build_policy_opener(policy)
    with client.open(request, timeout=timeout or policy.timeout) as response:
        final_url = response.geturl()
        validate_url(final_url, policy)
        headers = dict(response.headers.items())
        content = read_limited(
            response,
            headers=response.headers,
            max_bytes=max_bytes or policy.max_binary_bytes,
            label=f"{policy.source} response body",
        )
        return NetworkResponse(
            url=final_url,
            status_code=response.getcode(),
            headers=headers,
            content=content,
        )


__all__ = [
    "NetworkPolicyError",
    "NetworkResponse",
    "PolicyRedirectHandler",
    "ResourceLimitError",
    "SourcePolicy",
    "build_policy_opener",
    "policy_for_endpoint",
    "request_bytes",
    "response_charset",
    "source_policy",
    "validate_url",
]
