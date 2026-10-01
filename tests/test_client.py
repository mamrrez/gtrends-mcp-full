"""The client against a fake Google: wire formats, the session cookie, pacing, back-off and the cache."""

import json

import pytest

from conftest import FakeHTTP, make_client
from gtrends_mcp_full.client import (
    COOKIE_MATURITY,
    RELATED_SPACING,
    Offline,
    RateLimited,
    TrendsError,
    normalize_geo,
    normalize_property,
    parse_batch,
    strip_xssi,
)

ITEM = {"keyword": "tesla", "geo": "US", "time": "today 12-m"}


def test_strip_xssi_and_parse_batch():
    assert strip_xssi(")]}'\n{\"a\":1}") == {"a": 1}
    assert strip_xssi(")]}',\n[1,2]") == [1, 2]
    with pytest.raises(TrendsError, match="not JSON"):
        strip_xssi("<html>sorry</html>")
    envelope = ")]}'\n\n42\n" + json.dumps([["wrb.fr", "i0OFE", json.dumps([None, [["x"]]]), None]]) + '\n25\n[["di",21]]'
    assert parse_batch(envelope, "i0OFE") == [None, [["x"]]]
    with pytest.raises(TrendsError, match="rejected"):
        parse_batch(')]}\'\n\n[["er",null,null,null,null,400]]', "i0OFE")
    with pytest.raises(TrendsError, match="no data"):
        parse_batch(")]}'\n\n", "i0OFE")


def test_geo_and_property_normalisation():
    assert normalize_geo("") == "" and normalize_geo("Worldwide") == "" and normalize_geo(None) == ""
    assert normalize_geo("ir") == "IR" and normalize_geo("us_ca") == "US-CA" and normalize_geo("US-CA-807") == "US-CA-807" and normalize_geo("IR-23") == "IR-23"
    with pytest.raises(ValueError, match="find_location"):
        normalize_geo("Tehran")
    assert normalize_property("YouTube") == "youtube" and normalize_property("shopping") == "froogle" and normalize_property("") == ""
    with pytest.raises(ValueError):
        normalize_property("tiktok")


def test_interest_over_time_parses_the_chart(tmp_path):
    c = make_client(tmp_path)
    data = c.interest_over_time([ITEM, {**ITEM, "keyword": "byd"}])
    assert data["labels"] == ["tesla", "byd"] and data["resolution"] == "WEEK" and not data["multirange"]
    assert len(data["points"]) > 50
    assert data["points"][0]["values"] == [100.0, 10.0]
    assert data["points"][-1]["partial"] is True
    assert c.user_type == "USER_TYPE_SCRAPER" and "anonymous" in c.session_kind()


def test_an_identical_question_is_answered_from_the_cache(tmp_path):
    c = make_client(tmp_path)
    c.interest_over_time([ITEM])
    sent = c.requests_sent
    c.interest_over_time([ITEM])
    assert c.requests_sent == sent and c.cache_hits == 1


def test_a_second_chart_for_the_same_question_reuses_the_tokens(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    c.interest_over_time([ITEM])
    c.interest_by_region([ITEM])
    c.related(ITEM)
    assert http.paths().count("/trends/api/explore") == 1
    assert c.requests_sent == 4


def test_more_than_five_terms_never_reach_google(tmp_path):
    c = make_client(tmp_path)
    with pytest.raises(ValueError, match="at most 5"):
        c.explore([{**ITEM, "keyword": str(i)} for i in range(6)])
    assert c.requests_sent == 0


def test_mixing_worldwide_with_countries_is_explained(tmp_path):
    c = make_client(tmp_path)
    with pytest.raises(TrendsError, match="Worldwide cannot be mixed"):
        c.interest_over_time([ITEM, {**ITEM, "geo": ""}])


def test_multirange_points_carry_one_timestamp_per_range(tmp_path):
    c = make_client(tmp_path)
    data = c.interest_over_time([{**ITEM, "time": "2025-01-01 2025-03-31"}, {**ITEM, "time": "2024-01-01 2024-03-31"}])
    assert data["multirange"] and len(data["points"][0]["t"]) == 2 and len(data["points"][0]["values"]) == 2


def test_regions_resolution_and_low_volume_are_passed_on(tmp_path):
    c = make_client(tmp_path)
    assert [r["code"] for r in c.interest_by_region([ITEM])["rows"]] == ["US-CA", "US-TX", "US-WY"]
    assert any(r["code"] == "XX" for r in c.interest_by_region([ITEM], low_volume=True)["rows"])
    city = c.interest_by_region([ITEM], resolution="city")
    assert city["resolution"] == "CITY" and city["rows"][0]["lat"] == 1.0
    with pytest.raises(ValueError):
        c.interest_by_region([ITEM], resolution="street")


def test_breakout_is_decided_by_the_number_not_the_word(tmp_path):
    c = make_client(tmp_path)
    rel = c.related(ITEM)
    assert rel["rising"][0] == {"text": "tesla 2027", "value": 62300.0, "growth": "Breakout", "breakout": True, "mid": "", "type": ""}
    assert rel["rising"][1]["growth"] == "+250%" and not rel["rising"][1]["breakout"]
    assert rel["top"][0]["text"] == "tesla price" and rel["top"][0]["growth"] == ""


def test_related_topics_are_empty_for_an_anonymous_session_and_present_for_a_signed_in_one(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    assert c.related(ITEM, "topics")["top"] == []
    http.user_type = "USER_TYPE_LEGIT_USER"
    c2 = make_client(tmp_path / "b", http)
    assert c2.related(ITEM, "topics")["top"][0]["mid"] == "/m/0dr90d"
    assert "signed-in" in c2.session_kind()


def test_trending_now_rows(tmp_path):
    c = make_client(tmp_path)
    trends = c.trending_now("US", 24)
    t = trends[0]
    assert t["title"] == "champions league" and t["volume"] == 500000 and t["growth"] == 1000
    assert t["ended"] is None and t["categories"] == ["Sports"] and t["breakdown"][1] == "ucl draw"
    assert trends[1]["ended"] is not None
    assert c.trend_news(t["news_tokens"])[0]["source"] == "Example News"
    with pytest.raises(ValueError, match="no worldwide"):
        c.trending_now("")


def test_rss_feed(tmp_path):
    items = make_client(tmp_path).trending_rss("US")
    assert items[0]["title"] == "champions league" and items[0]["traffic"] == "500K+"
    assert items[0]["news"][0]["url"] == "https://news.example/0" and items[0]["started"] > 0


def test_lookups(tmp_path):
    c = make_client(tmp_path)
    assert c.autocomplete("tesla")[0]["mid"] == "/m/0dr90d"
    assert c.geo_tree()["children"][0]["id"] == "IR"
    assert c.category_tree()["children"][0]["id"] == 47


# -- the session cookie ---------------------------------------------------------


def test_first_explore_request_gets_the_cookie_and_is_repeated_at_once(tmp_path):
    http = FakeHTTP()
    http.require_cookie = True
    c = make_client(tmp_path, http, trusted_session=False)
    c.begin_call()
    assert c.interest_over_time([ITEM])["points"]
    assert http.sent_cookies[:2] == [None, "NID=fresh"]  # refused without, accepted with
    assert c._blocked == {} and max(c.slept, default=0) < 4  # the refusal was not treated as a rate limit: no back-off
    assert json.loads((tmp_path / "cookies.json").read_text())["proven"] is True


def test_a_new_cookie_is_not_sent_to_endpoints_that_work_without_one(tmp_path):
    http = FakeHTTP()
    http.require_cookie = http.challenge_new = True
    c = make_client(tmp_path, http, trusted_session=False)
    c.begin_call()
    c.trending_now("US")  # hands out a cookie as a side effect
    c.autocomplete("tesla")
    c.geo_tree()
    assert http.sent_cookies == [None, None, None]
    assert c._cookie["value"] == "fresh" and c.session_ready_in() > 0


def test_a_challenged_new_cookie_waits_until_it_has_matured(tmp_path):
    http = FakeHTTP()
    http.require_cookie = http.challenge_new = True
    clock = [1000.0]
    c = make_client(tmp_path, http, trusted_session=False)
    c._now = lambda: clock[0]
    c.begin_call(50.0)
    with pytest.raises(RateLimited, match="validating this server's new session"):
        c.interest_over_time([ITEM])
    assert http.sent_cookies == [None, "NID=fresh"]
    assert c._blocked == {}  # nothing is blocked: other tools keep working

    # asked again too early: no request is sent at all
    c.begin_call(50.0)
    with pytest.raises(RateLimited, match="validating"):
        c.interest_over_time([ITEM])
    assert len(http.calls) == 2
    c.trending_now("US")  # works meanwhile, without the cookie
    assert http.sent_cookies[-1] is None

    # once the cookie is old enough it is sent again and accepted
    clock[0] += COOKIE_MATURITY + 1
    http.mature = True
    c.begin_call(50.0)
    assert c.interest_over_time([ITEM])["points"]
    assert c._cookie["proven"] is True and c.session_ready_in() == 0


def test_a_short_remaining_wait_is_slept_through(tmp_path):
    http = FakeHTTP()
    http.require_cookie = http.challenge_new = True
    clock = [1000.0]
    c = make_client(tmp_path, http, trusted_session=False)

    def sleep(seconds):
        c.slept.append(seconds)
        clock[0] += seconds
        if clock[0] - 1000.0 >= COOKIE_MATURITY:
            http.mature = True

    c._now, c._sleep = (lambda: clock[0]), sleep
    c.begin_call(500.0)
    assert c.interest_over_time([ITEM])["points"]
    assert any(abs(s - COOKIE_MATURITY) < 5 for s in c.slept)  # it waited out the rest of the cookie's first 100 seconds


def test_the_stored_cookie_is_used_by_the_next_run(tmp_path):
    http = FakeHTTP()
    http.require_cookie = True
    c = make_client(tmp_path, http, trusted_session=False)
    c.interest_over_time([ITEM])
    http2 = FakeHTTP()
    http2.require_cookie = True
    c2 = make_client(tmp_path, http2, trusted_session=False)
    c2.interest_over_time([{**ITEM, "keyword": "byd"}])
    assert http2.sent_cookies[0] == "NID=fresh"  # no refusal, no second request


def test_a_supplied_signed_in_cookie_is_sent_as_is_and_never_stored(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http, trusted_session=False, GTRENDS_COOKIE="SID=abc; HSID=def")
    c.trending_now("US")
    c.interest_over_time([ITEM])
    assert set(http.sent_cookies) == {"SID=abc; HSID=def"}
    assert not (tmp_path / "cookies.json").exists()


def test_the_http_library_is_never_left_holding_a_cookie(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    c.trending_now("US")
    assert http.cookies == {}


# -- pacing, back-off, fallbacks -------------------------------------------------


def test_requests_are_spaced_out(tmp_path):
    now = [100.0]
    c = make_client(tmp_path)
    c._clock = lambda: now[0]
    c.autocomplete("tesla")
    c.trending_now("US")
    assert c.slept and abs(c.slept[-1] - 1.5) < 1e-9


def test_related_searches_are_kept_further_apart_than_other_requests(tmp_path):
    now = [100.0]
    c = make_client(tmp_path)
    c._clock = lambda: now[0]
    c._sleep = lambda s: (c.slept.append(s), now.__setitem__(0, now[0] + s))
    c.related(ITEM)
    del c.slept[:]
    c.related({**ITEM, "keyword": "byd"})
    # explore is paced normally; the second related-searches call waits out the rest of its own, longer gap
    assert c.slept == [2.0, RELATED_SPACING - 2.0]


def test_a_rate_limit_backs_off_and_then_succeeds(tmp_path):
    http = FakeHTTP()
    http.fail = [429]
    c = make_client(tmp_path, http)
    c.begin_call(60.0)
    assert c.trending_now("US")
    assert any(4.0 <= s <= 5.5 for s in c.slept) and c._blocked == {}


def test_a_persistent_rate_limit_stops_that_group_of_endpoints_only(tmp_path):
    http = FakeHTTP()
    http.fail = [429] * 10
    now = [0.0]
    c = make_client(tmp_path, http)
    c._clock = lambda: now[0]
    c._sleep = lambda s: now.__setitem__(0, now[0] + s)
    c.begin_call(120.0)
    with pytest.raises(RateLimited, match="rate-limiting") as e:
        c.interest_over_time([ITEM])
    assert e.value.retry_after >= 60 and c.blocked_for("explore") > 0
    sent = len(http.calls)
    with pytest.raises(RateLimited):  # refused without sending anything
        c.interest_over_time([{**ITEM, "keyword": "x"}])
    assert len(http.calls) == sent
    http.fail = []
    assert c.trending_now("US")  # a different group of endpoints still answers
    now[0] += 10_000
    c.begin_call(120.0)
    assert c.interest_over_time([ITEM])["points"]


def test_an_expired_answer_is_served_when_google_refuses(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http, GTRENDS_TRENDING_TTL="0.001")
    first = c.trending_now("US")
    import time

    time.sleep(0.01)
    http.fail = [429] * 10
    c.begin_call(1.0)
    assert c.trending_now("US") == first
    assert "showing the answer saved" in c.notes()[0]


def test_bad_request_and_missing_endpoint_messages(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    http.fail = [400]
    with pytest.raises(TrendsError, match="unknown location code"):
        c.interest_over_time([ITEM])
    http.fail = [404]
    with pytest.raises(TrendsError, match="no such endpoint"):
        c.trending_rss("US")
    http.fail = [500, 500, 500]
    c.begin_call(60.0)
    with pytest.raises(TrendsError, match="HTTP 500"):
        c.autocomplete("x")


def test_network_failure_is_reported_with_its_reason(tmp_path):
    class Broken(FakeHTTP):
        def request(self, *a, **k):
            raise ConnectionError("no route")

    with pytest.raises(TrendsError, match="Could not reach Google Trends .*no route"):
        make_client(tmp_path, Broken()).trending_now("US")


def test_offline_mode_answers_from_cache_only(tmp_path):
    make_client(tmp_path).trending_now("US")
    http = FakeHTTP()
    off = make_client(tmp_path, http, GTRENDS_OFFLINE="1")
    assert off.trending_now("US")
    with pytest.raises(Offline):
        off.trending_now("GB")
    assert http.calls == []


def test_check_reports_each_endpoint(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    results = c.check()
    assert len(results) == 8 and all(ok for _, ok, _ in results)
    http2 = FakeHTTP()
    http2.fail = [404]
    bad = make_client(tmp_path / "b", http2).check()
    assert bad[0][1] is False and "no such endpoint" in bad[0][2] and all(ok for _, ok, _ in bad[1:])


def test_forget_session_clears_cookie_and_back_off(tmp_path):
    c = make_client(tmp_path)
    c._save_cookie()
    c._blocked["explore"] = 1e12
    c.forget_session()
    assert c._cookie is None and c.blocked_for() == 0 and not (tmp_path / "cookies.json").exists()


# -- found in review ---------------------------------------------------------------


def expire_cache(client) -> None:
    """Make every saved answer too old to be used as fresh (it stays available as a fallback)."""
    db = client.store._db()
    db.execute("UPDATE cache SET expires = 0")
    db.commit()


def test_an_unproven_cookie_never_goes_to_endpoints_that_work_without_one_even_when_old(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http, trusted_session=False)
    c._cookie, c._cookie_loaded = {"value": "old", "issued": 0.0, "proven": False, "rejected": True}, True
    c.autocomplete("tesla")
    c.trending_now("US")
    assert http.sent_cookies == [None, None]
    c.interest_over_time([ITEM])  # the chart endpoints are where it gets proven
    assert http.sent_cookies[2] == "NID=old" and c._cookie["proven"] is True
    c.autocomplete("byd")
    assert http.sent_cookies[-1] == "NID=old"


def test_a_lookup_that_keeps_being_refused_backs_off_instead_of_looping(tmp_path):
    http = FakeHTTP()
    http.fail = [429] * 500
    c = make_client(tmp_path, http, trusted_session=False)
    c._cookie, c._cookie_loaded = {"value": "old", "issued": 0.0, "proven": False, "rejected": True}, True
    c.begin_call(60.0)
    with pytest.raises(RateLimited):
        c.autocomplete("tesla")
    assert len(http.calls) <= 4 and sum(c.slept) >= 4  # a handful of paced attempts, then silence


def test_a_second_handshake_in_one_request_is_treated_as_a_rate_limit(tmp_path):
    class AlwaysNew(FakeHTTP):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            return type(FakeHTTP().request("GET", "https://trends.google.com/x"))(429, "", cookies={"NID": f"n{len(self.calls)}"})

    http = AlwaysNew()
    c = make_client(tmp_path, http, trusted_session=False)
    c.begin_call(20.0)
    with pytest.raises(RateLimited):
        c.interest_over_time([ITEM])
    assert len(http.calls) <= 4


def test_fresh_means_fresh_no_stale_fallback(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http, GTRENDS_TRENDING_TTL="0.001")
    c.trending_now("US")
    import time

    time.sleep(0.01)
    http.fail = [404] * 5
    with pytest.raises(TrendsError, match="no such endpoint"):
        c.trending_now("US", fresh=True)
    assert c.trending_now("US")  # without `fresh` the saved answer is still offered, labelled


def test_check_never_answers_from_the_cache(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    assert all(ok for _, ok, _ in c.check())  # fills every cache
    http.fail = [404] * 100
    c.begin_call(300.0)
    results = c.check()
    assert not any(ok for _, ok, _ in results) and all("no such endpoint" in detail for _, _, detail in results)


def test_a_cookie_google_stops_honouring_is_replaced(tmp_path):
    http = FakeHTTP()
    http.require_cookie = True
    http.revoked = "test"
    c = make_client(tmp_path, http)  # holds the established cookie "test"
    c.begin_call(60.0)
    assert c.interest_over_time([ITEM])["points"]
    assert http.sent_cookies[:2] == ["NID=test", "NID=fresh"]
    assert c._cookie["value"] == "fresh" and c._cookie["proven"] is True and c._blocked == {}


def test_a_stored_cookie_past_its_lifetime_is_dropped(tmp_path):
    (tmp_path / "cookies.json").write_text(json.dumps({"value": "ancient", "issued": 1.0, "proven": True}))
    http = FakeHTTP()
    http.require_cookie = True
    c = make_client(tmp_path, http, trusted_session=False)
    c.interest_over_time([ITEM])
    assert "NID=ancient" not in http.sent_cookies and c._cookie["value"] == "fresh"


def test_tokens_are_never_served_stale(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    c.explore([ITEM])
    expire_cache(c)
    http.fail = [500, 500, 500]
    c.begin_call(60.0)
    with pytest.raises(TrendsError, match="HTTP 500"):
        c.explore([ITEM])
    assert c.notes() == []


def test_a_changed_answer_format_is_reported_as_such_and_falls_back(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    good = c.interest_over_time([ITEM])
    expire_cache(c)
    real = http._explore
    http._explore = lambda req: {"widgets": [{"id": "TIMESERIES", "type": "fe_line_chart"}], "keywords": []}  # no token, no request
    c.begin_call(60.0)
    assert c.interest_over_time([ITEM]) == good and "format this version does not understand" in c.notes()[0]
    with pytest.raises(TrendsError, match="format this version does not understand"):
        c.interest_over_time([{**ITEM, "keyword": "never seen"}])
    http._explore = real


def test_one_refusal_with_no_time_to_retry_earns_only_a_short_pause(tmp_path):
    http = FakeHTTP()
    http.fail = [429]
    c = make_client(tmp_path, http)
    c.begin_call(3.0)
    with pytest.raises(RateLimited) as e:
        c.trending_now("US")
    assert e.value.retry_after == 15.0 and 0 < c.blocked_for("trending") <= 15.0


def test_an_empty_batch_payload_is_an_error_not_an_empty_trend_list(tmp_path):
    http = FakeHTTP()
    http._batch = lambda data: ")]}'\n\n" + json.dumps([["wrb.fr", "i0OFE", None, None]])
    c = make_client(tmp_path, http)
    with pytest.raises(TrendsError, match="no data"):
        c.trending_now("US")


def test_running_out_of_budget_is_an_interruption(tmp_path):
    from gtrends_mcp_full.client import Interrupted, OutOfTime

    c = make_client(tmp_path)
    c.begin_call(2.0)
    c.trending_now("US")
    c.trending_now("GB")
    with pytest.raises(OutOfTime) as e:
        c.trending_now("DE")
    assert isinstance(e.value, Interrupted) and c.blocked_for() == 0  # nothing is blocked: the next call can ask


def test_the_lock_is_free_while_waiting_and_while_the_request_is_on_the_wire(tmp_path):
    import threading

    free: list[bool] = []
    c = None

    def probe() -> None:
        got = []
        t = threading.Thread(target=lambda: got.append(c._lock.acquire(timeout=0.5) and (c._lock.release() or True)))
        t.start()
        t.join()
        free.append(bool(got and got[0]))

    class Watching(FakeHTTP):
        def request(self, *a, **k):
            probe()
            return super().request(*a, **k)

    c = make_client(tmp_path, Watching())
    c._sleep = lambda s: probe()
    c.autocomplete("tesla")
    c.trending_now("US")  # paced: sleeps first, then sends
    assert len(free) == 3 and all(free)


def test_a_refusal_widens_the_gap_for_that_group_for_a_while(tmp_path):
    http = FakeHTTP()
    c = make_client(tmp_path, http)
    c.begin_call(600.0)
    c.interest_over_time([ITEM])
    usual = c.slept[-1]
    assert usual == 2.0 and c.pace_factor("explore") == 1.0 and c.refusals == 0

    http.fail = [429]
    c.interest_over_time([{**ITEM, "keyword": "byd"}])  # refused once, then answered
    assert c.refusals == 1 and c.pace_factor("explore") == 1.5 and c.pace_factor("trending") == 1.0
    del c.slept[:]
    c.interest_over_time([{**ITEM, "keyword": "rivian"}])
    assert c.slept == [3.0, 3.0]  # both requests of the next chart keep the wider gap
    c.trending_now("US")
    assert c.slept[-1] == 1.5  # another group of endpoints is not slowed

    http.fail = [429, 429, 429]
    c.interest_over_time([{**ITEM, "keyword": "lucid"}])
    assert c.pace_factor("explore") == 3.0  # it widens with each refusal, up to three times the usual

    c._clock.now += 301
    assert c.pace_factor("explore") == 1.0  # and returns to normal after five quiet minutes
