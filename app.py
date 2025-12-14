from flask import Flask, render_template, request, send_file, Response, send_from_directory
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

# ----------------- BRIGHT DATA -----------------
BRIGHTDATA_API_KEY = "1bbcee91427624e79bfbc87c146ae2dbf0ddce6f55f0ed8ef2f448b49ca3e93d"
BRIGHTDATA_ZONE = "web_unlocker1"
BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"

# ----------------- CACHE -----------------
CACHE_DIR = "cache"
IMAGES_DIR = "static/images"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

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
    urls = []

    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u not in urls:
            urls.append(u)

    dyn = re.search(r'data-a-dynamic-image="({[^"]+})"', html)
    if dyn:
        try:
            obj = json.loads(dyn.group(1).replace("&quot;", '"'))
            urls.extend(obj.keys())
        except:
            pass

    clean, seen = [], set()
    for u in urls:
        u = u.split("?", 1)[0]
        u = re.sub(r'\._[^.]+\.', '.', u)
        if u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in seen:
            seen.add(u)
            clean.append(u)

    return clean[:12]


def download_and_save_image(url, asin, index):
    """Pobiera zdjęcie i zapisuje lokalnie, zwraca lokalną ścieżkę"""
    # Tworzę unikalną nazwę pliku
    img_hash = md5(url.encode()).hexdigest()[:12]
    ext = url.split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png", "webp"]:
        ext = "jpg"
    
    filename = f"{asin}_{index}_{img_hash}.{ext}"
    filepath = os.path.join(IMAGES_DIR, filename)
    
    # Jeśli już istnieje, nie pobieram ponownie
    if os.path.exists(filepath):
        return f"/static/images/{filename}"
    
    # Pobieram zdjęcie
    try:
        r = requests.get(url, timeout=25)
        r.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(r.content)
        return f"/static/images/{filename}"
    except:
        return None

# ----------------- core scraper -----------------

def fetch_amazon(url_or_asin: str):
    url_or_asin = (url_or_asin or "").strip()
    if not url_or_asin:
        return {"title": "No title found", "images": [], "bullets": [], "meta": {}}

    if "amazon" not in url_or_asin:
        asin = url_or_asin.upper()
        amazon_url = f"https://www.amazon.co.uk/dp/{asin}"
    else:
        amazon_url = url_or_asin.split("?", 1)[0]
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", amazon_url, flags=re.I)

    cache_key = md5(asin.encode()).hexdigest()
    cached = cache_load(cache_key)
    if cached:
        return cached

    headers = {
        "Authorization": f"Bearer {BRIGHTDATA_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "zone": BRIGHTDATA_ZONE,
        "url": amazon_url,
        "format": "raw"
    }

    r = requests.post(
        BRIGHTDATA_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=60
    )
    r.raise_for_status()
    html = r.text

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    # Pobieram URLs zdjęć
    image_urls = extract_highres_images(html)
    
    # Pobieram i zapisuję lokalnie
    local_images = []
    for idx, img_url in enumerate(image_urls):
        local_path = download_and_save_image(img_url, asin, idx)
        if local_path:
            local_images.append(local_path)

    bullets = [
        li.get_text(" ", strip=True)
        for li in soup.select("#feature-bullets li")
        if "Click to" not in li.get_text()
    ][:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        txt = li.get_text(" ", strip=True)
        if ":" in txt:
            k, v = txt.split(":", 1)
            meta[k.strip()] = v.strip()

    result = {
        "title": title,
        "images": local_images,  # Teraz to lokalne ścieżki!
        "bullets": bullets,
        "meta": meta
    }

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
    data = fetch_amazon(request.form.get("url", ""))
    # Zapisuję images do sesji/cache dla API endpoint
    asin = request.form.get("url", "").strip()
    if "amazon" not in asin:
        asin = asin.upper()
    else:
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", asin, flags=re.I)
    
    # Zapisuję w cache z kluczem dla API
    cache_save(f"api_{asin}", {"images": data["images"]})
    
    return render_template(
        "result.html",
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        asin=asin,
        listing_text=generate_listing_text(
            data["title"], data["meta"], data["bullets"]
        )
    )

@app.route("/api/images/<asin>")
def api_images(asin):
    """API endpoint dla iOS Shortcut - zwraca pełne URLe zdjęć"""
    cached = cache_load(f"api_{asin}")
    if not cached:
        return {"error": "ASIN not found"}, 404
    
    # Tworzymy pełne URLe
    base_url = request.host_url.rstrip('/')
    full_urls = [f"{base_url}{img}" for img in cached["images"]]
    
    return {"images": full_urls}

if __name__ == "__main__":
    app.run(debug=True, port=8000)
