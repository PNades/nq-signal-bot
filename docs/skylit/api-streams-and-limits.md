<!-- Saved 2026-10-05 from https://www.skylit.ai/docs/api-reference/heatmap/live-sse-stream-up-to-10-symbols-per-connection.md and rate-limits-and-retries.md (public docs, no key). -->

# Live SSE stream (up to 10 symbols per connection)

`GET https://api.skylit.ai/v1/stream`

API: Heatseeker. Credits: 1 per symbol to open + 1 per symbol per minute open.

A **Server-Sent Events** (`text/event-stream`) feed of live per-strike
heatmaps. Two wire formats:

- **v2** (default with `symbols=`, or `format=v2`): up to 10 symbols on
  one connection, camelCase events matching `SymbolHeatmap`, resumable
  with `Last-Event-ID`.
- **v1** (default with `symbol=`): the original single-symbol format,
  unchanged.

### v2 events

Every event names its `symbol` (except connection-level events).

| Event | `id:` | Payload |
|---|---|---|
| `connected` | — | `{format:"v2", symbols, metric, creditsRemaining, creditsPerMinute, maxDurationSeconds}` |
| `resumed` | yes | Only when `Last-Event-ID` was sent. See *Resume*. |
| `snapshot` | yes | `StreamSnapshot` (= `SymbolHeatmap` + `lagged`). Once per symbol on connect, then on every board change. |
| `velocity` | yes | `StreamVelocity`: per-node velocity for the stream's `metric`, when it changes (checked every ~3s). |
| `symbol_unavailable` | — | `{symbol, reason: "no_live_board" \| "expiration_not_found", message}`; a `snapshot` follows once a board exists. |
| `credits` | — | `{remaining, charged}` after each per-minute debit. |
| `closed` | — | `{reason: "insufficient_credits" \| "account_suspended" \| "key_revoked" \| "monthly_cap_reached" \| "credit_check_failed"}`; stream ends. `key_revoked`: an API key on the account was revoked after this stream opened (streams opened on another still-valid key close too; reconnect to continue). `monthly_cap_reached`: the account's monthly spend cap was reached. |
| `reconnect` | yes | `{reason: "max_duration" \| "server_shutdown"}`; stream ends, reconnect with `Last-Event-ID`. |
| `: ping` | — | SSE comment every 15s so idle proxies (CloudFront: 60s) keep the connection. |

Snapshots differ from `/v1/heatmap` in two ways: `spot` is the board's
own spot, and strikes carry no `velocityPct` (use the `velocity`
event). Within a symbol, `asOf` never goes backwards; a board already
sent is never re-sent.

**Event ids (resume cursor).** The `id:` is the connection's position
in **every** symbol, not just the event's: a comma-separated list of
`SYMBOL:asOfUnixMs`, e.g. `SPY:1790000000123,QQQ:1790000000456`.
`asOfUnixMs` is the board's publish timestamp, identical on every
server, so a cursor resumes on any pod. Per symbol it never decreases
and strictly increases with each `snapshot` for that symbol.

**Resume.** Reconnect with header `Last-Event-ID: <last id>` (browsers'
`EventSource` does this automatically) or `?lastEventId=`. The server
keeps no event history: boards are full state, so it sends the
`resumed` event and then a fresh `snapshot` for every symbol that has a
live board, whether or not it changed. Per symbol `status` is:
`unchanged` (no board was published during the gap — nothing missed),
`advanced` (at least one board was published; intermediate boards are
**not** replayed, only the latest), `unknown` (symbol not in the
cursor), or `unavailable` (no live board right now). `gapCovered` is
true only if every symbol is `unchanged`; `stateRestored` is true when
every symbol received a current snapshot. Velocity is not replayed; the
latest reading is re-sent when this server has one.

**Backpressure.** A client that reads slower than boards are published
is never queued behind: each symbol holds at most one pending board, a
newer board replaces it, and the board that is finally sent carries
`lagged: true`. A client that stops reading for 30s is disconnected.

**Pricing (v2).** `N` = number of symbols. `N` credits on connect
(charged before the SSE upgrade, so an under-funded client gets a clean
`402`), then `N` credits per minute open — 1 credit ($0.001) per symbol
per minute, i.e. $0.06 per symbol-hour, plus $0.001 per symbol to open.
Minutes are charged in advance and a partial minute is not refunded.

**Limits.** At most 10 symbols per connection (more → `400`). At most
25 symbols per customer per server across all open streams (a v1
stream counts as 1); over it → `429` `stream_limit_reached` with
`Retry-After: 2` (a just-closed stream frees its symbols within about
a second). v1 also keeps its 5-streams cap. Each server streams a
bounded number of **distinct** symbols across all customers (default
20); a v2 stream that would add a new symbol beyond that gets `503`
`stream_capacity` with `Retry-After: 5` — retry (you may land on
another server). Symbols already being streamed are always admitted.
Streams end after 1 hour with `reconnect`.

### v1 events (single symbol, unchanged)

`connected` `{symbol, creditsRemaining}`, `initial_data`,
`snapshot_update`, `velocity_update` (internal PascalCase board shape),
`credits`, `closed`, `reconnect`, and a `: keepalive` comment every
30s. 1 credit to open + 1 per minute. v1 rejects `expirations`.

> OpenAPI cannot fully model an event stream. See
> https://www.skylit.ai/docs/api-reference/rate-limits-and-retries (Streams).

## Authentication

Send your Skylit API key as a bearer token: `Authorization: Bearer <key>`. No other header is accepted.

## Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `symbols` | query | string | no | Comma-separated tickers, up to 10 (e.g. `SPY,QQQ,IWM`). Selects format v2 by default. Unknown symbols → `404`. |
| `symbol` | query | string | no | Single ticker. Selects the legacy v1 format unless `format=v2`. Send `symbol` or `symbols`, not both. |
| `format` | query | string | no | Wire format. Defaults to `v2` with `symbols=` and `v1` with `symbol=`. `v1` is single-symbol only. (one of `v1`, `v2`) |
| `lastEventId` | query | string | no | v2 resume cursor, for clients that cannot set the `Last-Event-ID` header. The header wins if both are sent. |
| `Last-Event-ID` | header | string | no | v2 resume cursor — the last `id:` received. |
| `metric` | query | string | no | Which Greek exposure to return per strike. (one of `gamma`, `vanna`; default `gamma`) |
| `maxStrikes` | query |  | no | Maximum number of strikes around spot to return: an integer from 1 to 1000, or `all` for every strike the snapshot lists (SPXW lists about 730). Values above 1000 return `400 invalid_parameter`; they are never silently reduced. Values below 1 are treated as 1. The single-symbol stream (`/v1/stream?symbol=`) accepts at most 400 and no `all`. (default `92`) |
| `maxExpirations` | query |  | no | How many of the nearest expirations to net into each strike's `value`: an integer from 1 to 60, or `all`. Values above 60 return `400 invalid_parameter`. Ignored when `expirations` is set. (default `5`) |
| `includeEmpty` | query | boolean | no | By default, an interior strike whose every returned cell is below 50 in absolute value (listed but effectively untraded) is left out, judged on the selected metric alone. So gamma and vanna for the same instant can return different strike lists. The outermost strikes are never removed. `true` keeps every strike in the window, so the list is the snapshot's own contiguous ladder and is identical for gamma and vanna. Not supported on the single-symbol stream (`symbol=`). (default `false`) |
| `expirations` | query | string | no | v2 only — same meaning as on `/v1/heatmap`. Rejected with `400` on v1. |
| `layout` | query | string | no | v2 only — `matrix` adds the per-expiration grid to each snapshot, as on `/v1/heatmap`. (one of `net`, `matrix`; default `net`) |

## Example request

```bash
curl -N "https://api.skylit.ai/v1/stream" \
  -H "Authorization: Bearer $SKYLIT_API_KEY" \
  -H "Accept: text/event-stream"
```

## Responses

### 200

An SSE stream of heatmap events.

`text/event-stream`: v2 frames, e.g. event: snapshot id: SPY:1790000000123,QQQ:1790000000456 data: {StreamSnapshot} `snapshot` data matches #/components/schemas/StreamSnapshot, `velocity` data #/components/schemas/StreamVelocity, and `resumed` data #/components/schemas/StreamResumed.

### 400

Request validation failed.

### 401

Missing API key (`unauthorized`), sent by the gateway.

### 402

Out of credits (`insufficient_credits`).

### 403

Unknown, revoked or expired key (`forbidden`, from the gateway), or an
admin-suspended account (`account_suspended`).

### 404

Unknown symbol, no data available, or none of the requested
`expirations` exist for the symbol (`code: expiration_not_found`).

### 429

Per-minute rate limit exceeded.

Headers: `Retry-After`.

### 503

Heatmap data is temporarily unavailable.


# Rate limits and retries for agents

> How fast an agent may call the Skylit API, which errors to retry, and copy-paste backoff code.

This page is written for AI agents and the people who build them. Follow it
literally and your agent will never be throttled for long, never pay twice for a
failure, and never hammer an endpoint that can't answer.

## The limits

| Limit | Value | What happens over it |
| --- | --- | --- |
| Requests per key | Set by your plan: `limits.requestsPerMinute` in `GET /v1/account` | `429` `rate_limited` from the gateway |
| Requests per source IP, all keys combined | an edge ceiling far above one key's limit, counted over the last 5 minutes | `429` [`ip_rate_limited`](/docs/api-reference/errors#ip_rate_limited) from the edge, with `Retry-After: 60` |
| Symbols per Heatseeker call | `limits.symbolsPerHeatmapCall` (at most 10) | `400` `invalid_parameter` (never silently truncated) |
| Symbols per Flowseeker list parameter | 50 | `400` `INVALID_PARAMETER` |
| Historical replays in flight per account | `limits.historicalInFlight` | `429` `too_many_concurrent_requests`, with `Retry-After` |
| Flowseeker requests in flight per account | a small fixed cap | `429` `TOO_MANY_CONCURRENT_QUERIES`, with `Retry-After` |
| Streams | `limits.symbolsPerStream` per connection, `limits.streamSymbolsConcurrent` across the account, and a per-plan number of open streams | `429` `stream_limit_reached`, with `Retry-After` |
| Time per request | 25 s (Flowseeker, Atlas) | `504` `gateway_timeout`, refunded |

`GET /v1/account` (free) returns your current limits in `data.limits`. Read them at
start-up instead of hard-coding them. Today Pro, Developer, Initiate and
Community get 120 requests per minute per key, with up to 5 keys (600 per
account), and an invite code gets 60 per key with 2 keys (120 per account). See
[Plans and credits](https://www.skylit.ai/docs/api-reference/plans-and-credits).

The per-minute limit applies to each key and counts its requests to every Skylit
host (`api`, `flow-api`, `atlas-api`) together. Credits are what you spend. See
[Authentication](https://www.skylit.ai/docs/api-reference/authentication#credits).

## Headers to read

| Header | On | Meaning |
| --- | --- | --- |
| `X-RateLimit-Limit` | every response | Requests allowed per minute on this key (your plan's limit). |
| `X-RateLimit-Remaining` | every response | Requests left in the current window. |
| `X-RateLimit-Reset` | every response | Unix time, in seconds, when the window resets. |
| `Retry-After` | some `429` and `503` responses | Seconds to wait before retrying. |
| `X-Credits-Remaining` | chargeable responses | Your credit balance after this call, refunds included. |

> **Note:** **When you get a `429`: honor `Retry-After` when it's present, otherwise sleep
> until `X-RateLimit-Reset` plus a little random jitter.** The gateway's own `429`
> (code `rate_limited`, `X-RateLimit-Remaining: 0`) doesn't send `Retry-After`. The
> API's concurrency and stream `429`s do, and so does the edge's per-IP `429`
> (code `ip_rate_limited`, `Retry-After: 60`, no `X-RateLimit-*` headers). The rule
> above works for all of them.

To stay under the ceiling without ever seeing a `429`, pace yourself: when
`X-RateLimit-Remaining` gets low, sleep until `X-RateLimit-Reset`.

## What to retry

Every public endpoint is a `GET` and has no side effects, so any request is safe
to repeat. Failed requests are refunded (any `4xx` or `5xx`), so a retry never
costs you twice.

| Status | Retry? | What to do |
| --- | --- | --- |
| `429` | Yes | Wait `Retry-After`, or until `X-RateLimit-Reset`. Then retry. For the concurrency codes, also send fewer requests in parallel. |
| `500`, `502` | Yes | Exponential backoff with jitter (below). |
| `503` | Yes | Wait `Retry-After` when present, otherwise back off. |
| `504` | Yes | Back off, and ask for a smaller time window if it happens again. |
| `400`, `404`, `405`, `422` | No | The request is wrong. Read `error.message`, fix the request, then send it again. |
| `401` | No | No key was sent. Fix the `Authorization` header. |
| `402` | No | Out of credits, or the monthly cap is reached. Stop and tell the user. |
| `403` | No | Bad key, suspended account, or no access to this data. Stop and tell the user. |
| `501` | No | Not built yet. |
| Network error or timeout | Yes | Back off, as for `503`. |

Every error code, with what it means, is on [Errors](https://www.skylit.ai/docs/api-reference/errors).

## Backoff

Use exponential backoff with **full jitter**:

- wait a random time between 0 and `min(30 s, 1 s × 2^attempt)`;
- when the response has `Retry-After`, wait at least that long;
- give up after **5 attempts** and report the error, with its `code`, to the user.

Jitter matters. Without it, many agents that failed at the same moment all
retry at the same moment, and fail again.

```python Python
import os, random, time
import requests

API = "https://api.skylit.ai"
RETRYABLE = {429, 500, 502, 503, 504}
session = requests.Session()
session.headers["Authorization"] = f"Bearer {os.environ['SKYLIT_API_KEY']}"

def skylit_get(path, params=None, max_attempts=5, base=1.0, cap=30.0):
    for attempt in range(max_attempts):
        try:
            r = session.get(API + path, params=params, timeout=35)
        except requests.RequestException:
            r = None  # network error: retry
        if r is not None and r.status_code < 400:
            return r.json()
        if r is not None and r.status_code not in RETRYABLE:
            try:
                err = r.json().get("error")
            except ValueError:
                err = None
            # Most errors are {"error": {"code", "message"}}; a few edge errors
            # carry a plain string. Key off `code` when it's there.
            if isinstance(err, dict):
                raise RuntimeError(f"{r.status_code} {err.get('code')}: {err.get('message')}")
            raise RuntimeError(f"{r.status_code}: {err or r.text[:200]}")
        if attempt == max_attempts - 1:
            break
        wait = random.uniform(0, min(cap, base * 2 ** attempt))
        if r is not None:
            if r.headers.get("Retry-After"):
                wait = max(wait, float(r.headers["Retry-After"]))
            elif r.status_code == 429 and r.headers.get("X-RateLimit-Reset"):
                reset_in = float(r.headers["X-RateLimit-Reset"]) - time.time()
                wait = max(wait, reset_in + random.uniform(0, 1))
        time.sleep(max(wait, 0))
    raise RuntimeError(f"gave up on {path} after {max_attempts} attempts")

print(skylit_get("/v1/flow/SPY", {"limit": 10})["data"]["tradeCount"])
```

```ts TypeScript
const API = "https://api.skylit.ai";
const RETRYABLE = new Set([429, 500, 502, 503, 504]);
const headers = { Authorization: `Bearer ${process.env.SKYLIT_API_KEY}` };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function skylitGet(path: string, params: Record<string, string> = {},
                                maxAttempts = 5, baseMs = 1000, capMs = 30000) {
  const url = `${API}${path}?${new URLSearchParams(params)}`;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let res: Response | null = null;
    try {
      res = await fetch(url, { headers, signal: AbortSignal.timeout(35000) });
    } catch {
      res = null; // network error or timeout: retry
    }
    if (res && res.ok) return res.json();
    if (res && !RETRYABLE.has(res.status)) {
      const body = await res.json().catch(() => null);
      const error = body?.error;
      // Most errors are {error: {code, message}}; a few edge errors carry a plain string.
      if (error && typeof error === "object")
        throw new Error(`${res.status} ${error.code}: ${error.message}`);
      throw new Error(`${res.status}: ${error ?? res.statusText}`);
    }
    if (attempt === maxAttempts - 1) break;
    let waitMs = Math.random() * Math.min(capMs, baseMs * 2 ** attempt);
    const retryAfter = res?.headers.get("Retry-After");
    const reset = res?.headers.get("X-RateLimit-Reset");
    if (retryAfter) waitMs = Math.max(waitMs, Number(retryAfter) * 1000);
    else if (res?.status === 429 && reset)
      waitMs = Math.max(waitMs, Number(reset) * 1000 - Date.now() + Math.random() * 1000);
    await sleep(Math.max(waitMs, 0));
  }
  throw new Error(`gave up on ${path} after ${maxAttempts} attempts`);
}
```

## Concurrency

- Run historical replays one or two at a time. A third concurrent replay gets `429`.
- Keep Flowseeker requests to a few in parallel per account, not dozens.
- Batch instead of fanning out: one Heatseeker call takes up to 10 symbols, and
  Flowseeker list parameters (`tickers`, `symbols`) take up to 50.
- Poll no faster than the data changes. `/v1/heatmap` is cached for 5 seconds.
  For live updates, open a stream instead of polling.

## Pages and time windows

- Endpoints with `limit` and `offset` (for example `/v1/dark-pool/trades`) page
  by `offset`. Stop when a page returns fewer rows than `limit`.
- Range caps are published on each endpoint and enforced up front with a `400`
  that names the cap. Split wide ranges into windows under the cap rather than
  retrying the wide one.
- Atlas `/v1/history` (on `https://atlas-api.skylit.ai`) is capped by trading days per request. The `400` carries
  `max_days`: page backwards in windows of `max_days` or fewer.
- A short or empty result means the data ends there. The API never silently
  shortens a range.

## Streams

- Open one stream with several symbols (`symbols=SPY,QQQ`) rather than one stream
  per symbol.
- Streams bill **per symbol**: 1 credit per symbol to open, then 1 credit per
  symbol per minute. A symbol with no live board yet (`symbol_unavailable`, for
  example outside market hours) still counts. The `connected` event states
  `creditsPerMinute`, and a `credits` event follows each debit.
- Streams close after `maxDurationSeconds` (one hour). Reconnect, and send the last event ID you saw
  (`Last-Event-ID` header, or the `lastEventId` query parameter) to resume
  without gaps.
- On a dropped connection, reconnect with the same backoff as above.
- A `reconnect` event (`max_duration` or `server_shutdown`) means: reconnect now
  with `Last-Event-ID`.
- A final `closed` event carries a `reason`. Reconnect only after
  `credit_check_failed`. For `insufficient_credits`, `account_suspended`,
  `monthly_cap_reached`, `daily_cap_reached`, `key_revoked` or `access_withdrawn`, stop and tell the
  user.
- Lines starting with `:` are keep-alive pings (every 15 s). Ignore them, and
  treat 60 s of silence as a dropped connection.

## MCP

MCP tools wrap the REST endpoints one-to-one, so the same limits apply. A tool
call the API rejects returns a result with `isError: true` that carries the API
error code. It's refunded, like any failed REST call. Retry it only when the
underlying status is retryable (`429` or `5xx`), with the same backoff. Fix the
arguments for anything else. A JSON-RPC error `-32602` (invalid params) means an
argument has the wrong type or name: for example, `symbols` is one
comma-separated string (`"SPY,QQQ"`), not a list.
