"""
Regression test for a real-world WSDL shape: ASMX/WCF services commonly
expose one service with two ports (a SOAP 1.1 binding and a SOAP 1.2
binding) that carry identical operations -- e.g. the public
http://www.dneonline.com/calculator.asmx?WSDL, which has a "Calculator"
service with "CalculatorSoap" and "CalculatorSoap12" ports both exposing
Add/Subtract/Multiply/Divide. Before this fix, SoapAdapter.discover_tools()
produced two ToolDefinitions named "calc_Add" (one per port), which
server.py's duplicate-tool-name check then rejected outright.
"""

from __future__ import annotations

import threading
import time

import pytest
from flask import Flask, Response, request

from legacy2mcp.config import AdapterConfig, AuditConfig, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.server import Legacy2McpApp

PORT = 8197
ENDPOINT = f"http://127.0.0.1:{PORT}/calculator"

# Two ports ("CalculatorSoap", "CalculatorSoap12") on one service, both
# binding the same portType -- i.e. the same operations exposed twice.
DUAL_PORT_WSDL = f"""<?xml version="1.0" encoding="utf-8"?>
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

  <wsdl:binding name="CalculatorSoap12Binding" type="tns:CalculatorSoap">
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
    <wsdl:port name="CalculatorSoap12" binding="tns:CalculatorSoap12Binding">
      <soap:address location="{ENDPOINT}"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

NS = "http://legacy2mcp.example/calculator"


def _create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/calculator", methods=["GET"])
    def wsdl():
        return Response(DUAL_PORT_WSDL, mimetype="text/xml")

    @app.route("/calculator", methods=["POST"])
    def soap_call():
        from lxml import etree

        root = etree.fromstring(
            request.get_data(), etree.XMLParser(resolve_entities=False, no_network=True)
        )
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

    return app


@pytest.fixture(scope="module", autouse=True)
def dual_port_soap_service():
    app = _create_app()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, use_reloader=False),
        daemon=True,
    )
    thread.start()
    time.sleep(1)
    yield


def test_duplicate_operation_across_ports_does_not_crash_startup():
    config = Legacy2McpConfig(
        server=ServerConfig(name="test"),
        adapters=[AdapterConfig(id="calc", type="soap", config={"wsdl_url": f"{ENDPOINT}?wsdl"})],
        security=SecurityConfig(audit=AuditConfig(enabled=False)),
    )

    app = Legacy2McpApp(config)
    try:
        names = [name for name in app._tools if name != "describe_capabilities"]
        assert names == ["calc_Add"]  # exposed once, not once per port
    finally:
        app.close()
