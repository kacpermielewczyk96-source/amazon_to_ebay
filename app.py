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

def fetch_amazon(url_or_asin):
    API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"

    url_or_asin = url_or_asin.strip().upper()

    if "amazon" not in url_or_asin:
        asin = url_or_asin
    else:
        asin = re.search(r"/dp/([A-Z0-9]{8,12})", url_or_asin)
        asin = asin.group(1) if asin else url_or_asin[-10:]

    # ✅ Szybkie API (JSON)
    api_url = f"https://www.amazon.co.uk/gp/aod/api/patterns/dp/{asin}"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        "Accept-Language": "en-GB,en;q=0.9"
    }

    try:
        r = requests.get(api_url, headers=headers, timeout=5)
        data = r.json()  # <-- jeśli tu poleci wyjątek → lecimy w fallback
        title = data.get("title", "").strip()
        bullets = [b.strip() for b in data.get("feature_bullets", [])][:10]

        meta = {
            "Brand": data.get("brand", ""),
            "Colour": data.get("color", "")
        }

        images = []
        for img in data.get("images", []):
            full = img.get("hiRes") or img.get("large")
            if full and full.startswith("http"):
                images.append(full)
        images = images[:12]

        # ✅ Jeśli tytuł istnieje → szybka metoda działa → zwracamy
        if title:
            return {
                "title": title,
                "images": images,
                "bullets": bullets,
                "meta": meta
            }

    except:
        pass  # przechodzimy do fallback

    # 🔥 FALLBACK → stary scraper przez ScraperAPI
    amazon_url = f"https://www.amazon.co.uk/dp/{asin}"
    url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}&keep_headers=true"

    r = requests.get(url, headers=headers, timeout=20)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)
    images = list(dict.fromkeys(images))[:12]

    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

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

    # Tytuł
    lines.append(title)
    lines.append("")

    # Podstawowe dane
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
        lines.append("")

    # Stopka
    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")

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
