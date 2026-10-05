<!-- Saved 2026-10-05 from https://www.skylit.ai/docs/api-reference/dark-pool/*.md (public docs, no key). -->

# Paginated off-exchange (TRF) prints

`GET https://api.skylit.ai/v1/dark-pool/trades`

API: Flowseeker. Credits: 5.

Server-side filtered dark-pool prints from the off-exchange tape
(FINRA TRF, publisher FINN/FINC). Defaults to **today (ET)** with a
**$1,000,000** minimum notional (the blocks-by-default rule); pass
`min_notional=0` for every print of the requested `tickers`. Requests
without `tickers` need `min_notional` of at least **100,000**. The
trade-date span is capped at **31 days** per request. Prints carry
**no side, BBO, or greeks**.

**Paging.** Follow `meta.nextCursor` (pass it back as `cursor` with the
same filters) to walk any number of prints without gaps or repeats,
even while new prints arrive. `order=asc` always returns a cursor, so
repeating the last call picks up prints reported since. `offset`
(max 50,000) still works for short scrolls; it cannot be combined with
`cursor`.

**Polling.** `after` / `before` bound the trade time exactly (ISO-8601
or epoch nanoseconds), and set the trade-date range when no `date`
parameter is given.

## Authentication

Send your Skylit API key as a bearer token: `Authorization: Bearer <key>`. No other header is accepted.

## Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `tickers` | query | string | no | Comma-separated tickers to include (e.g. `AAPL,NVDA`, max 50). Omit for all names. |
| `date` | query | string (date) | no | Single trade date (`YYYY-MM-DD`, ET). Defaults to today (ET). |
| `date_start` | query | string (date) | no | Inclusive start of a trade-date range (`YYYY-MM-DD`, ET). Max span 31 days. |
| `date_end` | query | string (date) | no | Inclusive end of a trade-date range (`YYYY-MM-DD`, ET). Max span 31 days. |
| `time_start` | query | string | no | Inclusive lower bound of the time-of-day window (`HH:MM`, ET). |
| `time_end` | query | string | no | Inclusive upper bound of the time-of-day window (`HH:MM`, ET). |
| `min_notional` | query | number (double) | no | Minimum notional (USD). Defaults to 1,000,000. Pass 0 for the firehose. (default `1000000`) |
| `max_notional` | query | number (double) | no |  |
| `min_size` | query | integer | no |  (min 0) |
| `max_size` | query | integer | no |  (min 0) |
| `min_price` | query | number (double) | no |  |
| `max_price` | query | number (double) | no |  |
| `min_avg_vol` | query | number (double) | no | Minimum AvgVol — the print's size as a percent of the underlying's average daily volume (`pctAvgVol`). Prints with no known ADV are excluded when this is set. |
| `max_avg_vol` | query | number (double) | no | Maximum AvgVol (percent of average daily volume). Prints with no known ADV are excluded when this is set. |
| `sectors` | query | string | no | Comma-separated GICS sectors to include. |
| `industries` | query | string | no | Comma-separated GICS industries to include. |
| `venue` | query | string | no | Reporting venue filter. Omit for both. (one of `FINN`, `FINC`) |
| `limit` | query | integer | no | Page size (server caps at 5000). (default `500`; min 1; max 5000) |
| `offset` | query | integer | no | Row offset for pagination. (default `0`; min 0; max 50000) |
| `order` | query | string | no | Direction of `sort`. (one of `asc`, `desc`; default `desc`) |
| `sort` | query | string | no | `time` (default), `notional` or `size`. Largest-first ranking (`sort=notional`) is paged with `offset`; `nextCursor` is time-only. (one of `time`, `notional`, `size`; default `time`) |
| `cursor` | query | string | no | `meta.nextCursor` from the previous page (time sort only). |
| `after` | query | string | no | Only prints strictly after this instant (ISO-8601 or epoch nanoseconds). |
| `before` | query | string | no | Only prints strictly before this instant (ISO-8601 or epoch nanoseconds). |

## Example request

```bash
curl "https://api.skylit.ai/v1/dark-pool/trades" \
  -H "Authorization: Bearer $SKYLIT_API_KEY"
```

## Responses

### 200

Paginated dark-pool prints for the requested filters.

Shape (placeholder values):

```json
{
  "data": [
    {
      "timestamp": "2026-07-02T14:31:05.123Z",
      "ticker": "SPY",
      "price": 0,
      "size": 0,
      "notional": 0,
      "venue": "FINN",
      "sector": "string",
      "industry": "string",
      "pctAvgVol": 15.53
    }
  ],
  "meta": {
    "timestamp": "string",
    "requestId": "d7574836",
    "nextCursor": "string",
    "nextEndTime": "string",
    "limit": 0,
    "offset": 0,
    "count": 0,
    "hasMore": false
  }
}
```

### 400

Request validation failed.

invalidParam:

```json
{
  "error": {
    "code": "INVALID_PARAMETER",
    "message": "Invalid value 'bogus' for 'timeframe'. Allowed: 5m, 15m, 1h, 4h, 1d."
  }
}
```

### 401

Missing or invalid API key.

missingKey:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication required"
  }
}
```

### 402

The account's shared Skylit credit balance is lower than this route's cost. Top up to continue. Carries `X-Credits-Remaining: 0`.

Headers: `X-Credits-Remaining`.

outOfCredits:

```json
{
  "error": {
    "code": "insufficient_credits",
    "message": "Out of credits. Top up to continue making requests."
  }
}
```

### 403

Unknown, revoked or expired API key (the gateway's `forbidden`), the account's API access is suspended (`account_suspended`) or blocked (`account_blocked`). Not retryable.

badKey:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Access to this API has been disallowed"
  }
}
```

accountSuspended:

```json
{
  "error": {
    "code": "account_suspended",
    "message": "API access has been suspended for this account. Contact support."
  }
}
```

### 404

Unknown ticker or contract (`SYMBOL_NOT_FOUND`), or no data for the requested window. Not charged.

unknownSymbol:

```json
{
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "Unknown ticker 'ZZZZQ'."
  }
}
```

noData:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "No trades found for AAPL on 2026-05-27 with timeframe 1d"
  }
}
```

### 429

Either the key exceeded its requests-per-minute limit (`X-RateLimit-Limit`; no `Retry-After`, wait until `X-RateLimit-Reset`), or the account is over one of the API's own limits, such as the query budget (queries running at once, fresh queries per second): `TOO_MANY_CONCURRENT_QUERIES` or `RATE_LIMITED` with `Retry-After`. Not charged; retry after the wait.

tooFast:

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded."
  }
}
```

queryBudget:

```json
{
  "error": {
    "code": "TOO_MANY_CONCURRENT_QUERIES",
    "message": "This account already has 16 queries running on the shared query engine across all servers (limit ch.concurrent.customer). Cached answers are not affected. Retry after Retry-After; to backfill a day, follow meta.nextCursor instead of splitting time windows. See https://www.skylit.ai/docs/api-reference/rate-limits-and-retries",
    "retry_after": 1,
    "docs_url": "https://www.skylit.ai/docs/api-reference/errors#too_many_concurrent_queries"
  }
}
```

### 500

Unexpected server error (`INTERNAL_ERROR`, `DATABASE_ERROR`). Not charged; safe to retry with exponential backoff.

internal:

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Internal server error"
  }
}
```

### 503

Underlying data source temporarily unavailable, the credit balance could not be verified (`credit_check_failed`), or the API is paused for maintenance (`api_paused`, with a `Retry-After` header and a `retry_after` field in seconds). Not charged; safe to retry.

ingestionLag:

```json
{
  "error": {
    "code": "UNAVAILABLE",
    "message": "Live feed is degraded; please retry in a few seconds."
  }
}
```

creditCheckFailed:

```json
{
  "error": {
    "code": "credit_check_failed",
    "message": "Could not verify credit balance. Please retry."
  }
}
```

### 504

The request did not complete within 25 seconds. Not charged; narrow the window or retry.

tooSlow:

```json
{
  "error": {
    "code": "GATEWAY_TIMEOUT",
    "message": "The request took too long and was not completed. It was not charged; narrow the request or retry."
  }
}
```


# Largest individual dark-pool prints for a ticker

`GET https://api.skylit.ai/v1/dark-pool/top-prints/{ticker}`

API: Flowseeker. Credits: 3.

The top-N largest individual off-exchange prints for `{ticker}` over a
trailing window, ordered by notional descending. Each row is a single
TRF print (not an aggregate), useful as a support/resistance anchor.

## Authentication

Send your Skylit API key as a bearer token: `Authorization: Bearer <key>`. No other header is accepted.

## Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `ticker` | path | string | yes | Underlying ticker symbol (uppercase, e.g. `SPY`, `AAPL`). |
| `top_n` | query | integer | no | Number of largest prints to return. (default `5`; min 1; max 20) |
| `lookback_days` | query | integer | no | Calendar-day trailing window. (default `45`; min 1; max 180) |
| `as_of_date` | query | string (date) | no | Optional anchor date (`YYYY-MM-DD`); the window becomes `[as_of_date - lookback_days, as_of_date]`. Omit for a today-anchored window. |

## Example request

```bash
curl "https://api.skylit.ai/v1/dark-pool/top-prints/SPY" \
  -H "Authorization: Bearer $SKYLIT_API_KEY"
```

## Responses

### 200

Top-N largest prints for `{ticker}`, ordered by notional.

Shape (placeholder values):

```json
{
  "data": [
    {
      "timestamp": "string",
      "price": 0,
      "notional": 0,
      "size": 0
    }
  ],
  "meta": {
    "timestamp": "string",
    "requestId": "d7574836",
    "nextCursor": "string",
    "nextEndTime": "string"
  }
}
```

### 400

Request validation failed.

invalidParam:

```json
{
  "error": {
    "code": "INVALID_PARAMETER",
    "message": "Invalid value 'bogus' for 'timeframe'. Allowed: 5m, 15m, 1h, 4h, 1d."
  }
}
```

### 401

Missing or invalid API key.

missingKey:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication required"
  }
}
```

### 402

The account's shared Skylit credit balance is lower than this route's cost. Top up to continue. Carries `X-Credits-Remaining: 0`.

Headers: `X-Credits-Remaining`.

outOfCredits:

```json
{
  "error": {
    "code": "insufficient_credits",
    "message": "Out of credits. Top up to continue making requests."
  }
}
```

### 403

Unknown, revoked or expired API key (the gateway's `forbidden`), the account's API access is suspended (`account_suspended`) or blocked (`account_blocked`). Not retryable.

badKey:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Access to this API has been disallowed"
  }
}
```

accountSuspended:

```json
{
  "error": {
    "code": "account_suspended",
    "message": "API access has been suspended for this account. Contact support."
  }
}
```

### 404

Unknown ticker or contract (`SYMBOL_NOT_FOUND`), or no data for the requested window. Not charged.

unknownSymbol:

```json
{
  "error": {
    "code": "SYMBOL_NOT_FOUND",
    "message": "Unknown ticker 'ZZZZQ'."
  }
}
```

noData:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "No trades found for AAPL on 2026-05-27 with timeframe 1d"
  }
}
```

### 429

Either the key exceeded its requests-per-minute limit (`X-RateLimit-Limit`; no `Retry-After`, wait until `X-RateLimit-Reset`), or the account is over one of the API's own limits, such as the query budget (queries running at once, fresh queries per second): `TOO_MANY_CONCURRENT_QUERIES` or `RATE_LIMITED` with `Retry-After`. Not charged; retry after the wait.

tooFast:

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded."
  }
}
```

queryBudget:

```json
{
  "error": {
    "code": "TOO_MANY_CONCURRENT_QUERIES",
    "message": "This account already has 16 queries running on the shared query engine across all servers (limit ch.concurrent.customer). Cached answers are not affected. Retry after Retry-After; to backfill a day, follow meta.nextCursor instead of splitting time windows. See https://www.skylit.ai/docs/api-reference/rate-limits-and-retries",
    "retry_after": 1,
    "docs_url": "https://www.skylit.ai/docs/api-reference/errors#too_many_concurrent_queries"
  }
}
```

### 500

Unexpected server error (`INTERNAL_ERROR`, `DATABASE_ERROR`). Not charged; safe to retry with exponential backoff.

internal:

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "Internal server error"
  }
}
```

### 503

Underlying data source temporarily unavailable, the credit balance could not be verified (`credit_check_failed`), or the API is paused for maintenance (`api_paused`, with a `Retry-After` header and a `retry_after` field in seconds). Not charged; safe to retry.

ingestionLag:

```json
{
  "error": {
    "code": "UNAVAILABLE",
    "message": "Live feed is degraded; please retry in a few seconds."
  }
}
```

creditCheckFailed:

```json
{
  "error": {
    "code": "credit_check_failed",
    "message": "Could not verify credit balance. Please retry."
  }
}
```

### 504

The request did not complete within 25 seconds. Not charged; narrow the window or retry.

tooSlow:

```json
{
  "error": {
    "code": "GATEWAY_TIMEOUT",
    "message": "The request took too long and was not completed. It was not charged; narrow the request or retry."
  }
}
```
