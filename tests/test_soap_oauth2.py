from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, Response, jsonify, request

from legacy2mcp.adapters.base import AdapterError
from legacy2mcp.adapters.soap_adapter import OAuth2BearerAuth, SoapAdapter
from legacy2mcp.config import OAuth2AuthConfig, SoapAdapterSettings


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


def test_oauth2_bearer_auth_token_caching_and_refresh():
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

    with patch("os.environ.get", return_value="secret-val"):
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


def test_oauth2_bearer_auth_error_handling():
    auth_config = OAuth2AuthConfig(
        token_url="https://auth.example.com/token",
        client_id="client-1",
        client_secret_env="SOAP_SECRET",
    )

    with patch("os.environ.get", return_value="secret-val"):
        # HTTP Error
        err_resp = MagicMock()
        err_resp.status_code = 401
        err_resp.text = "Unauthorized"
        with patch("requests.post", return_value=err_resp):
            auth = OAuth2BearerAuth(auth_config)
            with pytest.raises(AdapterError, match="status 401"):
                auth.get_token()

        # Missing access_token
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"error": "no token"}
        with patch("requests.post", return_value=ok_resp):
            auth = OAuth2BearerAuth(auth_config)
            with pytest.raises(AdapterError, match="missing 'access_token'"):
                auth.get_token()


# End-to-end integration test with live mock SOAP service and mock OAuth endpoint
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
    # 1. OAuth server
    oauth_app = Flask("oauth_server")

    @oauth_app.route("/oauth/token", methods=["POST"])
    def token():
        if (
            request.form.get("grant_type") == "client_credentials"
            and request.form.get("client_id") == "test-client"
            and request.form.get("client_secret") == "secret123"
        ):
            return jsonify({"access_token": "mock-bearer-token-999", "expires_in": 3600})
        return jsonify({"error": "invalid_client"}), 401

    # 2. SOAP server
    soap_app = Flask("soap_server")

    @soap_app.route("/calculator", methods=["GET"])
    def wsdl():
        return Response(WSDL, mimetype="text/xml")

    @soap_app.route("/calculator", methods=["POST"])
    def soap_call():
        auth_header = request.headers.get("Authorization")
        if auth_header != "Bearer mock-bearer-token-999":
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
    assert result['AddResult'] == 42
