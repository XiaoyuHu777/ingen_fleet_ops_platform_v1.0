# Week 2 Fleet Registry API contract

The registry exposes a versioned API under `/v1`. The Aido Rover examples below
also apply to Fari. `GET /openapi.json` exposes the machine-readable schema and
`/docs` the interactive reference. This contract is for the Week 2 local
prototype; artifact publishing, an agent, and a rollout controller are later work.

## Conventions

- JSON bodies use `Content-Type: application/json`; unknown fields are rejected.
  Heartbeat bodies are limited to 2,048 bytes and other `/v1/` bodies to 65,536
  bytes. Over-limit requests return HTTP 413. Names are trimmed strings of 1–128
  characters; `platform` and `agent_version` are limited to 100. Digests are `sha256:` followed
  by exactly 64 lowercase hexadecimal digits. A digest is an identifier, not
  evidence that an artifact exists, is authentic, or has passed evaluation.
- Robot IDs are UUIDs. API timestamps are UTC ISO-8601 strings, for example
  `2026-10-30T12:34:56Z`; nullable timestamps mean no event has yet been recorded.
- `Authorization: Bearer <token>` is required: operator key for administrative
  endpoints, the individual robot credential for that robot's heartbeat and
  desired-state read. Robot credentials are generated with high entropy, returned
  only on first successful enrollment, and stored as SHA-256 hashes. The operator
  key is configured separately. Never place real credentials in scripts, logs,
  source control, URLs, or screenshots.
- A supplied `X-Request-ID` containing 1–128 letters, numbers, `.`, `_`, or `-`
  is echoed; otherwise the server generates a UUID. Errors use
  `{"error":{"code":"...","message":"...","request_id":"..."}}` and the
  response header also carries the request ID. Avoid exposing request content in
  error messages.
- The service is a prototype. Use TLS and appropriate secret handling beyond
  the loopback local environment; it does not yet implement artifact validation,
  a safety interlock, or an individual operator audit trail.

In the examples, set `BASE=http://127.0.0.1:8000`, set `OPERATOR_KEY` in your
shell to the configured development operator key, and use the generated UUID as
`ROBOT_ID`. Set `ROBOT_TOKEN` to the one-time value from enrollment. These are
shell variables for a local demonstration; they are not supplied by this document.
The commands use `curl`; do not retain enrollment output or terminal history in a
shared place.

## Data representations

A robot representation contains:

| Field | Type and meaning |
| --- | --- |
| `id` | Server-generated UUID. |
| `platform` | Required name, e.g. `rover` or `fari`. |
| `hardware_profile` | Required JSON object describing hardware; keys and values are not standardized or verified in Week 2. |
| `site` | Required name. |
| `cohort_labels` | Array of at most 32 names; defaults to an empty array. |
| `safe_update_window` | Nullable absolute or daily local-time interval, described below. This metadata does not authorize or execute an update. |
| `created_at`, `updated_at` | Server timestamps for registration and changes to registration metadata. |
| `reported_state` | Observation from a robot heartbeat, described below. |
| `desired_state` | Operator-set target, described below. |

A `safe_update_window` may be one of two half-open intervals `[start, end)`:

- Absolute: `{"start":"2026-11-01T00:00:00Z","end":"2026-11-01T02:00:00Z"}`.
  Both timestamps require seconds and `Z` or an explicit `+/-HH:MM` offset;
  optional fractional seconds are accepted. Start must precede end. Values are
  normalized to UTC.
- Daily: `{"start_local":"22:00","end_local":"06:00","timezone":"America/New_York"}`.
  Times are `HH:MM` in 24-hour form, and timezone must be an available IANA zone.
  Equal times are invalid; an end earlier than the start denotes the next day.
  This is an illustrative configuration, not a product-specific default.

The service validates and stores these fields; it does not calculate occurrences,
resolve ambiguous or nonexistent local times during daylight-saving transitions,
or enforce a window. Those behaviors require an explicit later policy and tests.
A window alone does not establish that a robot is safe to update.

`reported_state` includes nullable `current_digest`, `previous_digest`,
`agent_version`, and `last_seen`, an object `health_summary`, and integer
`heartbeat_sequence` (initially 0). `last_seen` is server receipt time, not a
robot-supplied clock. `health_summary` is unstandardized JSON; the list `health`
filter compares its `status` property. Do not put personal data in it. Missing
heartbeats do not automatically change a robot's status.

`desired_state` has nullable `target_digest`, `set_by`, `reason`, and `set_at`.
Initially all are null. Setting a null digest explicitly clears the target.
`set_by` is a caller-supplied label, not independently authenticated identity.
A desired target and a robot's report may legitimately differ; clients should
never infer convergence from the target alone.

## Endpoints

### Enroll: `POST /v1/robots` (operator)

Required: `platform`, `hardware_profile`, `site`. Optional: `cohort_labels`,
`safe_update_window`. The response is `{ "robot": <robot>, "credential": <token> }`.
It is HTTP 201 for a new enrollment. An optional `Idempotency-Key` (1–128
characters) associates a request with the created robot. A repeated matching
request returns HTTP 200 and the existing robot with `credential: null`; a
mismatched request using the same key returns 409 `idempotency_conflict`. The
credential cannot be recovered on retry, so securely store it from the initial
response. A failed delivery of that response requires an operational recovery
procedure; there is no credential reissue endpoint in Week 2.

```sh
curl -i "$BASE/v1/robots" -H "Authorization: Bearer $OPERATOR_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: rover-enroll-001' \
  -d '{"platform":"rover","hardware_profile":{"compute":"example"},"site":"site-a","cohort_labels":["canary"]}'
```

### List: `GET /v1/robots` (operator)

Optional query parameters `platform`, `site`, `cohort`, `version`, and `health`
filter by exact platform, site, one cohort label, reported current digest, and
`health_summary.status`, respectively. Filters combine with AND. `limit` is
1–100 (default 50); results are ordered by UUID. The response has `items` and
nullable `next_cursor`. Pass a returned cursor unchanged as `cursor` for the
next page. Pagination is not a snapshot: concurrent additions or changes may
alter later pages.

```sh
curl -G "$BASE/v1/robots" -H "Authorization: Bearer $OPERATOR_KEY" \
  --data-urlencode 'platform=rover' --data-urlencode 'site=site-a' \
  --data-urlencode 'cohort=canary' --data-urlencode 'health=healthy' \
  --data-urlencode 'limit=25'
```

### Read registration: `GET /v1/robots/{id}` (operator)

Returns HTTP 200 and the robot representation, or 404 if the UUID is unknown.

```sh
curl "$BASE/v1/robots/$ROBOT_ID" -H "Authorization: Bearer $OPERATOR_KEY"
```

### Modify registration: `PATCH /v1/robots/{id}` (operator)

Only supplied metadata fields change. Objects and arrays are replaced in full,
not merged; `safe_update_window: null` clears it. The other fields cannot be
null. Returns the updated robot. Reported and desired state are not changed.

```sh
curl -X PATCH "$BASE/v1/robots/$ROBOT_ID" -H "Authorization: Bearer $OPERATOR_KEY" \
  -H 'Content-Type: application/json' -d '{"cohort_labels":["pilot"],"safe_update_window":{"start":"2026-11-01T00:00:00Z","end":"2026-11-01T02:00:00Z"}}'
```

### Heartbeat: `POST /v1/robots/{id}/heartbeat` (that robot)

`sequence` is a required integer from 1 to 9223372036854775807 and must strictly
increase for this robot, including after restart. `agent_version` is required;
`current_digest` and `previous_digest` default to null, and `health_summary`
defaults to `{}`. Each heartbeat replaces these reported fields. An accepted
heartbeat returns the robot representation and updates server-side `last_seen`.
A duplicate or older sequence returns 409 `heartbeat_replay` and must not refresh
`last_seen`.

```sh
curl -X POST "$BASE/v1/robots/$ROBOT_ID/heartbeat" -H "Authorization: Bearer $ROBOT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"sequence":1,"agent_version":"0.1.0","current_digest":null,"previous_digest":null,"health_summary":{"status":"healthy"}}'
```

### Poll desired state: `GET /v1/robots/{id}/desired-state` (that robot)

Returns HTTP 200 and the desired-state object with an `ETag`. Send the returned
value verbatim as `If-None-Match` on the next request. If unchanged, the server
returns 304 with no body and the ETag. Both requests authenticate the robot, and
neither poll changes stored state. A conditional poll is not a heartbeat.

```sh
curl -i "$BASE/v1/robots/$ROBOT_ID/desired-state" -H "Authorization: Bearer $ROBOT_TOKEN"
curl -i "$BASE/v1/robots/$ROBOT_ID/desired-state" -H "Authorization: Bearer $ROBOT_TOKEN" \
  -H "If-None-Match: $ETAG"
```

Set `ETAG` to the full value from the first response, including its quotes.

### Set desired state: `PUT /v1/robots/{id}/desired-state` (operator)

The complete body requires `target_digest` (a digest or null), `set_by`, and
`reason`. The server records `set_at` and returns the desired-state object. Each
accepted write records a new timestamp, even if the same target is supplied.

```sh
curl -X PUT "$BASE/v1/robots/$ROBOT_ID/desired-state" -H "Authorization: Bearer $OPERATOR_KEY" \
  -H 'Content-Type: application/json' -d '{"target_digest":null,"set_by":"operator","reason":"clear target"}'
```

### Revoke: `POST /v1/robots/{id}/revoke` (operator)

Revokes the robot credential and returns HTTP 204 without a body; repeating the
operation is safe. Its next authenticated robot request is rejected. Revocation
cannot force an offline robot to stop, and Week 2 does not provide a reissue or
re-enrollment endpoint for the same ID.

```sh
curl -i -X POST "$BASE/v1/robots/$ROBOT_ID/revoke" -H "Authorization: Bearer $OPERATOR_KEY"
```

## Errors

| HTTP | Code | Meaning |
| --- | --- | --- |
| 401 | `unauthorized` | Missing, incorrect, wrong-robot, or revoked credential. |
| 404 | `not_found` | The requested robot does not exist. |
| 404, 405 | `http_error` | Route does not exist or method is not allowed. |
| 409 | `idempotency_conflict` | An enrollment key was reused with a different body. |
| 409 | `heartbeat_replay` | Sequence is not greater than the last accepted sequence. |
| 413 | `body_too_large` | Request body exceeds the endpoint limit. |
| 422 | `validation_error` | Body, path, query, or header failed validation. |
| 500 | `internal_error` | Unexpected server error; consult server logs using the request ID. |

Errors carry the request ID as described above. 5xx responses signal server or
dependency failure, and clients should avoid assuming an operation succeeded.
Use an idempotency key for enrollment retries and treat credentials as one-time.

## Security and threat model

**Assets and boundaries.** The registry holds operator-set targets, robot
identity and reported state. An operator credential and per-robot bearer
credentials represent different principals. Authentication alone does not prove
that a reported artifact was verified, or that an update is safe. Deployments
must protect bearer tokens in transit with TLS and restrict database and network
access. The local example is intended for loopback development, not production.

**Stolen robot.** An attacker with its bearer credential can impersonate that
robot until the credential is revoked. It must not grant administrative access
or access to another robot's endpoints. Revocation is checked when a request is
authenticated; an offline robot cannot be notified instantly. The operator should
revoke the credential and investigate its last reports. Credentials hashed at
rest reduce exposure from a database-only leak; they do not protect a token
already stolen from a robot. Hardware-backed key protection and reenrollment
remain future work.

**Leaked operator key.** Its holder can perform operator actions until the key is
replaced. Rotate the configured key, restart or redeploy as required, investigate
administrative changes, and protect deployment secrets and transport. The local
prototype's shared operator key does not provide individual accountability,
fine-grained authorization, rate limits, or a complete audit trail. Those are
necessary before deployment; changing a key does not undo already accepted
changes.

**Replayed heartbeat.** A captured or duplicated heartbeat could otherwise
replace newer observations and falsely refresh last-seen time. Heartbeats carry a
per-robot monotonically increasing sequence; an accepted sequence cannot be
accepted again. The counter must survive agent restarts. A sequence is not a
signature or proof of real-world health: a token holder can send a higher value
with fabricated data, and an attacker who obtains a valid token can disrupt the
sequence. TLS, credential protection, revocation and later telemetry validation
remain important.

A desired-state ETag optimizes polling only. It does not authorize activation,
verify an artifact, renew a lease, or represent an acknowledgement that a robot
has converged. The future agent and controller must enforce the separate trust
and activation protocol documented in ADR-0003.
