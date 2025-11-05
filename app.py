# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile

app = Flask(__name__)

# ----------------- helpers -----------------

def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 80:
        return s
    cut = s[:80]
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut


def extract_highres_images(html: str):
    """Zwraca listę max 12 pełnych URL-i zdjęć produktu z Amazona."""
    urls = []

    # 1) hiRes
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        urls.append(u)

    # 2) large
    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u not in urls:
            urls.append(u)

    # 3) Fallback: data-a-dynamic-image (pewniak)
    dyn = re.search(r'data-a-dynamic-image="({[^"]+})"', html)
    if dyn:
        block = dyn.group(1).replace("&quot;", '"')
        try:
            obj = json.loads(block)
            for u in obj.keys():
                urls.append(u)
        except Exception:
            pass

    # Normalizacja i odfiltrowanie miniaturek
    clean = []
    seen = set()
    for u in urls:
        if not u:
            continue
        u = u.replace("\\u0026", "&")
        # odetnij parametry typu ?v=3, ?abc=...
        u = u.split("?", 1)[0]
        # usuń wzorce miniaturek (…._AC_SX342_.jpg => .jpg)
        if re.search(r'\._[^.]+\.', u):
            u = re.sub(r'\._[^.]+\.', '.', u)
        # tylko grafiki i bez duplikatów
        if u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in seen:
            seen.add(u)
            clean.append(u)

    return clean[:12]


# ----------------- core scraper -----------------

def fetch_amazon(url_or_asin: str):
    API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"  # <— Twój klucz ScraperAPI

    url_or_asin = url_or_asin.strip()
    if "amazon" not in url_or_asin:
        amazon_url = f"https://www.amazon.co.uk/dp/{url_or_asin.upper()}"
    else:
        amazon_url = url_or_asin.split("?", 1)[0]

    def fetch(render=False) -> str:
        url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}"
        if render:
            url += "&render=true"
        r = requests.get(url, timeout=25)
        r.raise_for_status()
        return r.text

    # 1) szybka próba (bez render)
    html = fetch(render=False)
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("span", {"id": "productTitle"})

    # 2) fallback (render = true), gdy Amazon utrudnia
    if not title_tag:
        html = fetch(render=True)
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("span", {"id": "productTitle"})

    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    # zdjęcia wyciągamy z TEGO html powyżej (bezpośrednio ze ScraperAPI)
    images = extract_highres_images(html)

    # bullets
    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    # meta (Brand/Colour itp.)
    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ", strip=True)
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    return {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": meta
    }


def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = []
    lines.append(title)
    lines.append("")

    if brand or colour:
        if brand:
            lines.append(f"Brand: {brand}")
        if colour:
            lines.append(f"Colour: {colour}")
        lines.append("")

    if bullets:
        lines.append("✨ Key Features")
        lines.append("")
        for b in bullets[:10]:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
            lines.append("")

    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")
    return "\n".join(lines)


# ----------------- routes -----------------

@app.route("/health")
def health():
    return "ok", 200

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    data = fetch_amazon(url)

    listing_text = generate_listing_text(data["title"], data["meta"], data["bullets"])

    return render_template(
        "result.html",
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        listing_text=listing_text
    )

@app.route("/proxy")
def proxy():
    u = unquote(request.args.get("u", ""))
    r = requests.get(u, timeout=25)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
def download_zip():
    selected = request.form.getlist("selected")
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        for i, u in enumerate(selected):
            z.writestr(f"image_{i+1}.jpg", requests.get(u, timeout=25).content)
    mem.seek(0)
    return send_file(mem, mimetype="application/zip",
                     as_attachment=True, download_name="images.zip")


if __name__ == "__main__":
    app.run(debug=True, port=8000)
