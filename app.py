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
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            urls.append(u)

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            urls.append(u)

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.select("img[src*='images/I/']"):
        u = img.get("src", "")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            urls.append(u)

    out = []
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:12]

def parse_bullets_and_meta(soup: BeautifulSoup):
    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)
    bullets = [b for b in bullets if b][:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        text = " ".join(li.get_text(" ", strip=True).split())
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    return bullets, {"Brand": brand, "Colour": colour}

def generate_listing_html(title, meta, bullets):
    clean_title = re.sub(r"\[[^\]]+\]", "", title).strip()

    html = []
    html.append(f"<h2 style='margin-bottom:6px;'>{clean_title}</h2>")

    # Product Details Section
    if meta.get("Brand") or meta.get("Colour"):
        html.append("<h3>📌 Product Details</h3><ul>")
        if meta.get("Brand"):
            html.append(f"<li><b>Brand:</b> {meta['Brand']}</li>")
        if meta.get("Colour"):
            html.append(f"<li><b>Colour:</b> {meta['Colour']}</li>")
        html.append("</ul>")

    # Key Features Section
    if bullets:
        html.append("<h3>✨ Key Features</h3><ul>")
        for b in bullets:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            html.append(f"<li>{b}</li>")
        html.append("</ul>")

    return "\n".join(html)

def fetch_amazon(url_or_asin):
    url_or_asin = url_or_asin.strip()

    # MOBILE Amazon = mniej blokad
    if "amazon" not in url_or_asin:
        url = f"https://www.amazon.co.uk/dp/{url_or_asin.upper()}?th=1&psc=1"
    else:
        url = url_or_asin.replace("www.amazon.", "m.amazon.") + "?th=1&psc=1"

    headers = {
        "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_2 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                       "Version/16.2 Mobile/15E148 Safari/604.1"),
        "Accept-Language": "en-GB,en;q=0.9",
    }

    r = requests.get(url, headers=headers, timeout=20)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # ✅ Title
    title_tag = soup.select_one("h1#title, span#productTitle, h1 span")
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    # ✅ Images (mobile image viewer)
    images = []
    for img in soup.select("img[src*='images/I/']"):
        src = img.get("src")
        if src and src.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(src)
    images = list(dict.fromkeys(images))[:12]

    # ✅ Bullets
    bullets = [li.get_text(" ", strip=True) for li in soup.select("li span.a-list-item")]
    bullets = bullets[:10]

    # ✅ Meta
    meta = {}
    for row in soup.select("tr"):
        cells = row.get_text(" ", strip=True).split(":")
        if len(cells) == 2:
            key, val = cells
            meta[key.strip()] = val.strip()

    return {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": meta
    }
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    url = request.form.get("url", "").strip()
    data = fetch_amazon(url)
    listing_html = generate_listing_html(data["title"], data["meta"], data["bullets"])
    return render_template("result.html",
                           title80=truncate_title_80(data["title"]),
                           full_title=data["title"],
                           images=data["images"],
                           listing_html=listing_html)

@app.route("/download", methods=["POST"])
def download_selected():
    selected = request.form.getlist("selected")
    return render_template("download.html", selected=selected)

@app.route("/proxy")
def proxy():
    u = unquote(request.args.get("u", ""))
    if not u.startswith("http"):
        return Response("Bad URL", status=400)
    r = requests.get(u, timeout=20, stream=True)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
def download_zip():
    selected = request.form.getlist("selected")
    if not selected:
        return "No images", 400

    mem = BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        for i, u in enumerate(selected):
            r = requests.get(u, timeout=15)
            z.writestr(f"image_{i+1}.jpg", r.content)

    mem.seek(0)
    name = f"images_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=name)

if __name__ == "__main__":
    app.run(debug=True, port=8000)