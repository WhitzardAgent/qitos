from __future__ import annotations

from datetime import timedelta
import warnings

from qitos.kit.tool.web import HTMLExtractText, HTTPGet, HTTPPost, HTTPRequest


class _Resp:
    def __init__(self, url: str, status_code: int = 200, text: str = "", headers=None, json_value=None):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.reason = "OK" if status_code < 400 else "ERROR"
        self.history = []
        self.elapsed = timedelta(milliseconds=37)
        self._json = json_value
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def test_http_request_parses_json(monkeypatch):
    tool = HTTPRequest()

    def _fake_request(**kwargs):
        return _Resp(
            url=kwargs["url"],
            status_code=200,
            text='{"ok": true}',
            headers={"Content-Type": "application/json"},
            json_value={"ok": True},
        )

    monkeypatch.setattr(tool._session, "request", _fake_request)
    out = tool.run(method="GET", url="https://example.com")
    assert out["status"] == "success"
    assert out["json"] == {"ok": True}
    assert out["content_type"].startswith("application/json")


def test_http_get_and_post_delegate(monkeypatch):
    get_tool = HTTPGet()
    post_tool = HTTPPost()

    calls = []

    def _fake_get_run(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "method": kwargs["method"], "url": kwargs["url"], "content": "ok"}

    monkeypatch.setattr(get_tool._request, "run", _fake_get_run)
    monkeypatch.setattr(post_tool._request, "run", _fake_get_run)

    g = get_tool.run(url="https://a.com", params={"q": "x"})
    p = post_tool.run(url="https://b.com", json_data={"x": 1})
    assert g["method"] == "GET"
    assert p["method"] == "POST"
    assert len(calls) == 2


def test_html_extract_text_title_and_content():
    tool = HTMLExtractText()
    html = """
    <html><head><title>Demo Page</title></head>
    <body>
      <script>ignore()</script>
      <h1>Hello</h1>
      <p>World</p>
      <a href="https://x.com">Link</a>
    </body></html>
    """
    out = tool.run(html=html, max_chars=1000, keep_links=True)
    assert out["status"] == "success"
    assert out["title"] == "Demo Page"
    assert "Hello" in out["content"]
    assert "https://x.com" in out["content"]


def test_html_extract_text_emits_no_deprecation_warning():
    tool = HTMLExtractText()
    html = "<html><head><title>Demo</title></head><body><p>Hello</p></body></html>"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = tool.run(html=html, max_chars=1000)

    assert out["status"] == "success"
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
