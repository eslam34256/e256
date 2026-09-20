import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"


def configure(tmp_path, monkeypatch, demo=True):
    monkeypatch.setenv("LEAKHUNTER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEAKHUNTER_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LEAKHUNTER_DEMO_MODE", "1" if demo else "0")
    if not demo:
        monkeypatch.setenv("LEAKHUNTER_ORG_NAME", "Test Organization")
        monkeypatch.setenv("LEAKHUNTER_ADMIN_EMAIL", "admin@test.local")
        monkeypatch.setenv("LEAKHUNTER_BOOTSTRAP_PASSWORD", "test-only-long-password")


def test_landing_and_full_customer_demo(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert any(button.label == "🚀 Launch Customer Demo" for button in app.button)

    next(button for button in app.button if button.label == "🚀 Launch Customer Demo").click().run(timeout=60)
    assert not app.exception
    assert len(app.tabs) == 12
    metrics = {metric.label: metric.value for metric in app.metric}
    assert float(metrics["Spend analyzed"].split()[0].replace(",", "")) > 0
    assert float(metrics["Potential value"].split()[0].replace(",", "")) >= 0
    assert metrics["Suppliers"] == "12"


def test_pilot_bootstrap_reaches_login(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, demo=False)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert not any(button.label == "🚀 Launch Customer Demo" for button in app.button)
    next(button for button in app.button if button.label == "🔐 Sign in to Workspace").click().run()
    assert not app.exception
    assert app.selectbox[0].options == ["Test Organization"]
