"""
A tiny hand-rolled SOAP 1.1 service, used two ways:

1. As a pytest fixture (spun up on localhost) so the adapter test suite
   doesn't depend on any external network access.
2. As the "sample SOAP service" shipped in the Docker image / examples,
   so `docker compose up` gives a real WSDL to point legacy2mcp at
   without needing an internet connection.

It intentionally mirrors the shape of the well-known public
"Calculator" WSDL (http://www.dneonline.com/calculator.asmx?WSDL):
four operations (Add, Subtract, Multiply, Divide), each taking two
ints and returning an int. That keeps the demo instantly recognizable
to anyone who has played with a SOAP tutorial before.

This is NOT a general-purpose SOAP server -- it is a fixed fixture,
kept deliberately small so it's obvious what it does.
"""

from __future__ import annotations

from flask import Flask, request, Response

WSDL = """<?xml version="1.0" encoding="utf-8"?>
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

      <xsd:element name="Subtract">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="intA" type="xsd:int"/>
            <xsd:element name="intB" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="SubtractResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="SubtractResult" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>

      <xsd:element name="Multiply">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="intA" type="xsd:int"/>
            <xsd:element name="intB" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="MultiplyResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="MultiplyResult" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>

      <xsd:element name="Divide">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="intA" type="xsd:int"/>
            <xsd:element name="intB" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
      <xsd:element name="DivideResponse">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="DivideResult" type="xsd:int"/>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </wsdl:types>

  <wsdl:message name="AddSoapIn"><wsdl:part name="parameters" element="tns:Add"/></wsdl:message>
  <wsdl:message name="AddSoapOut"><wsdl:part name="parameters" element="tns:AddResponse"/></wsdl:message>
  <wsdl:message name="SubtractSoapIn"><wsdl:part name="parameters" element="tns:Subtract"/></wsdl:message>
  <wsdl:message name="SubtractSoapOut"><wsdl:part name="parameters" element="tns:SubtractResponse"/></wsdl:message>
  <wsdl:message name="MultiplySoapIn"><wsdl:part name="parameters" element="tns:Multiply"/></wsdl:message>
  <wsdl:message name="MultiplySoapOut"><wsdl:part name="parameters" element="tns:MultiplyResponse"/></wsdl:message>
  <wsdl:message name="DivideSoapIn"><wsdl:part name="parameters" element="tns:Divide"/></wsdl:message>
  <wsdl:message name="DivideSoapOut"><wsdl:part name="parameters" element="tns:DivideResponse"/></wsdl:message>

  <wsdl:portType name="CalculatorSoap">
    <wsdl:operation name="Add">
      <wsdl:documentation>Adds two integers.</wsdl:documentation>
      <wsdl:input message="tns:AddSoapIn"/>
      <wsdl:output message="tns:AddSoapOut"/>
    </wsdl:operation>
    <wsdl:operation name="Subtract">
      <wsdl:documentation>Subtracts intB from intA.</wsdl:documentation>
      <wsdl:input message="tns:SubtractSoapIn"/>
      <wsdl:output message="tns:SubtractSoapOut"/>
    </wsdl:operation>
    <wsdl:operation name="Multiply">
      <wsdl:documentation>Multiplies two integers.</wsdl:documentation>
      <wsdl:input message="tns:MultiplySoapIn"/>
      <wsdl:output message="tns:MultiplySoapOut"/>
    </wsdl:operation>
    <wsdl:operation name="Divide">
      <wsdl:documentation>Divides intA by intB.</wsdl:documentation>
      <wsdl:input message="tns:DivideSoapIn"/>
      <wsdl:output message="tns:DivideSoapOut"/>
    </wsdl:operation>
  </wsdl:portType>

  <wsdl:binding name="CalculatorSoap" type="tns:CalculatorSoap">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="Add">
      <soap:operation soapAction="http://legacy2mcp.example/calculator/Add"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
    <wsdl:operation name="Subtract">
      <soap:operation soapAction="http://legacy2mcp.example/calculator/Subtract"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
    <wsdl:operation name="Multiply">
      <soap:operation soapAction="http://legacy2mcp.example/calculator/Multiply"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
    <wsdl:operation name="Divide">
      <soap:operation soapAction="http://legacy2mcp.example/calculator/Divide"/>
      <wsdl:input><soap:body use="literal"/></wsdl:input>
      <wsdl:output><soap:body use="literal"/></wsdl:output>
    </wsdl:operation>
  </wsdl:binding>

  <wsdl:service name="Calculator">
    <wsdl:port name="CalculatorSoap" binding="tns:CalculatorSoap">
      <soap:address location="{ENDPOINT}"/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

NS = "http://legacy2mcp.example/calculator"


def create_app(endpoint_url: str = "http://127.0.0.1:8123/calculator") -> Flask:
    app = Flask(__name__)
    wsdl_xml = WSDL.replace("{ENDPOINT}", endpoint_url)

    @app.route("/calculator", methods=["GET"])
    def wsdl():
        if request.args.get("wsdl") is not None or request.args.get("WSDL") is not None:
            return Response(wsdl_xml, mimetype="text/xml")
        return Response("Calculator SOAP endpoint. Append ?wsdl to GET.", mimetype="text/plain")

    @app.route("/calculator", methods=["POST"])
    def soap_call():
        from lxml import etree

        body = request.get_data()
        root = etree.fromstring(body)
        soap_ns = "http://schemas.xmlsoap.org/soap/envelope/"
        body_el = root.find(f"{{{soap_ns}}}Body")
        op_el = list(body_el)[0]
        op_name = etree.QName(op_el.tag).localname

        def get_int(el, name):
            return int(el.find(f"{{{NS}}}{name}").text)

        a = get_int(op_el, "intA")
        b = get_int(op_el, "intB")

        if op_name == "Add":
            result, result_tag = a + b, "AddResult"
        elif op_name == "Subtract":
            result, result_tag = a - b, "SubtractResult"
        elif op_name == "Multiply":
            result, result_tag = a * b, "MultiplyResult"
        elif op_name == "Divide":
            if b == 0:
                return _soap_fault("Client", "Division by zero"), 500
            result, result_tag = a // b, "DivideResult"
        else:
            return _soap_fault("Client", f"Unknown operation {op_name}"), 500

        resp_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{soap_ns}">
  <soap:Body>
    <{op_name}Response xmlns="{NS}">
      <{result_tag}>{result}</{result_tag}>
    </{op_name}Response>
  </soap:Body>
</soap:Envelope>"""
        return Response(resp_xml, mimetype="text/xml")

    return app


def _soap_fault(fault_code: str, message: str) -> Response:
    soap_ns = "http://schemas.xmlsoap.org/soap/envelope/"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{soap_ns}">
  <soap:Body>
    <soap:Fault>
      <faultcode>{fault_code}</faultcode>
      <faultstring>{message}</faultstring>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>"""
    return Response(xml, mimetype="text/xml")


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8123)
