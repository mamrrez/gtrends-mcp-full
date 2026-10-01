"""The command line, the settings and the runtime's input parsing."""

import json
import os

import pytest

from conftest import FakeClock, FakeHTTP, trust
from gtrends_mcp_full import cli
from gtrends_mcp_full.runtime import Runtime
from gtrends_mcp_full.settings import Settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in [v for v in os.environ if v.startswith("GTRENDS_")]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("GTRENDS_CONFIG_DIR", str(tmp_path))
    return monkeypatch


@pytest.fixture
def fake_runtime(env, monkeypatch):
    """Make the CLI's runtime talk to the fake Google."""
    http = FakeHTTP()

    def build():
        from gtrends_mcp_full.client import TrendsClient

        rt = Runtime()
        clock = FakeClock()
        rt._client = TrendsClient(rt.settings, rt.store(), http=http, sleep=clock.sleep, clock=clock)
        trust(rt._client)
        rt._client.begin_call(120.0)
        return rt

    monkeypatch.setattr(cli, "_runtime", build)
    return http


def test_settings_defaults(env, tmp_path):
    st = Settings.from_env()
    assert st.geo == "" and st.hl == "en-US" and st.min_interval == 1.5 and st.db_path == tmp_path / "trends.sqlite"
    assert st.proxy is None and st.cookie is None and st.offline is False


def test_settings_from_environment(env):
    env.setenv("GTRENDS_GEO", "ir")
    env.setenv("GTRENDS_TIMEZONE", "Asia/Tehran")
    env.setenv("GTRENDS_MIN_INTERVAL", "0.1")
    env.setenv("GTRENDS_OFFLINE", "yes")
    env.setenv("GTRENDS_PROXY", " http://127.0.0.1:8080 ")
    st = Settings.from_env()
    assert st.geo == "IR" and st.offline is True and st.proxy == "http://127.0.0.1:8080" and st.min_interval == 0.1
    assert st.tz_offset_minutes() == -210  # Tehran is UTC+3:30; Google wants minutes behind UTC
    assert st.tz_label() == "Asia/Tehran"
    env.setenv("GTRENDS_GEO", "worldwide")
    assert Settings.from_env().geo == ""
    env.setenv("GTRENDS_GEO", "us_ca")
    assert Settings.from_env().geo == "US-CA"
    env.setenv("GTRENDS_GEO", "Tehran")
    with pytest.raises(ValueError, match="GTRENDS_GEO"):
        Settings.from_env()
    env.setenv("GTRENDS_GEO", "")
    env.setenv("GTRENDS_MIN_INTERVAL", "fast")
    with pytest.raises(ValueError, match="GTRENDS_MIN_INTERVAL"):
        Settings.from_env()


def test_local_time_follows_daylight_saving_when_no_timezone_is_set(env):
    from gtrends_mcp_full.format import fmt_time, local_dt

    st = Settings.from_env()
    assert st.tzinfo() is None  # not a frozen offset: each timestamp is converted with the rules in force then
    assert local_dt(1_700_000_000, None).utcoffset() is not None and fmt_time(1_700_000_000, None) != "—"
    env.setenv("GTRENDS_TIMEZONE", "Europe/Berlin")
    tz = Settings.from_env().tzinfo()
    assert fmt_time(1_767_225_600, tz) == "2026-01-01 01:00" and fmt_time(1_782_864_000, tz) == "2026-07-01 02:00"


def test_runtime_parses_keywords(env):
    assert Runtime.keywords("tesla,  byd \n rivian; tesla") == ["tesla", "byd", "rivian"]
    assert Runtime.keywords("a + b, c") == ["a + b", "c"]
    with pytest.raises(ValueError, match="at least one keyword"):
        Runtime.keywords(" , ")
    with pytest.raises(ValueError, match="At most 2"):
        Runtime.keywords("a,b,c", cap=2)
    with pytest.raises(ValueError, match="100 characters"):
        Runtime.keywords("x" * 101)


def test_runtime_locations_use_the_configured_default(env):
    rt = Runtime()
    assert rt.geo("") == "" and rt.country("") == "US" and rt.geo("de") == "DE"
    env.setenv("GTRENDS_GEO", "IR")
    rt = Runtime()
    assert rt.geo(None) == "IR" and rt.country("") == "IR" and rt.geo("worldwide") == ""
    assert rt.geos("us, gb;de", [], cap=5) == ["US", "GB", "DE"]
    assert rt.geos("", ["FR"], cap=5) == ["FR"]
    with pytest.raises(ValueError, match="At most 2"):
        rt.geos("US,GB,DE", [], cap=2)
    assert rt.hours(500) == 191 and rt.hours(0) == 1 and rt.hours(24) == 24
    assert rt.is_topic("/m/0dr90d") and rt.is_topic("/g/11c3x48pb7") and not rt.is_topic("tesla")


def test_serve_is_the_default_command(monkeypatch):
    seen = {}
    monkeypatch.setattr("gtrends_mcp_full.server.run", lambda **kw: seen.update(kw))
    assert cli.main([]) == 0 and seen == {"transport": "stdio", "host": "127.0.0.1", "port": 8000}
    assert cli.main(["--transport", "streamable-http", "--port", "9000"]) == 0 and seen["port"] == 9000


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_doctor(fake_runtime, capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OK — every endpoint answered" in out and "Trending Now" in out and "anonymous" in out


def test_doctor_reports_failures(fake_runtime, capsys):
    fake_runtime.fail = [404]
    assert cli.main(["doctor"]) == 3
    out = capsys.readouterr().out
    assert "FAILED topic lookup" in out and "1 of 8 endpoints failed" in out


def test_trending_command(fake_runtime, capsys):
    assert cli.main(["trending", "US", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "champions league" in out and "iphone 18" in out and "ai news" not in out
    assert cli.main(["trending", "IR", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["title"] == "قیمت دلار"  # non-ASCII titles survive the console
    assert cli.main(["trending", "Tehran"]) == 2


def test_snapshot_command_saves_and_reports(fake_runtime, capsys, tmp_path):
    assert cli.main(["snapshot", "US,GB"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [r["trends"] for r in result["saved"]] == [5, 2] and result["failed"] == 0
    from gtrends_mcp_full.store import Store

    assert len(Store(tmp_path / "trends.sqlite").trending_search(None, None)) == 7


def test_tools_command_prints_the_reference(capsys):
    assert cli.main(["tools", "--brief"]) == 0
    out = capsys.readouterr().out
    assert "| `trending_now` |" in out and "| `share_of_search` |" in out
