from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
from datetime import datetime
import json
import html as html_unescape  # do odkodowania &quot; itp.

app = Flask(__name__)

def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 80:
        return s
    cut = s[:80]
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut

import json
import re

import re
import json

def extract_highres_images(html: str):
    urls = []

    print("IMAGES FOUND:", len(images))
    if images[:3]:
        print("SAMPLE:", images[:3])

    # --- wariant 0: czasem response ma encje HTML (Render/Scraper) ---
    # spróbujmy też na odkodowanej wersji
    try:
        from html import unescape as _unescape
        html_u = _unescape(html)
    except:
        html_u = html

    def add(u):
        if u and any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            urls.append(u)

    # --- wariant 1: hiRes ---
    for h in (html, html_u):
        for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', h):
            add(m.group(1).replace("\\u0026", "&"))

    # --- wariant 2: large ---
    for h in (html, html_u):
        for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', h):
            u = m.group(1).replace("\\u0026", "&")
            if u not in urls:
                add(u)

    # --- wariant 3: data-a-dynamic-image (najpewniejszy) ---
    for h in (html, html_u):
        m = re.search(r'data-a-dynamic-image\s*=\s*"({.*?})"', h)
        if not m:
            m = re.search(r"data-a-dynamic-image\s*=\s*'({.*?})'", h)
        if m:
            try:
                block = m.group(1).replace("&quot;", '"')
                obj = json.loads(block)
                for u in obj.keys():
                    add(u)
            except:
                pass

    # --- wariant 4: JSON w skryptach (colorImages / imageGalleryData / ImageBlockATF) ---
    for h in (html, html_u):
        for key in ("colorImages", "imageGalleryData", "ImageBlockATF"):
            m = re.search(rf'"{key}"\s*:\s*(\{{.*?\}}|\[.*?\])', h, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))
                    # próbujemy różnych struktur
                    candidates = []
                    if isinstance(data, dict):
                        for v in data.values():
                            if isinstance(v, list):
                                candidates.extend(v)
                            elif isinstance(v, dict):
                                candidates.extend(v.get("initial", []))
                    elif isinstance(data, list):
                        candidates = data
                    for item in candidates:
                        for k in ("hiRes", "large", "mainUrl", "thumbUrl", "displayImage"):
                            u = item.get(k) if isinstance(item, dict) else None
                            if u:
                                add(str(u))
                except:
                    pass

    # usuń miniatury Amazona (np. ...._AC_SX342_.jpg)
    urls = [u for u in urls if not re.search(r'\._[^.]+\.', u)]
    # unikalne + max 12
    return list(dict.fromkeys(urls))[:12]
import redis
import json
from hashlib import md5

redis_url = "redis://red-d44mcfkhg0os73fhhepg:6379"   # <- TWÓJ URL z Render
rdb = redis.Redis.from_url(redis_url, decode_responses=True)

def cache_load(key):
    data = rdb.get(key)
    return json.loads(data) if data else None

def cache_save(key, data):
    rdb.set(key, json.dumps(data), ex=60*60*24*7)  # cache na 7 dni

def fetch_amazon(url_or_asin):
    API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"

    url_or_asin = url_or_asin.strip().upper()
    asin = re.sub(r".*?/DP/([A-Z0-9]{8,14}).*", r"\1", url_or_asin)

    cache_key = md5(asin.encode()).hexdigest()
    cached = cache_load(cache_key)
    if cached:
        return cached

    amazon_url = f"https://www.amazon.co.uk/dp/{asin}"

    # ✅ ZAWSZE render=true żeby mieć zdjęcia
    url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}&render=true"
    r = requests.get(url, timeout=25)

    raw = r.text
    html = html_unescape.unescape(raw)  # << ważne
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)
    print("IMAGES FOUND:", images)  # ✅ debug

    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ", strip=True)
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    result = {"title": title, "images": images, "bullets": bullets, "meta": meta}
    cache_save(cache_key, result)
    return result
def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = []

    # Tytuł
    lines.append(title)
    lines.append("")

    # Brand / Colour
    if brand or colour:
        if brand:
            lines.append(f"Brand: {brand}")
        if colour:
            lines.append(f"Colour: {colour}")
        lines.append("")

    # Key Features
    if bullets:
        lines.append("✨ Key Features")
        lines.append("")
        for b in bullets[:10]:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
            lines.append("")  # ✅ PRZERWA między punktami

    # Stopka
    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")
    lines.append("")  # ✅ dodatkowa przerwa na koniec

    return "\n".join(lines)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    data = fetch_amazon(url)
    print("IMAGES FOUND:", data["images"]) 

    listing_text = generate_listing_text(data["title"], data["meta"], data["bullets"])

    return render_template(
        "result.html",
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        listing_text=listing_text  # ✅ <- teraz jest przekazywany
    )

@app.route("/proxy")
def proxy():
    u = unquote(request.args.get("u", ""))
    r = requests.get(u, timeout=20)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
def download_zip():
    selected = request.form.getlist("selected")
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        for i, u in enumerate(selected):
            z.writestr(f"image_{i+1}.jpg", requests.get(u).content)
    mem.seek(0)
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name="images.zip")

if __name__ == "__main__":
    app.run(debug=True, port=8000)

