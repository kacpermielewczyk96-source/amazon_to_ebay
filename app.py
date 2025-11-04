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
    url_or_asin = url_or_asin.strip().upper()

    # Wyciągamy ASIN
    asin_match = re.search(r"/dp/([A-Z0-9]{8,12})", url_or_asin)
    asin = asin_match.group(1) if asin_match else url_or_asin[-10:]

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        "Accept-Language": "en-GB,en;q=0.9"
    }

    # Tytuł, zdjęcia, brand, kolor
    r = requests.get(
        f"https://www.amazon.co.uk/gp/aod/api/patterns/dp/{asin}",
        headers=headers, timeout=8
    )
    data = r.json()

    title = data.get("title", "").strip()
    brand = data.get("brand", "")
    colour = data.get("color", "")

    images = []
    for img in data.get("images", []):
        full = img.get("hiRes") or img.get("large")
        if full:
            images.append(full)
    images = images[:12]

    # Bullets
    r2 = requests.get(
        f"https://www.amazon.co.uk/afx/async/pep/getJustifiedFeatures?asin={asin}&deviceType=web",
        headers=headers, timeout=8
    )
    bullets_json = r2.json()
    bullets = bullets_json.get("features", [])[:10]

    return {
        "title": title or "No title found",
        "images": images,
        "bullets": bullets,
        "meta": {"Brand": brand, "Colour": colour}
    }

def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = []

    # Tytuł
    lines.append(title)
    lines.append("")  # odstęp

    # Brand / Colour
    if brand or colour:
        if brand:
            lines.append(f"Brand: {brand}")
        if colour:
            lines.append(f"Colour: {colour}")
        lines.append("")  # odstęp

    # Key Features
    if bullets:
        lines.append("✨ Key Features")
        lines.append("")  # odstęp
        for b in bullets[:10]:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
            lines.append("")  # ✅ PRZERWA po każdej linii

    # Stopka
    lines.append("📦 Fast Dispatch from UK | 🚚 Tracked Delivery Included")

    return "\n".join(lines) + "\n"  # ✅ dodatkowy enter na końcu

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
