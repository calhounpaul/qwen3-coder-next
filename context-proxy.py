#!/usr/bin/env python3
"""Context-aware reverse proxy for llama-server.

Auto-truncates oversized tool results to prevent context window overflow.
Sits between Qwen Code and llama-server, forwarding all requests and
streaming SSE responses transparently.

Usage: context-proxy.py [listen_port] [upstream_base_url]
Example: context-proxy.py 8013 http://localhost:8003

Environment variables:
  PROXY_PORT            Listen port (default: 8013)
  PROXY_UPSTREAM        Upstream base URL (default: http://localhost:8003)
  PROXY_CONTEXT_LIMIT   Context window in tokens (default: 120000)
"""

import json
import logging
import os
import sys

try:
    from aiohttp import web, ClientSession
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "aiohttp"])
    from aiohttp import web, ClientSession

PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.getenv("PROXY_PORT", "8013"))
UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else os.getenv("PROXY_UPSTREAM", "http://localhost:8003")
CTX_TOKENS = int(os.getenv("PROXY_CONTEXT_LIMIT", "120000"))

# ~3.5 chars/token, reserve 15% of context for generation
MAX_CHARS = int(CTX_TOKENS * 3.5 * 0.85)

log = logging.getLogger("ctx-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ctx-proxy] %(message)s", datefmt="%H:%M:%S")


def shrink_if_needed(body: dict) -> dict:
    """Truncate the largest tool-result messages to fit within the context window."""
    msgs = body.get("messages")
    if not msgs:
        return body

    total = sum(len(json.dumps(m)) for m in msgs)
    if total <= MAX_CHARS:
        return body

    log.warning(
        "Request ~%d chars (~%dk tokens), limit ~%dk tokens",
        total, total // 3500, CTX_TOKENS // 1000,
    )

    # Identify tool messages, largest first
    tools = sorted(
        [(i, len(str(m.get("content", "")))) for i, m in enumerate(msgs) if m.get("role") == "tool"],
        key=lambda x: x[1],
        reverse=True,
    )
    if not tools:
        log.warning("No tool messages to truncate")
        return body

    excess = total - MAX_CHARS
    out = [dict(m) for m in msgs]

    for idx, clen in tools:
        if excess <= 0:
            break
        if clen < 500:
            continue
        trim = min(excess, clen - 300)
        if trim <= 0:
            continue

        keep = clen - trim
        content = str(out[idx]["content"])
        total_lines = content.count("\n") + 1
        kept = content[:keep]
        kept_lines = kept.count("\n") + 1
        out[idx]["content"] = (
            f"{kept}\n\n... [TRUNCATED: {kept_lines}/{total_lines} lines, "
            f"{trim} chars removed to fit {CTX_TOKENS}-token context]"
        )
        excess -= trim
        log.info("Truncated msg[%d]: %d -> %d chars", idx, clen, keep)

    return {**body, "messages": out}


async def handle(req: web.Request) -> web.StreamResponse:
    """Forward request to upstream, truncating chat completions if needed."""
    body = await req.read() if req.can_read_body else None

    # Truncate oversized chat completion requests
    if "/chat/completions" in req.path and req.method == "POST" and body:
        try:
            parsed = json.loads(body)
            parsed = shrink_if_needed(parsed)
            body = json.dumps(parsed).encode()
        except Exception as e:
            log.error("Failed to process request: %s", e)

    headers = {
        k: v for k, v in req.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    session = req.app["session"]
    async with session.request(
        req.method, f"{UPSTREAM}{req.path_qs}", headers=headers, data=body,
    ) as resp:
        # Stream SSE responses
        if resp.content_type == "text/event-stream":
            sr = web.StreamResponse(status=resp.status, headers={
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding")
            })
            await sr.prepare(req)
            async for chunk in resp.content.iter_any():
                await sr.write(chunk)
            await sr.write_eof()
            return sr

        # Non-streaming: return full response
        return web.Response(
            status=resp.status,
            body=await resp.read(),
            content_type=resp.content_type,
        )


async def on_startup(app):
    app["session"] = ClientSession()


async def on_cleanup(app):
    await app["session"].close()


app = web.Application()
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)
app.router.add_route("*", "/{path:.*}", handle)

if __name__ == "__main__":
    log.info(
        ":%d -> %s (ctx: %d tokens, max prompt: ~%dk chars)",
        PORT, UPSTREAM, CTX_TOKENS, MAX_CHARS // 1000,
    )
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)
