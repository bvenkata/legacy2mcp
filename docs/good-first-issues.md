# Good first issues for `legacy2mcp analyze`

Small, self-contained tasks. Each touches one or two files, has an obvious
test, and needs no API key or network. To take one, open an issue with its
title (label `good first issue`), say you're on it, and send a PR with a test.
[CONTRIBUTING.md § Working on `legacy2mcp analyze`](../CONTRIBUTING.md#working-on-legacy2mcp-analyze)
shows where everything lives.

## Entry points

1. **Detect `argparse` sub-commands as CLI entry points.** `parser.add_subparsers()`
   followed by `.set_defaults(func=handler)`. *File:* `analyzers/python.py`,
   `_call_entry_points`. *Test:* a snippet in `test_analysis_python.py`.
   (legacy2mcp's own CLI is a good real-world check.)
2. **Flask `add_url_rule()` and `MethodView` classes.** `app.add_url_rule("/x", view_func=f)`
   and `X.as_view("name")`. *File:* `analyzers/python.py`.
3. **FastAPI `app.include_router(router, prefix="/v1")`** should prefix that
   router's routes, the way Django `include()` already does via `Mount`.
4. **Koa / koa-router routes** (`router.get("/x", handler)`). *File:* `analyzers/javascript.py`.
5. **NestJS controllers.** `@Controller("orders")` + `@Get(":id")` decorators
   in TypeScript.
6. **Next.js server actions.** Functions in files starting with `"use server"`
   as entry points of kind `http_route` with method `POST`.

## Side effects

7. **More payment SDKs**: Adyen, Mollie, Paddle, Lemon Squeezy (Python and JS).
   *Files:* `_PAYMENT_LIBS` / `_PAYMENT_PACKAGES`.
8. **More email SDKs**: Postmark (Python), Mailgun's REST client, AWS SES via boto3
   (`ses.send_email`).
9. **Redis writes** (`redis.set`, `hset`, `lpush`, `publish`) as a `db` or
   `queue` side effect, with a risk rule.
10. **Elasticsearch / OpenSearch** `index`, `delete`, `search` calls as a `db`
    resource (microblog in `docs/sample-report` uses it).
11. **Template-string SQL in Python** (`cursor.execute(f"DELETE FROM {t}")`): reuse
    `_str_template` so the verb and table are still read.

## Report

12. **Keyboard navigation for the flow list.** `j`/`k` to move, `Enter` to expand.
    *File:* `report/assets/report.js`.
13. **"Copy as Markdown"** button on a flow card (steps + risk reasons) for
    pasting into tickets.
14. **Table view for the codebase map** (an accessible alternative to the graph:
    module, lines, functions, side effects), toggled next to the graph.

## Docs and fixtures

15. **A Fastify fixture app** under `tests/fixtures/analyze/` with a golden file,
    plus Fastify route detection.
16. **A "how risk is decided" table** in `docs/analyze.md` generated from
    `risk/rules.py` (one row per side-effect kind and op).
