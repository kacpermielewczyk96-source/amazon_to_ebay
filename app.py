from flask import Flask, render_template, request, send_file, Response
from io import BytesIO
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
import html as html_unescape  # DODAJ TEN IMPORT

app = Flask(__name__)

API_KEY = "9fe7f834a7ef9abfcf0d45d2b86f3a5f"

def truncate_title_80(s: str) -> str:
    s = (s or "").strip()
    return s if len(s) <= 80 else s[:s.rfind(" ", 0, 80)]

import html as html_unescape  # DODAJ TEN IMPORT
def fetch_amazon(url_or_asin):
    url_or_asin = url_or_asin.strip().upper()

    if "AMAZON" in url_or_asin:
        asin = re.sub(r".*?/DP/([A-Z0-9]{8,14}).*", r"\1", url_or_asin)
    else:
        asin = url_or_asin

    amazon_url = f"https://www.amazon.co.uk/dp/{asin}"

    # **TU JEST MAGIA → zawsze render=true**
    url = f"https://api.scraperapi.com?api_key={API_KEY}&url={amazon_url}&render=true"

    r = requests.get(url, timeout=30)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one("#productTitle, span#productTitle, h1 span")
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)

    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ", strip=True)
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    return {"title": title, "images": images, "bullets": bullets[:10], "meta": meta}

def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")
    lines = [title, ""]

    if brand or colour:
        if brand: lines.append(f"Brand: {brand}")
        if colour: lines.append(f"Colour: {colour}")
        lines.append("")

    if bullets:
        lines.append("✨ Key Features\n")
        for b in bullets:
            lines.append(f"⚫️ {b}")
            lines.append("")

    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included\n")
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
