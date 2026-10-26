# Operations metrics

The metrics `scripts/metrics_report.py` computes from the example APIs' operations logs
(`examples/demo_common/analytics.py`). Each definition fixes its unit, numerator, and
denominator; a change to any of them bumps the version here and `DEFINITIONS_VERSION` in
the script, so a trend never mixes two definitions.

Version 1.0.

## What is logged

One JSON line per event, per vertical and role, in `examples/<vertical>/data/.analytics/`:

| `kind` | Written when | Fields |
|---|---|---|
| `turn` | A chat turn ends | `turn_id`, session tag, locale, each tool called with its status (`ok`, `error`, `blocked`), the record ids the tool inputs named (`evidence_ids`), gate refusals with their reason, writes (`cart`, `change:<status>`), stop reason, error kind, usage, latency |
| `feedback` | The user votes on a reply | `turn_id`, `helpful` |
| `handoff` | The user asks for a person | `turn_id`, `reason`, `ticket` |
| `action` | The portal applies or dismisses a staged change | `name`, the change id |

No message text, tool result, or memory value is written; the session appears as the same
hashed tag the model-call log uses. `DEMO_ANALYTICS=0` turns the log off and
`DEMO_ANALYTICS_DIR` moves it.

## Units

- **Session**: one session token, from `POST /session` to reset. A session is
  **effective** when at least one of its turns ended without a system error.
- **Turn**: one user message and the reply to it.
- **Completion signal**: a 👍 on the turn; a write the turn made (a cart change, a staged
  change); a successful `checkout` call; or, on the portal, a staged change the operator
  applied (counted on the session's last turn).

## Metrics

| Metric | 中文 | Numerator | Denominator |
|---|---|---|---|
| Effective sessions | 有效接待会话数 | — | count of effective sessions |
| Resolution rate | 问题解决率 | effective sessions with a completion signal | effective sessions |
| First-turn resolution rate | 一次解决率 | effective sessions whose first completion signal is on their first turn | effective sessions |
| Effective self-service rate | 有效自助率 | resolved sessions with no handoff and no 👎 | effective sessions |
| Handoff rate | 转人工率 | effective sessions with a handoff | effective sessions |
| Negative feedback rate | 负反馈率 | 👎 votes | votes |
| Grounded turn rate | 有据回答率 | turns without a system error in which at least one tool call succeeded | turns without a system error |
| System error rate | 系统异常率 | turns that ended in an error event | turns |
| Latency p50 / p95 | 响应时延 | the turn's `elapsed_ms`, turns without a system error | — |
| Tokens per turn | 每轮 Token | mean over turns without a system error of the four usage counters summed | — |

Handoffs are split by the reason the UI sends: `user_requested` (the button with no prior
signal), `after_negative_feedback` (the button after a 👎 on that reply), and `after_error`
(the button under a reply that ended in an error). System errors are split by kind
(`no_key`, `auth_failed`, `provider_error`, `generic`) and gate refusals by tool and reason.

A severe-error rate needs a reviewer's judgment, which no log supplies: `--sample N` lists
the turns to review first (every 👎, then system errors, then gate refusals, then a seeded
random draw), and the reviewer's findings are counted outside this report.

## Before reading a change in a number

Check, in this order: whether this file's version changed; whether `DEMO_ANALYTICS` or
the log directory changed; whether a vertical, a tool, or a gate was added; and whether
the traffic is test traffic. A rate that moves with any of these is a definition change,
not a result.
