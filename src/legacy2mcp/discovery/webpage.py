"""
Import a plain website into a Legacy Interface Contract -- for a legacy
web app (or a plain content site) that has no API docs, no Postman
collection, and no HAR capture at hand, only a URL in a browser.

Starting from the given URL, this crawls same-site pages reached by
following `<a href>` links (bounded by `max_pages`/`max_depth` -- this is
a small, polite, same-origin crawl, not a general-purpose scraper: it
never follows a link to a different host, and the defaults are
deliberately modest). Every page visited contributes:

- One REST operation per `<form>` found on it (method + the form's
  resolved action path, one field per `<input>`/`<select>`/`<textarea>`)
  -- unchanged from the single-page behaviour this module started with.
- One read-only "get this page's content" operation per page with enough
  text to be worth exposing, for a page that's just content with no form
  at all (a marketing site, a docs site, ...). Calling the generated tool
  returns the page's extracted text (see legacy2mcp.htmltext), not a
  structured API response -- that's flagged in a note on every such
  operation, not silently implied.

This never executes JavaScript and never submits a form -- every page is
a single GET request, and link discovery is static-HTML parsing of
`<a href>`, same as form discovery. A page whose forms or navigation are
built or altered by client-side JS after load won't be found this way.

This is the least certain of any importer here -- a form's actual
server-side semantics (what an action really does, whether a GET-method
form is really read-only) can't be inferred from markup alone, so every
operation lands at confidence="low" / needs_review=True, the same posture
discovery/har.py uses for sampled traffic. One caveat is specific to form
operations and called out per-operation: HTML forms normally submit
non-GET requests as `application/x-www-form-urlencoded`, but
adapters/rest_adapter.py always sends a JSON body -- that mismatch is
flagged as a note for a human to check, not silently papered over.

contract_from_html_file() runs the same extraction over one saved page (a
page behind a login, saved from the browser) instead of fetching a URL.
There is nothing to crawl and no URL the page was served from, so relative
form actions resolve against "/" with a note to confirm the real path, and
the contract's source_label is the file path -- never an http(s) URL, so
classify.py leaves base_url as REPLACE_ME_BASE_URL rather than guessing.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from legacy2mcp.discovery.contract import ContractError, ContractField, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write, template_path
from legacy2mcp.discovery.redact import enum_eligible
from legacy2mcp.htmltext import extract_text_and_title

# Control widgets, not data fields -- a submit/reset button's name=value
# pair is sometimes meaningful to a backend (distinguishing which button
# was clicked), but that's rare enough, and ambiguous enough from markup
# alone, that treating it as a real input would create more noise than
# signal. File uploads can't be represented as a simple scalar value at
# all -- there's no adapter here that can attach a file to a request.
_SKIPPED_INPUT_TYPES = {"submit", "button", "reset", "image", "file"}
_CHOICE_INPUT_TYPES = {"radio", "checkbox"}
_NUMBER_INPUT_TYPES = {"number", "range"}
_SKIPPED_LINK_PREFIXES = ("javascript:", "mailto:", "tel:", "#")

# A page needs at least this much extracted text before a "read this
# page" tool is worth generating for it -- below this it's almost always
# a redirect shell, a login wall, or a JS-only app shell with nothing
# static to read.
_MIN_CONTENT_CHARS = 20

_FORM_URLENCODED_NOTE = (
    "HTML forms normally submit non-GET requests as "
    "'application/x-www-form-urlencoded', but legacy2mcp's REST adapter always sends a JSON "
    "request body -- confirm the backend accepts a JSON body for this endpoint before using "
    "the generated tool."
)
_CONTENT_PAGE_NOTE = (
    "This operation returns the page's extracted text content, not a structured API response "
    "-- there's nothing to validate it against beyond that."
)
_SAVED_PAGE_PATH_NOTE = (
    "This page was read from a saved HTML file, so the URL it was served from isn't known -- "
    "its path is a placeholder ('/'). Set the real path before using this tool."
)

# A saved file has no URL: its forms and content resolve against the bare
# root path, never against a file:// URL or the local file's own path.
_SAVED_PAGE_URL = "/"


class _PageParser(HTMLParser):
    """One pass over a page's HTML collects everything discovery needs
    from it: every `<form>`'s method/action and its `<input>`/`<select>`/
    `<textarea>` descendants, and every `<a href>` link (for the crawl to
    follow). Defensive about malformed/nested markup (a stack, not a
    single "current form") since this runs against arbitrary real-world
    HTML, not a spec `legacy2mcp` controls."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.links: list[str] = []
        self._stack: list[dict] = []
        self._current_select: dict | None = None
        self._in_option = False
        self._option_value: str | None = None
        self._option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v if v is not None else "") for k, v in attrs}

        if tag == "a":
            href = attr.get("href")
            if href and not href.lower().startswith(_SKIPPED_LINK_PREFIXES):
                self.links.append(href)
            return

        if tag == "form":
            self._stack.append(
                {
                    "method": (attr.get("method") or "get").upper(),
                    "action": attr.get("action"),
                    "id": attr.get("id") or "",
                    "name": attr.get("name") or "",
                    "fields": [],
                }
            )
            return
        if not self._stack:
            return  # input/select/textarea/option outside any <form> -- not a submittable field

        if tag == "input":
            self._stack[-1]["fields"].append(
                {
                    "tag": "input",
                    "type": (attr.get("type") or "text").lower(),
                    "name": attr.get("name") or "",
                    "value": attr.get("value"),
                    "required": "required" in attr,
                }
            )
        elif tag == "select":
            field = {
                "tag": "select",
                "name": attr.get("name") or "",
                "required": "required" in attr,
                "multiple": "multiple" in attr,
                "options": [],
            }
            self._stack[-1]["fields"].append(field)
            self._current_select = field
        elif tag == "option" and self._current_select is not None:
            self._in_option = True
            self._option_value = attr.get("value")
            self._option_text = []
        elif tag == "textarea":
            self._stack[-1]["fields"].append(
                {"tag": "textarea", "name": attr.get("name") or "", "required": "required" in attr}
            )

    def handle_data(self, data: str) -> None:
        if self._in_option:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._stack:
            self.forms.append(self._stack.pop())
        elif tag == "select":
            self._current_select = None
        elif tag == "option" and self._in_option:
            value = self._option_value if self._option_value is not None else "".join(self._option_text).strip()
            if value and self._current_select is not None:
                self._current_select["options"].append(value)
            self._in_option = False
            self._option_value = None
            self._option_text = []


def _choice_field(name: str, raw_fields: list[dict]) -> ContractField:
    """A radio/checkbox group sharing one `name` -- a single checkbox is
    naturally a boolean; more than one distinct value under the same name
    is a real closed set (a group of radio buttons, or a multi-checkbox
    picklist), so it becomes a string field with an enum, same policy
    enum_eligible() already applies to every other importer's sampled
    data."""
    required = any(f["required"] for f in raw_fields)
    if len(raw_fields) == 1 and raw_fields[0]["type"] == "checkbox":
        return ContractField(name=name, location="query", type="boolean", required=required, confidence="low")

    values = {f["value"] for f in raw_fields if f.get("value")}
    enum = enum_eligible(name, values)
    return ContractField(
        name=name,
        location="query",
        type="string",
        required=required,
        enum=enum,
        confidence="low",
        description="Inferred from a group of radio/checkbox inputs sharing this name.",
    )


def _select_field(field: dict) -> ContractField:
    if field["multiple"]:
        return ContractField(
            name=field["name"],
            location="query",
            type="array",
            required=field["required"],
            confidence="low",
            needs_review=True,
            description="A multi-select -- the rest adapter can't represent a list value yet; review manually.",
        )
    enum = enum_eligible(field["name"], set(field["options"])) if field["options"] else None
    return ContractField(name=field["name"], location="query", type="string", required=field["required"], enum=enum, confidence="low")


def _input_field(raw: dict) -> ContractField | None:
    input_type = raw["type"]
    if input_type in _SKIPPED_INPUT_TYPES:
        return None
    if not raw["name"]:
        return None  # unnamed inputs are never submitted as a field

    if input_type == "hidden":
        return ContractField(
            name=raw["name"],
            location="query",
            type="string",
            required=False,
            confidence="low",
            needs_review=True,
            description=(
                "Hidden field -- its value wasn't captured (it may be a per-request token "
                "such as CSRF, which this static-HTML scan can't resolve); confirm how to "
                "supply it before using this tool."
            ),
        )
    if input_type == "checkbox":
        return ContractField(name=raw["name"], location="query", type="boolean", required=raw["required"], confidence="low")
    if input_type in _NUMBER_INPUT_TYPES:
        return ContractField(name=raw["name"], location="query", type="number", required=raw["required"], confidence="low")
    return ContractField(name=raw["name"], location="query", type="string", required=raw["required"], confidence="low")


def _fields_from_form(raw_fields: list[dict]) -> list[ContractField]:
    fields: list[ContractField] = []
    choice_groups: dict[str, list[dict]] = {}

    for raw in raw_fields:
        if raw["tag"] == "select":
            fields.append(_select_field(raw))
        elif raw["tag"] == "textarea":
            if raw["name"]:
                fields.append(ContractField(name=raw["name"], location="query", type="string", required=raw["required"], confidence="low"))
        elif raw["tag"] == "input":
            if raw["type"] in _CHOICE_INPUT_TYPES and raw["name"]:
                choice_groups.setdefault(raw["name"], []).append(raw)
                continue
            field = _input_field(raw)
            if field is not None:
                fields.append(field)

    for name, group in choice_groups.items():
        fields.append(_choice_field(name, group))

    return fields


def _unique_name(base: str, used_names: set[str]) -> str:
    base = base.strip("/_").replace("/", "_").replace("{", "").replace("}", "") or "page"
    name = base
    suffix = 2
    while name in used_names:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def _operation_from_form(
    form: dict, page_url: str, page_parts, used_names: set[str], saved_file: str | None = None
) -> ContractOperation:
    """`saved_file` is set when the page was read from a local file
    (contract_from_html_file), whose own URL is unknown: `page_url` is then
    the bare root path, and a relative or missing action gets a note
    rather than a path that only looks real."""
    method = form["method"] if form["method"] in ("GET", "POST") else "GET"
    action = form.get("action")
    resolved = urljoin(page_url, action) if action else page_url
    action_parts = urlsplit(resolved)

    notes: list[str] = []
    if saved_file is not None and not action:
        notes.append(
            "The form has no action, so it submits to the URL the page was served from, which a "
            "saved HTML file doesn't record -- the path '/' is a placeholder; set the real one."
        )
    elif saved_file is not None and not urlsplit(action).netloc and not action.startswith("/"):
        notes.append(
            f"The form action '{action}' is relative to the URL the page was served from, which a "
            f"saved HTML file doesn't record -- it was resolved against '/'; confirm the real path."
        )
    if saved_file is not None and action_parts.netloc:
        notes.append(
            f"Form action points at '{action_parts.netloc}' -- set RestAdapterSettings.base_url to "
            f"that host."
        )
    elif action_parts.netloc and action_parts.netloc != page_parts.netloc:
        notes.append(
            f"Form action resolves to a different host ('{action_parts.netloc}') than the page "
            f"itself ('{page_parts.netloc}') -- set RestAdapterSettings.base_url to the form's "
            f"host, not the page's."
        )
    if action_parts.query:
        notes.append(
            f"Form action's URL already had a query string ('{action_parts.query}') baked in -- "
            f"not modeled as a field here; confirm whether it's still needed."
        )
    if method != "GET":
        notes.append(_FORM_URLENCODED_NOTE)

    templated_path, path_var_names = template_path(action_parts.path or "/")
    path_fields = [
        ContractField(
            name=n,
            location="path",
            type="string",
            required=True,
            confidence="low",
            needs_review=True,
            description="Inferred from the form action's URL -- confirm the real type.",
        )
        for n in path_var_names
    ]

    fields = path_fields + _fields_from_form(form["fields"])
    if method != "GET":
        for f in fields:
            if f.location == "query":
                f.location = "body"

    raw_name = form["id"] or form["name"]
    op_name = _unique_name(raw_name or f"{method.lower()}_{templated_path}", used_names)
    write_name_hint = raw_name or templated_path

    return ContractOperation(
        name=op_name,
        source_kind="rest",
        method=method,
        path=templated_path,
        description=f"Discovered from a <form> in {saved_file}." if saved_file else f"Discovered from a <form> on {page_url}.",
        side_effect="write" if (method != "GET" or looks_like_write(write_name_hint)) else "read",
        fields=fields,
        confidence="low",
        needs_review=True,
        notes=notes,
    )


def _page_operation_name(path: str) -> str:
    stripped = path.strip("/")
    return f"get_{stripped.replace('/', '_')}" if stripped else "get_home"


def _operation_from_page_content(
    page_url: str, page_parts, html: str, used_names: set[str], saved_file: str | None = None
) -> ContractOperation | None:
    text, title = extract_text_and_title(html)
    if len(text) < _MIN_CONTENT_CHARS:
        return None

    op_name = _unique_name(_page_operation_name(page_parts.path), used_names)
    preview = text if len(text) <= 160 else text[:160].rstrip() + "..."
    notes = [_CONTENT_PAGE_NOTE, f"Content preview: {preview}"]
    if saved_file is not None:
        notes.insert(0, _SAVED_PAGE_PATH_NOTE)
    return ContractOperation(
        name=op_name,
        source_kind="rest",
        method="GET",
        path=page_parts.path or "/",
        description=title
        or (f"Page content discovered in {saved_file}." if saved_file else f"Page content discovered at {page_url}."),
        side_effect="read",
        fields=[],
        confidence="low",
        needs_review=True,
        notes=notes,
    )


def _operations_from_page(
    page_url: str, html: str, parser: _PageParser, used_names: set[str], saved_file: str | None = None
) -> list[ContractOperation]:
    """One operation per `<form>` on the page, or one read-only content
    operation if it has no form but enough text -- shared by the crawl and
    the saved-file path, so both extract exactly the same way."""
    page_parts = urlsplit(page_url)
    operations: list[ContractOperation] = []

    for form in parser.forms:
        op = _operation_from_form(form, page_url, page_parts, used_names, saved_file=saved_file)
        used_names.add(op.name)
        operations.append(op)

    if not parser.forms:
        content_op = _operation_from_page_content(page_url, page_parts, html, used_names, saved_file=saved_file)
        if content_op is not None:
            used_names.add(content_op.name)
            operations.append(content_op)

    return operations


def _normalize_host(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, parts.query, ""))


def _parse_page(html: str) -> _PageParser | None:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None
    return parser


def _crawl_site(
    start_url: str, timeout: float, max_pages: int, max_depth: int
) -> tuple[dict[str, tuple[str, _PageParser]], list[str]]:
    """Breadth-first, same-site-only crawl starting at `start_url`. Returns
    (url -> (raw HTML, already-parsed page) for every page successfully
    fetched and parsed, list of fetch/parse errors for pages that were
    linked to but couldn't be read) -- errors are returned rather than
    raised, since one broken link shouldn't stop the whole crawl. Each
    page is parsed exactly once here, for both link discovery and the
    forms/content extraction the caller does afterwards."""
    import requests

    start_host = _normalize_host(urlsplit(start_url).netloc)
    pages: dict[str, tuple[str, _PageParser]] = {}
    errors: list[str] = []
    seen = {_normalize_url(start_url)}
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])

    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        try:
            response = requests.get(url, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            errors.append(f"{url}: {exc}")
            continue
        if response.status_code != 200:
            errors.append(f"{url}: HTTP {response.status_code}")
            continue
        content_type = response.headers.get("Content-Type", "")
        if content_type and "html" not in content_type.lower():
            continue  # a PDF, an image, ... -- nothing this importer can read

        parser = _parse_page(response.text)
        if parser is None:
            errors.append(f"{url}: could not parse HTML")
            continue
        pages[url] = (response.text, parser)
        if depth >= max_depth:
            continue

        for href in parser.links:
            link = urljoin(url, href)
            link_parts = urlsplit(link)
            if link_parts.scheme not in ("http", "https"):
                continue
            if _normalize_host(link_parts.netloc) != start_host:
                continue  # same-site only -- never crawl off to a different host
            normalized = _normalize_url(link)
            if normalized in seen:
                continue
            seen.add(normalized)
            queue.append((link, depth + 1))

    return pages, errors


def contract_from_webpage(
    url: str, timeout: float = 10.0, max_pages: int = 25, max_depth: int = 2
) -> LegacyInterfaceContract:
    """Crawl same-site pages starting at `url` (bounded by `max_pages`/
    `max_depth`) and turn every `<form>` found into a REST operation, plus
    one read-only "get this page's content" operation per page that has
    no form but does have real text content. Raises ContractError if the
    start URL itself can't be fetched, or if the whole crawl turns up
    nothing usable (no forms, no pages with enough text -- most often a
    JS-rendered app with nothing static to read)."""
    pages, errors = _crawl_site(url, timeout=timeout, max_pages=max_pages, max_depth=max_depth)
    if not pages:
        detail = f" ({errors[0]})" if errors else ""
        raise ContractError(f"Could not fetch anything starting from '{url}'{detail}.")

    used_names: set[str] = set()
    operations: list[ContractOperation] = []

    for page_url, (html, parser) in pages.items():
        operations.extend(_operations_from_page(page_url, html, parser, used_names))

    if not operations:
        raise ContractError(
            f"Crawled {len(pages)} page(s) starting from '{url}' but found no <form> elements and "
            f"no page with enough static text content to be worth a tool. legacy2mcp only reads "
            f"static HTML -- a site built or rendered by client-side JavaScript won't be found "
            f"this way."
        )

    return LegacyInterfaceContract(
        source_label=url,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=operations,
    )


def contract_from_html_file(path: str | Path) -> LegacyInterfaceContract:
    """Turn one saved HTML page (a page behind a login, "Save As" from the
    browser, or captured by another tool) into a contract, with the same
    form and content extraction contract_from_webpage() uses. Nothing is
    fetched and no link is followed: a local file has no site to crawl.
    The page's own URL isn't known, so paths resolve against "/" (with a
    note on each operation where that matters), and source_label is the
    file path, so `init` leaves base_url as REPLACE_ME_BASE_URL. Raises
    ContractError if the file can't be read or parsed, or has no form and
    not enough text for a content tool."""
    file_label = str(path)
    try:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ContractError(f"Could not read HTML file '{file_label}': {exc}.") from exc

    parser = _parse_page(html)
    if parser is None:
        raise ContractError(f"Could not parse '{file_label}' as HTML.")

    operations = _operations_from_page(_SAVED_PAGE_URL, html, parser, set(), saved_file=file_label)
    if not operations:
        raise ContractError(
            f"'{file_label}' has no <form> elements and not enough static text content to be worth "
            f"a tool. legacy2mcp only reads static HTML -- a page saved before client-side "
            f"JavaScript built its forms won't have them."
        )

    return LegacyInterfaceContract(
        source_label=file_label,
        generated_at=datetime.now(timezone.utc).isoformat(),
        operations=operations,
    )
