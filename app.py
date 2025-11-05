from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
import json, os
from hashlib import md5

app = Flask(__name__)

# ======================
#   CACHE (7 dni)
# ======================
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def cache_load(key):
    path = os.path.join(CACHE_DIR, key + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def cache_save(key, data):
    path = os.path.join(CACHE_DIR, key + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ======================
#    TITLE SHORTEN
# ======================
def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 80:
        return s
    cut = s[:80]
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut


# ======================
#   IMAGE EXTRACTOR
# ======================
def extract_highres_images(html: str):
    urls = []

    # hiRes
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))

    # large
    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u not in urls:
            urls.append(u)

    # Fallback: dynamic images (działa zawsze)
    dyn = re.search(r'data-a-dynamic-image="({[^"]+})"', html)
    if dyn:
        try:
            block = dyn.group(1).replace("&quot;", '"')
            obj = json.loads(block)
            for img_url in obj.keys():
                urls.append(img_url)
        except:
            pass

    # Usuń miniatury Amazona
    urls = [u for u in urls if not re.search(r'\._[^.]+\.', u)]
    urls = [u for u in urls if u.endswith((".jpg", ".jpeg", ".png", ".webp"))]

    # max 12
    return list(dict.fromkeys(urls))[:12]


# ======================
#  AMAZON FETCH (ScraperAPI)
# ======================
def fetch_amazon(url_or_asin):
    API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"   # ← Twój klucz ScraperAPI

    url_or_asin = url_or_asin.strip()
    asin = re.sub(r".*?/DP/([A-Z0-9]{8,14}).*", r"\1", url_or_asin.upper())

    # CACHE KEY
    cache_key = md5(asin.encode()).hexdigest()
    cached = cache_load(cache_key)
    if cached:
        return cached

    amazon_url = f"https://www.amazon.co.uk/dp/{asin}"

    # 1) fast request
    r = requests.get(
        f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}",
        timeout=25
    )
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("span", {"id": "productTitle"})

    # 2) fallback if needed
    if not title_tag:
        r = requests.get(
            f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}&render=true",
            timeout=25
        )
        html = r.text
        soup = BeautifulSoup(html, "html.parser")

    # TITLE
    title = soup.find("span", {"id": "productTitle"})
    title = title.get_text(strip=True) if title else "No title found"

    # IMAGES
    images = extract_highres_images(html)

    # BULLETS
    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    # META
    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        if ":" in li.get_text():
            k, v = li.get_text(" ", strip=True).split(":", 1)
            meta[k.strip()] = v.strip()

    result = {"title": title, "images": images, "bullets": bullets, "meta": meta}
    cache_save(cache_key, result)
    return result


# ======================
#  DESCRIPTION BUILDER
# ======================
def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = [title, ""]
    if brand: lines.append(f"Brand: {brand}")
    if colour: lines.append(f"Colour: {colour}")
    lines.append("")

    lines.append("✨ Key Features\n")
    for b in bullets:
        lines.append(f"⚫️ {b}")
    lines.append("")
    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")
    return "\n".join(lines)


# ======================
#   ROUTES
# ======================
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
