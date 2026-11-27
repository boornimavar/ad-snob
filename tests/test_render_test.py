"""render_test/app.py: the Render check page (search faked, Meta never contacted)."""
from render_test import app as render_test


def test_page_shows_the_button():
    client = render_test.app.test_client()
    assert b"Run the test" in client.get("/").data


def test_reports_ads_or_a_refusal_and_limits_how_often(monkeypatch):
    client = render_test.app.test_client()
    render_test._last.update(at=0.0, result=None)

    def refuse(**kwargs):
        raise RuntimeError("Recv failure: Connection was reset")
    monkeypatch.setattr(render_test, "search_ads_broad", refuse)
    page = client.post("/test").data
    assert b"Meta refused or failed" in page and b"no_response" in page

    monkeypatch.setattr(render_test, "search_ads_broad", lambda **kw: (kw["on_collect"](30), [{}] * 12)[1])
    again = client.post("/test").data
    assert b"Please wait" in again  # one search per 5 minutes

    render_test._last.update(at=0.0)
    ok = client.post("/test").data
    assert b"30 ads came back" in ok and b"12 of them image ads" in ok
