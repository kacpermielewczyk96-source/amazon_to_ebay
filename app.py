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

def extract_highres_images(html: str) -> list:
    urls = []
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))
    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.select("img[src*='images/I/']"):
        u = img.get("src", "")
        urls.append(u)

    out = []
    for u in urls:
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in out:
            out.append(u)

    return out[:12]

def fetch_amazon(url_or_asin):
    SCRAPER_API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"

    url_or_asin = url_or_asin.strip()
    if "amazon" not in url_or_asin:
        url = f"https://www.amazon.co.uk/dp/{url_or_asin.upper()}"
    else:
        url = url_or_asin

    # ✅ wymuszamy wersję mobilną (mniej blokad, pełne zdjęcia)
    url = url.replace("www.amazon.", "m.amazon.")

    # ✅ ScraperAPI usuwa blokady Amazon
    r = requests.get(
        "https://api.scraperapi.com",
        params={
            "api_key": SCRAPER_API_KEY,
            "url": url
        },
        timeout=25
    )

    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # ✅ Tytuł
    title_tag = soup.select_one("h1#title, span#productTitle, h1 span")
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    # ✅ Zdjęcia (mobilne są czyste i wysokiej jakości)
    images = []
    for img in soup.select("img[src*='images/I/']"):
        u = img.get("src", "")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(u)
    images = list(dict.fromkeys(images))[:12]

    # ✅ Bullets
    bullets = []
    for li in soup.select("#feature-bullets li, li span.a-list-item"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    # ✅ Brand & Colour
    meta = {}
    for li in soup.select("#detailBullets_feature_div li, tr"):
        text = " ".join(li.get_text(" ", strip=True).split())
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "") or meta.get("Color", "")

    return {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": {"Brand": brand, "Colour": colour}
    }

def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = []

    # Tytuł bez emotek, czysty
    lines.append(title)
    lines.append("")  # odstęp

    # Parametry jeśli są
    if brand:
        lines.append(f"Brand: {brand}")
    if colour:
        lines.append(f"Colour: {colour}")
    lines.append("")

    # Cechy (max 10)
    if bullets:
        lines.append("✨ Key Features:")
        lines.append("")
        for b in bullets[:10]:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
            lines.append("")

    # Końcowa linia — **TAK, ta co chcesz**
    lines.append("📦 Fast Dispatch from UK • 🚚 Tracked Delivery Included")

    return "\n".join(lines)

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