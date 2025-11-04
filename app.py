from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
from datetime import datetime

app = Flask(__name__)

def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 80:
        return s
    cut = s[:80]
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut

def extract_highres_images(html: str):
    urls = []

    # ✅ Pobieramy tylko zdjęcia z głównej galerii (hiRes / large)
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            urls.append(u)

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in urls:
            urls.append(u)

    # ✅ ŻADNYCH zdjęć z recenzji / miniaturek
    # → więc nie dodajemy nic z soup.select("img...")

    # limit maksymalnie 12
    return urls[:12]

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

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.google.com/"
    }

    # 1️⃣ iPhone UA (szybko)
    r = requests.get(amazon_url, headers=HEADERS, timeout=10)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("span", {"id": "productTitle"})

    # 2️⃣ Android UA (druga szybka próba)
    if not title_tag:
        HEADERS2 = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G996B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Mobile Safari/537.36",
            "Accept-Language": "en-GB,en;q=0.9"
        }
        r = requests.get(amazon_url, headers=HEADERS2, timeout=10)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("span", {"id": "productTitle"})

    # 3️⃣ Jak nadal blokuje → dopiero wtedy wolny render
    if not title_tag:
        url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}&render=true"
        r = requests.get(url, timeout=25)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")

    title = soup.find("span", {"id": "productTitle"})
    title = title.get_text(strip=True) if title else "No title found"

    images = extract_highres_images(html)
    images = list(dict.fromkeys(images))[:12]

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

    result = {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": meta
    }

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

