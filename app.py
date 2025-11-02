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

def extract_images(html: str) -> list:
    urls = []
    # hi-res json
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))
    # large fallback
    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))
    # image viewer fallback
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
    url_or_asin = url_or_asin.strip()
    if "amazon" not in url_or_asin:
        url = f"https://www.amazon.co.uk/dp/{url_or_asin}"
    else:
        url = url_or_asin

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"),
        "Accept-Language": "en-GB,en;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=20)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"
    images = extract_images(html)

    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        txt = li.get_text(" ", strip=True)
        if ":" in txt:
            k, v = txt.split(":", 1)
            meta[k.strip()] = v.strip()

    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    return {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": {"Brand": brand, "Colour": colour}
    }

def generate_listing_text(title, meta, bullets):
    lines = []

    lines.append(title)
    lines.append("")

    if meta.get("Brand"):
        lines.append(f"Brand: {meta['Brand']}")
    if meta.get("Colour"):
        lines.append(f"Colour: {meta['Colour']}")
    lines.append("")

    if bullets:
        lines.append("✨ Key Features")
        lines.append("")
        for b in bullets:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            lines.append(f"⚫️ {b}")
        lines.append("")

    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")
    return "\n".join(lines)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    data = fetch_amazon(url)

    return render_template(
        "result.html",
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        listing_text=generate_listing_text(data["title"], data["meta"], data["bullets"])
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