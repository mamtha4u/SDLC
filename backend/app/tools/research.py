"""Internet research tools for agents (Bedrock has no built-in web search, so we run our own).

  web_search(query)        Bing HTML results → [{title, url, snippet}]
  fetch_url(url)           readable text of a public page (SSRF-safe: never internal / metadata addresses)
  pypi_package(name, py)   authoritative package facts from PyPI: latest version, requires_python,
                           and which wheels exist for a given Python version (e.g. cp314 manylinux x86_64)
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
MAX_TEXT = 14_000


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


async def web_search(query: str, n: int = 6) -> list[dict]:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
                                 follow_redirects=True) as c:
        r = await c.get(f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en&count=10")
    out = []
    for block in re.findall(r'<li class="b_algo"(.*?)</li>', r.text, flags=re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.S)
        if not m:
            continue
        snip = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.S) or re.search(r'class="b_lineclamp\d"[^>]*>(.*?)</', block, flags=re.S)
        out.append({"title": _strip(m.group(2)), "url": html.unescape(m.group(1)),
                    "snippet": _strip(snip.group(1)) if snip else ""})
        if len(out) >= n:
            break
    return out


def _public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Can't resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("Only public internet addresses can be fetched")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in {"p", "br", "li", "tr", "h1", "h2", "h3", "h4", "div", "pre", "table"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


async def fetch_url(url: str) -> dict:
    for _ in range(5):  # follow redirects manually so every hop is checked
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("Only http(s) URLs")
        _public_host(u.hostname)
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=False) as c:
            r = await c.get(url)
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        break
    ctype = r.headers.get("content-type", "")
    if "json" in ctype or "text/plain" in ctype:
        text = r.text
    else:
        p = _Text()
        p.feed(r.text)
        text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.parts))).strip()
    return {"url": str(r.url), "status": r.status_code, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


async def pypi_package(name: str, python: str = "3.14") -> dict:
    tag = "cp" + python.replace(".", "")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://pypi.org/pypi/{quote_plus(name)}/json")
    if r.status_code == 404:
        return {"package": name, "found": False}
    info = r.json()
    ver = info["info"]["version"]
    files = info["urls"]
    wheels = [f["filename"] for f in files if f["filename"].endswith(".whl")]
    matching = [w for w in wheels if f"-{tag}-" in w or "-py3-none-any" in w or ("abi3" in w and "-cp3" in w)]
    plats = sorted({p for w in matching for p in ("manylinux x86_64" if "manylinux" in w and "x86_64" in w else "",
                                                     "manylinux aarch64" if "manylinux" in w and "aarch64" in w else "",
                                                     "pure python" if "py3-none-any" in w else "") if p})
    return {
        "package": name, "found": True, "latest_version": ver, "requires_python": info["info"].get("requires_python"),
        "released": files[0]["upload_time"][:10] if files else None,
        f"wheels_for_python_{python}": len(matching), "platforms": plats,
        "example_wheels": matching[:6],
        "verdict": (f"{name} {ver} publishes {len(matching)} wheel(s) usable on Python {python}"
                    + (f" ({', '.join(plats)})" if plats else "")) if matching else
                   f"No {tag} or pure-Python wheels for {name} {ver}; it would need building from source.",
    }
