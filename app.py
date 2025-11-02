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
        urls.append(u)

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        urls.append(u)

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.select("img[src*='images/I/']"):
        u = img.get("src", "")
        urls.append(u)

    out = []
    for u in urls:
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in out:
            out.append(u)

    return out[:12]

def fetch_page(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; SM-G973F) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.google.com/"
    }
    return requests.get(url, headers=headers, timeout=20).text

def fetch_amazon(url_or_asin):
    url_or_asin = url_or_asin.strip()

    # używamy stabilnej wersji desktop – pełne zdjęcia
    if "amazon" not in url_or_asin:
        url = f"https://www.amazon.co.uk/dp/{url_or_asin.upper()}"
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

    # --- TITLE (pewny) ---
    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    # --- IMAGES: najlepsze możliwe jakościowo ---
    images = []
    # JSON hiRes / large
    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(u)

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(u)

    # fallback – obrazy z viewer
    for img in soup.select("img[src*='images/I/']"):
        u = img.get("src", "")
        if u.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(u)

    # unikaty + limit do 12
    images = list(dict.fromkeys(images))[:12]

    # --- BULLETS ---
    bullets = []
    for li in soup.select("#feature-bullets li"):
        t = li.get_text(" ", strip=True)
        if t and "Click to" not in t and "This fits your" not in t:
            bullets.append(t)
    bullets = bullets[:10]

    # --- META PARAMS (Brand / Colour) ---
    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ", strip=True)
        if ":" in text:
            k, v = text.split(":", 1)
            meta[k.strip()] = v.strip()

    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    return {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": {"Brand": brand, "Colour": colour}
    }

def generate_listing_html(title, meta, bullets):
    # Czyścimy tytuł z nawiasów i śmieci
    clean_title = re.sub(r"\[[^\]]+\]", "", title).strip()

    html = []
    html.append(f"<h2 style='margin-bottom:8px;'>{clean_title}</h2><br>")

    # ✅ Product Details tylko jeśli są realne dane
    brand = meta.get("Brand") or meta.get("Brand Name") or ""
    colour = meta.get("Colour") or meta.get("Color") or ""

    if brand or colour:
        html.append("<h3>📌 Product Details</h3><ul>")
        if brand:
            html.append(f"<li><b>Brand:</b> {brand}</li>")
        if colour:
            html.append(f"<li><b>Colour:</b> {colour}</li>")
        html.append("</ul><br><br>")

    # ✅ Key Features z odstępami ładnymi pod eBay
    if bullets:
        html.append("<h3>✨ Key Features</h3><br><ul>")
        for b in bullets:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()
            html.append(f"<li>{b}</li>")
        html.append("</ul><br><br>")

    return "\n".join(html)

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