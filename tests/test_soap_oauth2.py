from __future__ import annotations

import argparse
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, Response, jsonify, request
from pydantic import ValidationError

from legacy2mcp.adapters.base import AdapterError
from legacy2mcp.adapters.soap_adapter import OAuth2BearerAuth, SoapAdapter
from legacy2mcp.cli import _cmd_init
from legacy2mcp.config import ConfigError, OAuth2AuthConfig, RestAdapterSettings, SoapAdapterSettings, load_config


def _args(**kwargs):
    defaults = {
        "wsdl_url": None,
        "rest_url": None,
        "webpage_url": None,
        "github_url": None,
        "local_path": None,
        "from_contract": None,
        "auth": None,
        "out": "./config.yaml",
        "html_out": None,
        "no_html_report": True,
        "no_chat": True,
        "scan_only": True,
        "max_pages": 25,
        "max_depth": 2,
        "github_token_env": None,
        "llm_api_key_env": None,
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-4o",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_oauth2_config_from_file_with_url_and_password(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "url": "https://auth.example.com/token",
                "client_id": "test-client",
                "password": "test-password",
                "scope": "soap.read",
            }
        ),
        encoding="utf-8",
    )

    config = OAuth2AuthConfig(file=str(auth_file))
    token_url, client_id, client_secret, scope = config.resolve_credentials()

    assert token_url == "https://auth.example.com/token"
    assert client_id == "test-client"
    assert client_secret == "test-password"
    assert scope == "soap.read"


def test_oauth2_config_from_file_with_token_url_and_client_secret(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "token_url": "https://auth.example.com/oauth/token",
                "client_id": "client-abc",
                "client_secret": "secret-xyz",
            }
        ),
        encoding="utf-8",
    )

    config = OAuth2AuthConfig(file=str(auth_file))
    token_url, client_id, client_secret, scope = config.resolve_credentials()

    assert token_url == "https://auth.example.com/oauth/token"
    assert client_id == "client-abc"
    assert client_secret == "secret-xyz"
    assert scope is None


def test_oauth2_config_file_not_found():
    config = OAuth2AuthConfig(file="non_existent_auth.json")
    with pytest.raises(ValueError, match="Auth file not found"):
        config.resolve_credentials()


def test_oauth2_config_file_missing_fields(tmp_path):
    auth_file = tmp_path / "incomplete_auth.json"
    auth_file.write_text(json.dumps({"client_id": "test-client"}), encoding="utf-8")

    config = OAuth2AuthConfig(file=str(auth_file))
    with pytest.raises(ValueError, match="missing 'url' or 'token_url'"):
        config.resolve_credentials()


def test_oauth2_config_env_vars(monkeypatch):
    monkeypatch.setenv("TEST_SOAP_SECRET", "super-secret")
    config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="my-client",
        client_secret_env="TEST_SOAP_SECRET",
        scope="api",
    )
    token_url, client_id, client_secret, scope = config.resolve_credentials()

    assert token_url == "https://auth.example.com/token"
    assert client_id == "my-client"
    assert client_secret == "super-secret"
    assert scope == "api"


def test_oauth2_config_env_var_missing(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET_ENV", raising=False)
    config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="my-client",
        client_secret_env="MISSING_SECRET_ENV",
    )
    with pytest.raises(ValueError, match="is not set"):
        config.resolve_credentials()


def test_oauth2_bearer_auth_token_caching_and_refresh(monkeypatch):
    monkeypatch.setenv("SOAP_SECRET", "secret-val")
    auth_config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="client-1",
        client_secret_env="SOAP_SECRET",
    )

    call_count = 0

    def mock_post(url, data, timeout):
        nonlocal call_count
        call_count += 1
        assert data["grant_type"] == "client_credentials"
        assert data["client_id"] == "client-1"
        assert data["client_secret"] == "secret-val"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": f"token-{call_count}",
            "expires_in": 120,
        }
        return resp

    with patch("requests.post", side_effect=mock_post):
        auth = OAuth2BearerAuth(auth_config)

        # First fetch
        token1 = auth.get_token()
        assert token1 == "token-1"
        assert call_count == 1

        # Second call should use cache
        token2 = auth.get_token()
        assert token2 == "token-1"
        assert call_count == 1

        # Simulate expiration
        auth._expires_at = time.time() - 10
        token3 = auth.get_token()
        assert token3 == "token-2"
        assert call_count == 2

        # Test __call__ header injection
        prepared_req = MagicMock()
        prepared_req.headers = {}
        auth(prepared_req)
        assert prepared_req.headers["Authorization"] == "Bearer token-2"


def test_oauth2_bearer_auth_error_handling(monkeypatch):
    monkeypatch.setenv("SOAP_SECRET", "secret-val")
    auth_config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="client-1",
        client_secret_env="SOAP_SECRET",
    )

    # HTTP Error
    err_resp = MagicMock()
    err_resp.status_code = 401
    err_resp.text = "Unauthorized"
    with patch("requests.post", return_value=err_resp):
        auth = OAuth2BearerAuth(auth_config)
        with pytest.raises(AdapterError, match="status 401: Unauthorized"):
            auth.get_token()

    # Missing access_token
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"error": "no token"}
    with patch("requests.post", return_value=ok_resp):
        auth = OAuth2BearerAuth(auth_config)
        with pytest.raises(AdapterError, match="missing 'access_token'"):
            auth.get_token()


def test_oauth2_bearer_auth_expires_in_resilience(monkeypatch):
    monkeypatch.setenv("SOAP_SECRET", "secret-val")
    auth_config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="client-1",
        client_secret_env="SOAP_SECRET",
    )

    # String expires_in
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"access_token": "tok1", "expires_in": "1800"}
    with patch("requests.post", return_value=resp):
        auth = OAuth2BearerAuth(auth_config)
        token = auth.get_token()
        assert token == "tok1"
        assert auth._expires_at > time.time() + 1700

    # Null expires_in
    auth._access_token = None
    resp.json.return_value = {"access_token": "tok2", "expires_in": None}
    with patch("requests.post", return_value=resp):
        token = auth.get_token()
        assert token == "tok2"
        assert auth._expires_at > time.time() + 3500  # Default 3600 - 60

    # Non-numeric string expires_in
    auth._access_token = None
    resp.json.return_value = {"access_token": "tok3", "expires_in": "not-a-number"}
    with patch("requests.post", return_value=resp):
        token = auth.get_token()
        assert token == "tok3"
        assert auth._expires_at > time.time() + 3500


def test_oauth2_bearer_auth_error_message_truncation(monkeypatch):
    monkeypatch.setenv("SOAP_SECRET", "secret-val")
    auth_config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="client-1",
        client_secret_env="SOAP_SECRET",
    )

    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.text = "X" * 1000
    with patch("requests.post", return_value=err_resp):
        auth = OAuth2BearerAuth(auth_config)
        with pytest.raises(AdapterError) as exc_info:
            auth.get_token()
        err_msg = str(exc_info.value)
        assert len(err_msg) < 300
        assert "X" * 200 in err_msg
        assert "X" * 201 not in err_msg


def test_rest_adapter_rejects_oauth2_config():
    # Item 1: REST config should reject oauth2
    with pytest.raises(ValidationError):
        RestAdapterSettings.model_validate(
            {
                "base_url": "https://api.example.com",
                "auth": {"type": "oauth2", "token_url": "https://auth.example.com"},
            }
        )


def test_load_config_resolves_relative_auth_file_path(tmp_path):
    # Item 2: relative file path resolved against config.yaml directory
    cfg_dir = tmp_path / "sub"
    cfg_dir.mkdir()
    auth_file = cfg_dir / "my_auth.json"
    auth_file.write_text(
        json.dumps({"url": "https://t.com", "client_id": "c", "password": "p"}),
        encoding="utf-8",
    )

    config_yaml = cfg_dir / "config.yaml"
    config_yaml.write_text(
        f"""
server:
  name: test
adapters:
  - id: my_soap
    type: soap
    config:
      wsdl_url: "https://example.com/wsdl"
      auth:
        type: oauth2
        file: "my_auth.json"
""",
        encoding="utf-8",
    )

    loaded = load_config(str(config_yaml))
    soap_cfg = loaded.adapters[0].soap_settings()
    assert soap_cfg.auth.file == str(auth_file.resolve())


def test_init_with_auth_flag_wsdl(tmp_path, monkeypatch):
    # Item 3: init --wsdl-url with --auth writes absolute path to generated config
    out_path = tmp_path / "config.yaml"
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"url": "https://auth.example.com/token", "client_id": "c", "password": "p"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(
        _args(
            wsdl_url="https://svc.example.com/Legacy?wsdl",
            auth=str(auth_file),
            out=str(out_path),
        )
    )

    loaded = load_config(str(out_path))
    soap_cfg = loaded.adapters[0].soap_settings()
    assert soap_cfg.auth.type == "oauth2"
    assert soap_cfg.auth.file == str(auth_file.resolve())


def test_init_with_auth_flag_rest_rejected(tmp_path, monkeypatch, capsys):
    # Item 3: --auth with --rest-url errors out
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"url": "u", "client_id": "c", "password": "p"}), encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit):
        _cmd_init(_args(rest_url="https://api.example.com", auth=str(auth_file), out=str(tmp_path / "config.yaml")))
    captured = capsys.readouterr()
    assert "error: --auth is only allowed with --wsdl-url" in captured.err


def test_init_with_auth_flag_invalid_file(tmp_path, monkeypatch, capsys):
    # Item 3: --auth with missing keys errors out
    bad_auth = tmp_path / "bad_auth.json"
    bad_auth.write_text(json.dumps({"client_id": "c"}), encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit):
        _cmd_init(_args(wsdl_url="https://svc.example.com/wsdl", auth=str(bad_auth), out=str(tmp_path / "config.yaml")))
    captured = capsys.readouterr()
    assert "missing required fields" in captured.err


def test_init_with_auth_flag_git_warning(tmp_path, monkeypatch, capsys):
    # Item 3: auth.json in git repo without .gitignore prints warning
    repo_dir = tmp_path / "my_repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    auth_file = repo_dir / "auth.json"
    auth_file.write_text(json.dumps({"url": "u", "client_id": "c", "password": "p"}), encoding="utf-8")
    out_config = repo_dir / "config.yaml"

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _cmd_init(_args(wsdl_url="https://svc.example.com/wsdl", auth=str(auth_file), out=str(out_config)))
    captured = capsys.readouterr()
    assert "sits inside a git repo" in captured.err
    assert "not in .gitignore" in captured.err


# Live mock SOAP service and mock OAuth server
PORT = 8210
OAUTH_PORT = 8211
ENDPOINT = f"http://127.0.0.1:{PORT}/calculator"
OAUTH_ENDPOINT = f"http://127.0.0.1:{OAUTH_PORT}/oauth/token"

WSDL = f"""<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
                   xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
                   xmlns:tns="http://legacy2mcp.example/calculator"
                   xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                   targetNamespace="http://legacy2mcp.example/calculator">
  <wsdl:types>
    <xsd:schema targetNamespace="http://legacy2mcp.example/calculator" elementFormDefault="qualified">
      <xsd:element name="Add">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="intA" type="xsd:int"/>
            <xsd:element name="intB" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="AddResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="AddResult" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </wsdl:types>

  <wsdl:message name="AddSoapIn"><wsdl:part name="parameters" element="tns:Add"/></wsdl:message>
  <wsdl:message name="AddSoapOut"><wsdl:part name="parameters" element="tns:AddResponse"/></wsdl:message>

  <wsdl:portType name="CalculatorSoap">
    <wsdl:operation name="Add">
      <wsdl:input message="tns:AddSoapIn"/>
      <wsdl:output message="tns:AddSoapOut"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="CalculatorSoapBinding" type="tns:CalculatorSoap">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="Add">
      <soap:operation soapAction="http://legacy2mcp.example/calculator/Add"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="Calculator">
    <wsdl:port name="CalculatorSoap" binding="tns:CalculatorSoapBinding">
      <soap:address location="{ENDPOINT}"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

NS = "http://legacy2mcp.example/calculator"


@pytest.fixture(scope="module")
def mock_oauth_and_soap_servers():
    oauth_token_counter = 0
    soap_revoke_once = True

    oauth_app = Flask("oauth_server")

    @oauth_app.route("/oauth/token", methods=["POST"])
    def token():
        nonlocal oauth_token_counter
        if (
            request.form.get("grant_type") == "client_credentials"
            and request.form.get("client_id") == "test-client"
            and request.form.get("client_secret") == "secret123"
        ):
            oauth_token_counter += 1
            return jsonify({"access_token": f"mock-token-{oauth_token_counter}", "expires_in": 3600})
        return jsonify({"error": "invalid_client"}), 401

    soap_app = Flask("soap_server")

    @soap_app.route("/calculator", methods=["GET"])
    def wsdl():
        return Response(WSDL, mimetype="text/xml")

    @soap_app.route("/calculator", methods=["POST"])
    def soap_call():
        auth_header = request.headers.get("Authorization", "")
        # Expecting Bearer mock-token-N
        if not auth_header.startswith("Bearer mock-token-"):
            return Response("Unauthorized", status=401)

        from lxml import etree

        root = etree.fromstring(request.get_data())
        soap_ns = "http://schemas.xmlsoap.org/soap/envelope/"
        op_el = list(root.find(f"{{{soap_ns}}}Body"))[0]
        a = int(op_el.find(f"{{{NS}}}intA").text)
        b = int(op_el.find(f"{{{NS}}}intB").text)
        resp_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{soap_ns}">
  <soap:Body>
    <AddResponse xmlns="{NS}"><AddResult>{a + b}</AddResult></AddResponse>
  </soap:Body>
</soap:Envelope>"""
        return Response(resp_xml, mimetype="text/xml")

    from werkzeug.serving import make_server

    oauth_srv = make_server("127.0.0.1", OAUTH_PORT, oauth_app)
    soap_srv = make_server("127.0.0.1", PORT, soap_app)

    t1 = threading.Thread(target=oauth_srv.serve_forever, daemon=True)
    t2 = threading.Thread(target=soap_srv.serve_forever, daemon=True)
    t1.start()
    t2.start()

    time.sleep(0.1)
    yield
    oauth_srv.shutdown()
    soap_srv.shutdown()


@pytest.mark.asyncio
async def test_soap_adapter_with_oauth2_end_to_end(mock_oauth_and_soap_servers, tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "url": OAUTH_ENDPOINT,
                "client_id": "test-client",
                "password": "secret123",
            }
        ),
        encoding="utf-8",
    )

    settings = SoapAdapterSettings(
        wsdl_url=ENDPOINT,
        auth=OAuth2AuthConfig(file=str(auth_file)),
    )

    adapter = SoapAdapter("calc", settings)
    tools = adapter.discover_tools()
    assert len(tools) == 1
    assert tools[0].name == "calc_Add"

    result = await adapter.invoke("calc_Add", {"intA": 25, "intB": 17})
    assert result["AddResult"] == 42


@pytest.mark.asyncio
async def test_soap_adapter_oauth2_401_token_revocation_retry(tmp_path):
    # Tests that when an active token is revoked (401), the adapter
    # drops the cached token and retries once with a newly fetched token.
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"url": "https://auth.example.com/token", "client_id": "c", "password": "p"}),
        encoding="utf-8",
    )

    settings = SoapAdapterSettings(
        wsdl_url="https://example.com/wsdl",
        auth=OAuth2AuthConfig(file=str(auth_file)),
    )

    adapter = SoapAdapter("test", settings)
    adapter._tool_index["test_op"] = ("svc", "port", "op", None)

    from zeep.exceptions import TransportError

    fake_client = MagicMock()
    adapter._client = fake_client

    fake_auth = MagicMock()
    adapter._oauth_auth = fake_auth

    attempt_count = 0

    def fake_operation(**kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # First attempt: token was revoked on server, returns 401
            raise TransportError("Unauthorized", status_code=401)
        # Second attempt after refresh: success
        return {"success": True}

    setattr(fake_client.service, "op", fake_operation)

    result = await adapter.invoke("test_op", {})
    assert result == {"success": True}
    assert attempt_count == 2
    fake_auth.invalidate_token.assert_called_once()
