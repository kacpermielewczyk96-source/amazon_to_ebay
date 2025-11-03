from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile

app = Flask(__name__)

def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 80:
        return s
    cut = s[:80]
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut

def extract_highres_images(data):
    images = []
    for img in data.get("images", []):
        full = img.get("hiRes") or img.get("large")
        if full and full.startswith("https"):
            images.append(full)
    return images[:12]

def fetch_amazon(url_or_asin):
    url_or_asin = url_or_asin.strip().upper()

    # Wyciąganie ASIN
    if "AMAZON" not in url_or_asin:
        asin = url_or_asin
    else:
        m = re.search(r"/dp/([A-Z0-9]{8,12})", url_or_asin)
        asin = m.group(1) if m else url_or_asin[-10:]

    # API Amazon — szybkie i bez blokad
    api_url = f"https://www.amazon.co.uk/gp/aod/api/patterns/dp/{asin}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        "Accept-Language": "en-GB,en;q=0.9"
    }

    r = requests.get(api_url, headers=headers, timeout=8)
    data = r.json()

    title = data.get("title", "No title found").strip()
    bullets = [b.strip() for b in data.get("feature_bullets", [])][:10]

    meta = {
        "Brand": data.get("brand", ""),
        "Colour": data.get("color", "")
    }

    images = extract_highres_images(data)

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
    lines.append("")  # przerwa

    # Meta
    if brand:
        lines.append(f"Brand: {brand}")
    if colour:
        lines.append(f"Colour: {colour}")
    lines.append("")  # przerwa

    # Cechy
    if bullets:
        lines.append("✨ Key Features")
        lines.append("")
        for b in bullets:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
            lines.append("")  # przerwa po każdym punkcie

    # Stopka
    lines.append("📦 Fast Dispatch from UK | 🚚 Tracked Delivery Included")

    return "\n".join(lines) + "\n"

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