"""Public URL retrieval and conservative visible-text extraction."""

from html.parser import HTMLParser
from urllib.request import Request, urlopen


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style", "noscript"):
            self._hidden += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style", "noscript") and self._hidden:
            self._hidden -= 1

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or self._hidden:
            return
        if self._in_title:
            self.title += text
        else:
            self.parts.append(text)


def extract_article(html_bytes, url):
    parser = _VisibleTextParser()
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    content = "\n".join(parser.parts).strip()
    if not parser.title or not content:
        raise ValueError("網址無法擷取足夠內容，請改貼上原文")
    return {"title": parser.title.strip(), "content": content, "url": url}


def fetch_article(url, timeout_seconds=20):
    if not (url or "").startswith("https://"):
        raise ValueError("僅支援 HTTPS 網址")
    request = Request(url, headers={"User-Agent": "TaiwanTopicMonitor/0.2"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return extract_article(response.read(), url)
