"""Gateway accepts PAYMENT-SIGNATURE (v2) and X-PAYMENT (v1); path map is served."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _headers_mod():
    spec = importlib.util.spec_from_file_location(
        "gw_payment_headers", ROOT / "app/routers/payment_headers.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_v2_payment_signature_is_selected():
    value, version = _headers_mod().select_payment_header(
        {"PAYMENT-SIGNATURE": "eyJ4NDAyVmVyc2lvbiI6Mn0="}
    )
    assert version == 2
    assert value.startswith("eyJ4")


def test_v1_x_payment_fallback():
    value, version = _headers_mod().select_payment_header({"X-PAYMENT": "x402 {}"})
    assert (value, version) == ("x402 {}", 1)


def test_no_header():
    assert _headers_mod().select_payment_header({}) == ("", 0)


def test_documented_settle_path_is_served():
    served = (ROOT / "app/routers/x402_settle_api.py").read_text()
    doc = (ROOT.parent / "docs/X402-PATH-MAPPING.md").read_text()
    assert '@router.post("/settle-x402")' in served
    assert "/settle-x402" in doc
