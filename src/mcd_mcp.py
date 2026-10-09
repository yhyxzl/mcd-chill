"""麦当劳中国 MCP Server 的最小 Streamable HTTP 客户端。

不依赖任何 LLM 客户端或第三方 SDK，只用标准库完成 MCP 握手与工具调用。
服务端为无状态实现：initialize 不返回 Mcp-Session-Id，因此每个请求都是独立的 POST。

用量提示：单个 Token 限流 600 次/分钟，超限返回 429。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "mcd-chill", "version": "0.1.0"}

READ_ONLY_TOOLS = frozenset(
    {
        "now-time-info",
        "list-nutrition-foods",
        "order-list",
        "query-order",
        "query-my-account",
        "query-my-coupons",
        "query-my-prizes",
        "available-coupons",
        "campaign-calendar",
        "mall-points-products",
        "mall-product-detail",
        "mall-order-list",
        "mall-order-detail",
        "query-lottery-info",
        "query-nearby-stores",
        "query-meals",
        "query-meal-detail",
        "query-store-coupons",
        "query-promotions",
        "query-meal-assistance",
        "delivery-query-addresses",
        "delivery-query-stores",
        "query-party-city",
        "query-party-store",
        "query-party-store-date",
        "query-party-store-session",
        "query-survey-coupon",
    }
)


class McpError(RuntimeError):
    """MCP 调用失败。"""


class McdMcpClient:
    """面向 https://mcp.mcd.cn 的 JSON-RPC / MCP 客户端。"""

    def __init__(
        self,
        token: str | None = None,
        url: str | None = None,
        timeout: int = 30,
        retries: int = 3,
    ) -> None:
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise McpError(
                "缺少 MCP Token。请设置环境变量 MCD_MCP_TOKEN，"
                "或在构造 McdMcpClient 时显式传入 token。"
            )
        self.url = url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)
        self.timeout = timeout
        self.retries = max(1, retries)
        self._seq = 0
        self._session_id: str | None = None
        self._ready = False

    # ---------------------------------------------------------------- transport

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        last_error: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                time.sleep(0.8 * attempt)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    session_id = response.headers.get("Mcp-Session-Id")
                    if session_id:
                        self._session_id = session_id
                    raw = response.read().decode("utf-8", "replace")
                    content_type = response.headers.get("Content-Type", "")
                return self._decode(raw, content_type)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:600]
                hint = {
                    401: "MCP Token 无效或已过期，请重新在 open.mcd.cn/mcp 申请。",
                    429: "触发限流（600 次/分钟），请降低调用频率。",
                }.get(exc.code, "")
                if exc.code < 500:
                    raise McpError(f"HTTP {exc.code} {hint} {detail}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

        raise McpError(f"网络不可达（已重试 {self.retries} 次）：{last_error}") from last_error

    @staticmethod
    def _decode(raw: str, content_type: str) -> dict[str, Any]:
        """兼容 application/json 与 text/event-stream 两种响应体。"""
        stripped = raw.lstrip()
        if "text/event-stream" in content_type or stripped.startswith("event:") or "\ndata:" in raw:
            messages: list[dict[str, Any]] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if chunk and chunk != "[DONE]":
                    messages.append(json.loads(chunk))
            if not messages:
                raise McpError(f"SSE 响应中没有 data 段：{raw[:200]}")
            return messages[-1]
        if not stripped:
            return {}
        return json.loads(raw)

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._seq, "method": method}
        if params is not None:
            payload["params"] = params
        message = self._post(payload)
        if "error" in message:
            error = message["error"]
            raise McpError(f"{method} 失败：[{error.get('code')}] {error.get('message')}")
        return message.get("result", {})

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    # ------------------------------------------------------------------ protocol

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self._notify("notifications/initialized")
        self._ready = True
        return result

    def ensure_ready(self) -> None:
        if not self._ready:
            self.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用工具，返回原始 result（含 content / structuredContent / isError）。"""
        self.ensure_ready()
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    # ------------------------------------------------------------------ helpers

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用工具并直接返回数据体。

        优先取 structuredContent（服务端结构化结果），
        回退到把 content 里的文本段解析成 JSON，最后才原样返回文本。
        """
        result = self.call_tool(name, arguments)
        if result.get("isError"):
            raise McpError(f"{name} 返回错误：{self._text_of(result)[:300]}")

        structured = result.get("structuredContent")
        if structured is not None:
            return structured

        text = self._text_of(result)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    @staticmethod
    def _text_of(result: dict[str, Any]) -> str:
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)


def unwrap(value: Any) -> Any:
    """剥掉常见的外层包装，拿到真正的业务数据。

    麦当劳 MCP 的返回经常形如 {"data": {...}} 或 {"code": 0, "data": [...]}，
    这里统一剥到最内层的数据体，方便上层逻辑直接消费。
    """
    seen = 0
    while isinstance(value, dict) and seen < 4:
        keys = set(value)
        if keys & {"data", "result", "body"}:
            inner = value.get("data", value.get("result", value.get("body")))
            if inner is not None and inner != value:
                value = inner
                seen += 1
                continue
        break
    return value


def as_list(value: Any, *candidate_keys: str) -> list[Any]:
    """从任意嵌套结构里尽力找一个列表出来。"""
    current = unwrap(value)
    if isinstance(current, list):
        return current
    if isinstance(current, dict):
        for key in candidate_keys:
            if isinstance(current.get(key), list):
                return current[key]
        for item in current.values():
            if isinstance(item, list):
                return item
    return []
