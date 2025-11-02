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

def generate_listing_text(title, meta, bullets):
    # usuń kwadratowe nawiasy i śmieci z amazona:
    clean_title = re.sub(r"\[[^\]]+\]", "", title).strip()

    lines = []
    lines.append(truncate_title_80(clean_title))
    lines.append("")

    lines.append(f"Full Title: {clean_title}")
    lines.append("")

    # Meta sekcja
    params = []
    if meta.get("Brand"):
        params.append(f"Brand: {meta['Brand']}")
    if meta.get("Colour"):
        params.append(f"Colour: {meta['Colour']}")
    if meta.get("Outlets"):
        params.append(f"Outlets: {meta['Outlets']}")
    if meta.get("Wireless"):
        params.append("Wireless Charging: Yes")

    if params:
        lines.append("📌 Product Details")
        for p in params:
            lines.append(f"- {p}")
        lines.append("")

    # Sekcja cech
    if bullets:
        lines.append("✨ Key Features")
        for b in bullets[:10]:
            b = re.sub(r"\[[^\]]+\]", "", b).strip()   # usuwa [ ... ]
            lines.append(f"- {b}")
        lines.append("")

    return "\n".join(lines).strip()

def fetch_amazon(url_or_asin):
    # Normalizujemy wejście
    raw = url_or_asin.strip()

    # Jeśli ktoś wklei cały link → wyciągamy ASIN
    match = re.search(r"/dp/([A-Za-z0-9]{10})|/gp/product/([A-Za-z0-9]{10})|([A-Za-z0-9]{10})", raw)
    if match:
        asin = next(g for g in match.groups() if g).upper()
    else:
        asin = raw.upper()

    url = f"https://www.amazon.co.uk/dp/{asin}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-GB,en;q=0.9",
    }

    r = requests.get(url, headers=headers, timeout=20)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # Tytuł
    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)
    bullets, meta = parse_bullets_and_meta(soup)

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
    listing_text = generate_listing_text(data["title"], data["meta"], data["bullets"])
    return render_template("result.html", title80=truncate_title_80(data["title"]), full_title=data["title"], images=data["images"], listing_text=listing_text)

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