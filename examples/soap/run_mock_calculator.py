"""Run this to start the demo Calculator SOAP service on http://127.0.0.1:8123/calculator"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.mock_soap_service import create_app

if __name__ == "__main__":
    create_app("http://127.0.0.1:8123/calculator").run(host="0.0.0.0", port=8123)
