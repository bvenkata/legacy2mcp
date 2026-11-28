from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from legacy2mcp.analysis.analyzers import java
from legacy2mcp.analysis.walk import SourceFile

pytestmark = pytest.mark.skipif(not java.available(), reason="tree-sitter not installed")


def _analyze(rel: str, code: str):
    analyzer = java.JavaAnalyzer()
    return analyzer.analyze(SourceFile(path=Path(rel), rel=rel, language="java"), textwrap.dedent(code))


def _func(result, qualname):
    return next(f for f in result.module.functions if f.qualname == qualname)


def _effects(result, qualname):
    return [(e.kind, e.op, e.detail) for e in _func(result, qualname).side_effects]


def test_java_package_imports_and_classes():
    result = _analyze(
        "src/main/java/com/example/OrderService.java",
        """
        package com.example;

        import java.util.List;
        import java.util.Optional;
        import org.springframework.stereotype.Service;

        /**
         * Core order management service.
         */
        public class OrderService extends BaseService implements IOrderService {

            /**
             * Looks up an order by identifier.
             */
            public Optional<Order> findById(String id) {
                return Optional.empty();
            }
        }
        """,
    )
    assert result.module.path == "src/main/java/com/example/OrderService.java"
    assert result.module.name == "src/main/java/com/example/OrderService"
    assert result.module.imports == {
        "List": "java.util.List",
        "Optional": "java.util.Optional",
        "Service": "org.springframework.stereotype.Service",
    }
    assert len(result.module.classes) == 1
    cls = result.module.classes[0]
    assert cls.name == "OrderService"
    assert cls.bases == ["BaseService", "IOrderService"]
    assert cls.docstring == "Core order management service."
    assert cls.methods == ["OrderService.findById"]

    fn = _func(result, "OrderService.findById")
    assert fn.docstring == "Looks up an order by identifier."
    assert len(fn.params) == 1
    assert fn.params[0].name == "id"
    assert fn.params[0].type == "String"


def test_spring_controller_routes_and_params():
    result = _analyze(
        "src/main/java/com/example/OrderController.java",
        """
        package com.example;

        import org.springframework.web.bind.annotation.*;
        import org.springframework.security.access.prepost.PreAuthorize;

        @RestController
        @RequestMapping("/api/v1/orders")
        public class OrderController {

            @GetMapping("/{id}")
            @PreAuthorize("hasRole('USER')")
            public Order getOrder(@PathVariable("id") String orderId, @RequestParam(name = "detail") boolean detail) {
                return null;
            }

            @PostMapping
            public Order createOrder(@RequestBody Order order) {
                return order;
            }

            @DeleteMapping("/{id}")
            public void deleteOrder(@PathVariable String id) {
            }
        }
        """,
    )
    assert "spring" in result.frameworks
    routes = sorted((e.name, e.method, e.path, tuple(e.middleware)) for e in result.entry_points)
    assert routes == [
        ("DELETE /api/v1/orders/{id}", "DELETE", "/api/v1/orders/{id}", ()),
        ("GET /api/v1/orders/{id}", "GET", "/api/v1/orders/{id}", ("spring_security.PreAuthorize",)),
        ("POST /api/v1/orders", "POST", "/api/v1/orders", ()),
    ]

    get_fn = _func(result, "OrderController.getOrder")
    param_locs = [(p.name, p.location) for p in get_fn.params]
    assert ("id", "path") in param_locs
    assert ("detail", "query") in param_locs

    post_fn = _func(result, "OrderController.createOrder")
    assert any(p.location == "body" for p in post_fn.params)


def test_java_servlet_detection():
    result = _analyze(
        "src/main/java/com/example/UserServlet.java",
        """
        package com.example;

        import javax.servlet.http.HttpServlet;
        import javax.servlet.http.HttpServletRequest;
        import javax.servlet.http.HttpServletResponse;
        import javax.servlet.annotation.WebServlet;

        @WebServlet(urlPatterns = {"/users", "/customers"})
        public class UserServlet extends HttpServlet {

            @Override
            protected void doGet(HttpServletRequest req, HttpServletResponse resp) {
                String id = req.getParameter("id");
                String filter = req.getParameter("filter");
            }

            @Override
            protected void doPost(HttpServletRequest req, HttpServletResponse resp) {
            }
        }
        """,
    )
    assert "servlet" in result.frameworks
    names = sorted(e.name for e in result.entry_points)
    assert names == [
        "GET /customers",
        "GET /users",
        "POST /customers",
        "POST /users",
    ]
    do_get = _func(result, "UserServlet.doGet")
    param_names = [p.name for p in do_get.params]
    assert "id" in param_names
    assert "filter" in param_names


def test_jaxrs_resource_detection():
    result = _analyze(
        "src/main/java/com/example/ProductResource.java",
        """
        package com.example;

        import javax.ws.rs.*;
        import javax.ws.rs.core.Response;

        @Path("/products")
        public class ProductResource {

            @GET
            @Path("/{sku}")
            public Response getProduct(@PathParam("sku") String sku, @QueryParam("verbose") boolean verbose) {
                return Response.ok().build();
            }

            @POST
            public Response addProduct(@FormParam("name") String name) {
                return Response.ok().build();
            }
        }
        """,
    )
    assert "jax-rs" in result.frameworks
    names = sorted((e.name, e.method, e.path) for e in result.entry_points)
    assert names == [
        ("GET /products/{sku}", "GET", "/products/{sku}"),
        ("POST /products", "POST", "/products"),
    ]
    get_fn = _func(result, "ProductResource.getProduct")
    assert any(p.name == "sku" and p.location == "path" for p in get_fn.params)
    assert any(p.name == "verbose" and p.location == "query" for p in get_fn.params)


def test_side_effects_db_and_sql():
    result = _analyze(
        "src/main/java/com/example/OrderDao.java",
        """
        package com.example;

        public class OrderDao {
            public void executeWork(JdbcTemplate jdbcTemplate, OrderRepository repo, EntityManager em) {
                jdbcTemplate.query("SELECT id, amount FROM orders WHERE status = 'PAID'", rowMapper);
                jdbcTemplate.update("DELETE FROM audit_log WHERE created_at < NOW()");
                repo.findById(123L);
                repo.save(new Order());
                em.persist(new LogEntry());
            }
        }
        """,
    )
    effects = _effects(result, "OrderDao.executeWork")
    assert ("db", "read", "orders") in effects
    assert ("db", "write", "audit_log") in effects
    assert ("db", "read", "repo") in effects
    assert ("db", "write", "repo") in effects
    assert ("db", "write", "em") in effects


def test_side_effects_http_email_payment_file():
    result = _analyze(
        "src/main/java/com/example/PaymentProcessor.java",
        """
        package com.example;

        import com.stripe.model.Charge;
        import java.nio.file.Files;

        public class PaymentProcessor {
            public void process(RestTemplate restTemplate, JavaMailSender mailSender, KafkaTemplate kafka) {
                restTemplate.getForObject("https://api.external.com/verify", String.class);
                restTemplate.postForObject("https://api.external.com/charge", request, String.class);
                Charge.create(params);
                mailSender.send(message);
                kafka.send("order-topic", payload);
                Files.write(path, bytes);
            }
        }
        """,
    )
    effects = _effects(result, "PaymentProcessor.process")
    assert ("http_out", "read", "https://api.external.com/verify") in effects
    assert ("http_out", "write", "https://api.external.com/charge") in effects
    assert ("payment", "write", "") in effects or any(e[0] == "payment" and e[1] == "write" for e in effects)
    assert ("email", "write", "") in effects
    assert ("queue", "write", "order-topic") in effects
    assert ("file", "write", "") in effects


def test_async_annotation_flag():
    result = _analyze(
        "src/main/java/com/example/AsyncWorker.java",
        """
        package com.example;

        import org.springframework.scheduling.annotation.Async;

        public class AsyncWorker {
            @Async
            public void runInBackground() {
            }

            public void runSync() {
            }
        }
        """,
    )
    bg_fn = _func(result, "AsyncWorker.runInBackground")
    assert bg_fn.is_async is True

    sync_fn = _func(result, "AsyncWorker.runSync")
    assert sync_fn.is_async is False


def test_tree_sitter_resilient_to_syntax_errors():
    result = _analyze(
        "src/main/java/com/example/Broken.java",
        """
        package com.example;
        public class Broken {
            @GetMapping("/working")
            public void ok() {
                // incomplete syntax below
                if (
            }
        }
        """,
    )
    assert result.module.parse_error is not None
    assert any(e.name == "GET /working" for e in result.entry_points)


def test_analyze_spring_shop_fixture():
    from legacy2mcp.analysis.pipeline import run_analysis

    fixture_dir = Path(__file__).parent / "fixtures" / "analyze" / "spring_shop"
    analysis = run_analysis(fixture_dir)
    assert "spring" in analysis.summary.frameworks
    assert "java" in analysis.summary.languages
    assert analysis.summary.languages["java"].files == 4

    route_names = {ep.name for ep in analysis.entry_points}
    assert "GET /api/orders" in route_names
    assert "GET /api/orders/{id}" in route_names
    assert "POST /api/orders" in route_names

    flow_entry_names = {f.entry_name for f in analysis.flows}
    assert "GET /api/orders" in flow_entry_names
    assert "POST /api/orders" in flow_entry_names

