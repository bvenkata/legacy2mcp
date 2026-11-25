from __future__ import annotations

import textwrap
from pathlib import Path

from legacy2mcp.analysis.analyzers.python import PythonAnalyzer, module_name_for
from legacy2mcp.analysis.walk import SourceFile


def _analyze(rel: str, code: str):
    return PythonAnalyzer().analyze(SourceFile(path=Path(rel), rel=rel, language="python"), textwrap.dedent(code))


def _effects(result, qualname: str) -> list[tuple[str, str]]:
    func = next(f for f in result.module.functions if f.qualname == qualname)
    return [(e.kind, e.op) for e in func.side_effects]


def test_module_names():
    assert module_name_for("shop/orders.py") == "shop.orders"
    assert module_name_for("src/shop/__init__.py") == "shop"
    assert module_name_for("manage.py") == "manage"


def test_imports_resolve_aliases_and_relative_imports():
    result = _analyze("pkg/sub/mod.py", """
        import requests as rq
        import os.path
        from ..models import Order
        from . import helpers
    """)
    assert result.module.imports == {
        "Order": "pkg.models.Order", "helpers": "pkg.sub.helpers", "os": "os", "rq": "requests",
    }


def test_side_effect_classification():
    result = _analyze("svc.py", """
        import requests, stripe, smtplib
        from requests import get as fetch

        def f(cursor, session, path):
            fetch("https://a.example.com")
            requests.post("https://b.example.com")
            stripe.Charge.create(amount=1)
            stripe.Customer.retrieve("c")
            smtplib.SMTP("localhost")
            task.delay(1)
            cursor.execute("DELETE FROM t")
            cursor.execute("SELECT 1")
            session.add(obj)
            Order.objects.filter(x=1).update(y=2)
            Order.objects.get(pk=1)
            open(path, "w")
            open(path)
            user.set_password("x")
            len(path)
    """)
    assert _effects(result, "f") == [
        ("http_out", "read"), ("http_out", "write"), ("payment", "write"), ("payment", "read"),
        ("email", "write"), ("queue", "write"), ("db", "write"), ("db", "read"), ("db", "write"),
        ("db", "write"), ("db", "read"), ("file", "write"), ("auth", "write"),
    ]


def test_chained_call_counts_once():
    result = _analyze("q.py", """
        def f():
            return Order.query.filter_by(status="x").order_by("id").all()
    """)
    assert _effects(result, "f") == [("db", "read")]


def test_flask_blueprint_routes_and_request_params():
    result = _analyze("app.py", """
        from flask import Blueprint, request
        bp = Blueprint("o", __name__, url_prefix="/api")

        @bp.route("/items/<int:item_id>", methods=["GET", "PUT"])
        def item(item_id: int):
            q = request.args.get("q")
            name = request.form["name"]
    """)
    assert [(e.name, e.framework) for e in result.entry_points] == [
        ("GET /api/items/{item_id}", "flask"), ("PUT /api/items/{item_id}", "flask"),
    ]
    func = result.module.functions[0]
    assert [(p.name, p.location, p.required) for p in func.params] == [
        ("item_id", None, True), ("name", "form", True), ("q", "query", False),
    ]
    assert "flask" in result.frameworks


def test_fastapi_router_prefix_and_depends_skipped():
    result = _analyze("r.py", """
        from fastapi import APIRouter, Depends
        router = APIRouter(prefix="/v1")

        @router.post("/things")
        async def make(body: ThingIn, db=Depends(get_db)):
            ...
    """)
    (ep,) = result.entry_points
    assert (ep.name, ep.framework) == ("POST /v1/things", "fastapi")
    assert [p.name for p in result.module.functions[0].params] == ["body"]
    assert result.module.functions[0].is_async


def test_cli_task_and_job_entry_points():
    result = _analyze("jobs.py", """
        import click
        from celery import shared_task

        @click.command("sync-all")
        def sync():
            pass

        @shared_task
        def process(order_id):
            pass

        def nightly():
            pass

        scheduler.add_job(nightly, "cron", hour=1)
    """)
    assert sorted((e.kind, e.name) for e in result.entry_points) == [
        ("cli_command", "cli sync-all"),
        ("message_handler", "task process"),
        ("scheduled_job", "job nightly"),
    ]


def test_django_urls_emit_any_routes_and_mounts():
    result = _analyze("site/urls.py", """
        from django.urls import include, path, re_path
        from shop import views

        urlpatterns = [
            path("shop/", include("shop.urls")),
            path("about/", views.about),
            re_path(r"^items/(?P<pk>\\d+)/$", views.ItemView.as_view()),
        ]
    """)
    assert [(e.name, e.handler) for e in result.entry_points] == [
        ("ANY /about/", "shop.views.about"),
        ("ANY /items/{pk}/", "shop.views.ItemView"),
    ]
    assert [(m.prefix, m.target) for m in result.mounts] == [("shop/", "shop.urls")]


def test_django_view_methods_from_decorators_and_branches():
    result = _analyze("views.py", """
        from django.views.decorators.http import require_POST, require_http_methods

        @require_POST
        def a(request): ...

        @require_http_methods(["GET", "DELETE"])
        def b(request): ...

        def c(request):
            if request.method == "POST":
                pass
    """)
    methods = {f.qualname: f.http_methods for f in result.module.functions}
    assert methods == {"a": ["POST"], "b": ["DELETE", "GET"], "c": ["GET", "POST"]}


def test_syntax_error_is_recorded_not_raised():
    result = _analyze("broken.py", "def f(:\n")
    assert result.module.parse_error.startswith("SyntaxError")
    assert result.module.functions == []


def test_non_literal_detail_is_empty_string():
    result = _analyze("w.py", """
        def f(path, sql, cursor):
            open(path, "w")
            cursor.execute(sql)
    """)
    func = result.module.functions[0]
    assert [(e.kind, e.detail) for e in func.side_effects] == [("file", ""), ("db", "")]


def test_flask_wtf_validate_on_submit_is_a_post_branch():
    result = _analyze("views.py", """
        from flask import Flask
        app = Flask(__name__)

        @app.route("/", methods=["GET", "POST"])
        def index():
            form = PostForm()
            if form.validate_on_submit():
                db.session.add(Post())
                db.session.commit()
            return render_template("index.html")
    """)
    func = result.module.functions[0]
    assert [b.method for b in func.method_branches] == ["POST"]
    assert "POST" in func.http_methods


def test_rq_enqueue_and_fstring_urls():
    result = _analyze("svc.py", """
        import requests

        def f(queue, lang):
            queue.enqueue("app.tasks.export_posts", 1)
            requests.post(f"https://api.translator.example.com/translate?to={lang}")
            requests.get("https://api.example.org/v1?q={}".format(lang))
    """)
    assert [(e.kind, e.detail) for e in result.module.functions[0].side_effects] == [
        ("queue", ""), ("http_out", "https://api.translator.example.com/translate?to={}"),
        ("http_out", "https://api.example.org/v1?q={}"),
    ]
