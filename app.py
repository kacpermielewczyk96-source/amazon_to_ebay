from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import json
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
from hashlib import md5

app = Flask(__name__)

# ----------------- CACHE -----------------
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def cache_load(key):
    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return None
    return None

def cache_save(key, data):
    path = os.path.join(CACHE_DIR, key + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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

    # 3) Fallback: data-a-dynamic-image
    dyn = re.search(r'data-a-dynamic-image="({[^"]+})"', html)
    if dyn:
        try:
            obj = json.loads(dyn.group(1).replace("&quot;", '"'))
            urls.extend(list(obj.keys()))
        except:
            pass

    # Normalizacja + filtrowanie
    clean = []
    seen = set()
    for u in urls:
        if not u:
            continue
        u = u.split("?", 1)[0]  # usuń ?v=3 itp
        if re.search(r'\._[^.]+\.', u):  # usuń miniatury
            u = re.sub(r'\._[^.]+\.', '.', u)
        if u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in seen:
            seen.add(u)
            clean.append(u)

    return clean[:12]


# ----------------- core scraper -----------------

def fetch_amazon(url_or_asin: str):
    API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"  # ← Twój klucz ScraperAPI

    url_or_asin = url_or_asin.strip()
    if "amazon" not in url_or_asin:
        asin = url_or_asin.upper()
        amazon_url = f"https://www.amazon.co.uk/dp/{asin}"
    else:
        amazon_url = url_or_asin.split("?", 1)[0]
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", amazon_url, flags=re.IGNORECASE)

    cache_key = md5(asin.encode()).hexdigest()

    cached = cache_load(cache_key)
    if cached:
        return cached

    def fetch(render=False):
        url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}"
        if render:
            url += "&render=true"
        r = requests.get(url, timeout=25)
        r.raise_for_status()
        return r.text

    html = fetch(render=False)
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    if not title_tag:  # fallback
        html = fetch(render=True)
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("span", {"id": "productTitle"})

    title = title_tag.get_text(strip=True) if title_tag else "No title found"
    images = extract_highres_images(html)

    bullets = [
        li.get_text(" ", strip=True)
        for li in soup.select("#feature-bullets li")
        if "Click to" not in li.get_text() and li.get_text(strip=True)
    ][:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        txt = li.get_text(" ", strip=True)
        if ":" in txt:
            k, v = txt.split(":", 1)
            meta[k.strip()] = v.strip()

    result = {"title": title, "images": images, "bullets": bullets, "meta": meta}
    cache_save(cache_key, result)
    return result


def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = [title, ""]
    if brand: lines.append(f"Brand: {brand}")
    if colour: lines.append(f"Colour: {colour}")
    lines.append("")

    if bullets:
        lines.append("✨ Key Features\n")
        for b in bullets:
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
    data = fetch_amazon(request.form.get("url", "").strip())
    return render_template(
        "result.html",
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        listing_text=generate_listing_text(data["title"], data["meta"], data["bullets"])
    )

@app.route("/proxy")
def proxy():
    r = requests.get(unquote(request.args.get("u", "")), timeout=25)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
def download_zip():
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        for i, u in enumerate(request.form.getlist("selected")):
            z.writestr(f"image_{i+1}.jpg", requests.get(u, timeout=25).content)
    mem.seek(0)
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name="images.zip")

if __name__ == "__main__":
    app.run(debug=True, port=8000)
