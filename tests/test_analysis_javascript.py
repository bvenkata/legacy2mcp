from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from legacy2mcp.analysis.analyzers import javascript
from legacy2mcp.analysis.analyzers.javascript import next_route_path, resolve_specifier
from legacy2mcp.analysis.walk import SourceFile

pytestmark = pytest.mark.skipif(not javascript.available(), reason="tree-sitter not installed")


def _analyze(rel: str, code: str):
    lang = "typescript" if rel.endswith((".ts", ".tsx")) else "javascript"
    analyzer = javascript.JavaScriptAnalyzer()
    return analyzer.analyze(SourceFile(path=Path(rel), rel=rel, language=lang), textwrap.dedent(code))


def _func(result, qualname):
    return next(f for f in result.module.functions if f.qualname == qualname)


def _effects(result, qualname):
    return [(e.kind, e.op, e.detail) for e in _func(result, qualname).side_effects]


def test_specifier_and_next_paths():
    assert resolve_specifier("./pay.js", "src/routes/x.js") == "src/routes/pay"
    assert resolve_specifier("../db", "src/routes/x.js") == "src/db"
    assert resolve_specifier("stripe", "src/a.js") == "stripe"
    assert next_route_path("pages/api/orders/[id].ts") == ("pages", "/api/orders/{id}")
    assert next_route_path("src/pages/api/index.ts") == ("pages", "/api")
    assert next_route_path("app/(shop)/api/cart/[...slug]/route.ts") == ("app", "/api/cart/{slug}")
    assert next_route_path("app/about/page.tsx") is None


def test_es_and_commonjs_imports_and_exports():
    result = _analyze("src/a.js", """
        import axios from "axios";
        import def, { b as c } from "./b";
        import * as ns from "../lib/ns";
        const { Pool } = require("pg");
        const d = require("./d");
        export function one() {}
        export const two = () => {};
        function three() {}
        export default three;
    """)
    assert result.module.imports == {
        "Pool": "pg.Pool", "axios": "axios", "c": "src/b.b", "d": "src/d", "def": "src/b.default", "ns": "lib/ns",
    }
    assert result.module.exports == {"default": "three", "one": "one", "two": "two"}
    assert [f.qualname for f in result.module.functions] == ["one", "two", "three"]


def test_module_exports_object_and_assignments():
    result = _analyze("svc.js", """
        function a() {}
        const b = async () => {};
        module.exports = { a, renamed: b, inline() {} };
        exports.extra = function () {};
    """)
    assert result.module.exports == {"a": "a", "extra": "extra", "inline": "inline", "renamed": "b"}


def test_side_effects():
    result = _analyze("svc.js", """
        const Stripe = require("stripe");
        const axios = require("axios");
        const fs = require("fs");
        const jwt = require("jsonwebtoken");
        const stripe = new Stripe("k");
        async function f(db, prisma) {
          await fetch("https://api.example.com/x");
          await fetch("https://api.example.com/y", { method: "POST" });
          await axios.get("https://a.example.com");
          await stripe.refunds.create({});
          await stripe.customers.retrieve("c");
          await db.query("UPDATE accounts SET x = 1");
          await prisma.user.findMany();
          await prisma.user.delete({});
          await Order.findById(1);
          await Order.create({});
          Object.create(null);
          fs.writeFileSync("out.txt", "x");
          jwt.sign({}, "s");
          await queue.add("job", {});
        }
    """)
    assert _effects(result, "f") == [
        ("http_out", "read", "https://api.example.com/x"),
        ("http_out", "write", "https://api.example.com/y"),
        ("http_out", "read", "https://a.example.com"),
        ("payment", "write", "stripe"),
        ("payment", "read", "stripe"),
        ("db", "write", "accounts"),
        ("db", "read", "user"),
        ("db", "write", "user"),
        ("db", "read", "Order"),
        ("db", "write", "Order"),
        ("file", "write", "out.txt"),
        ("auth", "write", ""),
        ("queue", "write", "queue"),
    ]


def test_chained_call_falls_back_to_inner_match():
    result = _analyze("q.js", """
        async function f(pool) {
          return (await pool.query("SELECT * FROM orders")).rows.map(r => r.id);
        }
    """)
    assert _effects(result, "f") == [("db", "read", "orders")]


def test_express_routes_mounts_and_middleware():
    result = _analyze("src/app.js", """
        const express = require("express");
        const users = require("./routes/users");
        const { auth } = require("./mw");
        const app = express();
        app.use("/api/users", users);
        app.get("/health", (req, res) => res.send("ok"));
        app.post("/items/:id", auth, async (req, res) => {
          const { qty } = req.body;
          res.json({ id: req.params.id, q: req.query.q });
        });
        app.route("/things/:id").put(auth, users.update).delete(users.remove);
    """)
    names = sorted((e.name, e.handler, tuple(e.middleware)) for e in result.entry_points)
    assert names == [
        ("DELETE /things/{id}", "src/routes/users.remove", ()),
        ("GET /health", "src/app.js::<GET /health>", ()),
        ("POST /items/{id}", "src/app.js::<POST /items/{id}>", ("src/mw.auth",)),
        ("PUT /things/{id}", "src/routes/users.update", ("src/mw.auth",)),
    ]
    assert [(m.prefix, m.target) for m in result.mounts] == [("/api/users", "src/routes/users")]
    handler = _func(result, "<POST /items/{id}>")
    assert sorted((p.name, p.location) for p in handler.params if p.location) == [
        ("id", "path"), ("q", "query"), ("qty", "body"),
    ]
    assert "express" in result.frameworks


def test_axios_instance_named_api_is_not_a_route():
    result = _analyze("client.js", """
        import axios from "axios";
        const api = axios.create({ baseURL: "/x" });
        export async function save(data) { return api.post("/orders", data); }
    """)
    assert result.entry_points == []


def test_next_pages_router_methods_from_switch_and_checks():
    result = _analyze("pages/api/items/[id].ts", """
        import type { NextApiRequest, NextApiResponse } from "next";
        export default async function handler(req: NextApiRequest, res: NextApiResponse) {
          if (req.method !== "POST") return res.status(405).end();
          res.json({});
        }
    """)
    assert [(e.name, e.handler) for e in result.entry_points] == [
        ("POST /api/items/{id}", "pages/api/items/[id].ts::handler"),
    ]
    assert _func(result, "handler").params[0].type == "NextApiRequest"


def test_next_app_router_exports_and_body_params():
    result = _analyze("app/api/cart/route.ts", """
        import { NextResponse } from "next/server";
        export async function GET(request: Request) { return NextResponse.json({}); }
        export const POST = async (request: Request) => {
          const { sku, qty } = await request.json();
          return NextResponse.json({ sku, qty });
        };
    """)
    assert sorted(e.name for e in result.entry_points) == ["GET /api/cart", "POST /api/cart"]
    assert sorted((p.name, p.location) for p in _func(result, "POST").params if p.location) == [
        ("qty", "body"), ("sku", "body"),
    ]


def test_jobs_workers_consumers_cli():
    result = _analyze("jobs.js", """
        const cron = require("node-cron");
        const { Worker } = require("bullmq");
        const { program } = require("commander");
        function nightly() {}
        cron.schedule("0 1 * * *", nightly);
        new Worker("mail", async (job) => {});
        channel.consume("events", (msg) => {});
        program.command("sync <id>").description("x").action(async (id) => {});
    """)
    assert sorted((e.kind, e.name, e.handler) for e in result.entry_points) == [
        ("cli_command", "cli sync", "jobs.js::<cli sync>"),
        ("message_handler", "consumer events", "jobs.js::<consumer events>"),
        ("message_handler", "worker mail", "jobs.js::<worker mail>"),
        ("scheduled_job", "job 0 1 * * *", "jobs.js::nightly"),
    ]


def test_inline_handler_calls_not_double_counted():
    result = _analyze("r.js", """
        const express = require("express");
        const router = express.Router();
        function register() {
          router.post("/pay", async (req, res) => { await stripe.charges.create({}); });
        }
    """)
    assert _effects(result, "register") == []
    assert [e[0] for e in _effects(result, "<POST /pay>")] == ["payment"]


def test_tsx_and_syntax_errors():
    ok = _analyze("Button.tsx", "export const B = ({ x }: { x: string }) => <b>{x}</b>;\n")
    assert ok.module.parse_error is None
    bad = _analyze("bad.js", "function (\n")
    assert bad.module.parse_error == "syntax errors (partial parse)"
