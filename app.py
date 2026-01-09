from flask import Flask, render_template, request, send_file, Response, jsonify
from io import BytesIO
import re
import json
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
import zipfile
from hashlib import md5
import sqlite3
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ----------------- BRIGHT DATA -----------------
BRIGHTDATA_API_KEY = "1bbcee91427624e79bfbc87c146ae2dbf0ddce6f55f0ed8ef2f448b49ca3e93d"
BRIGHTDATA_ZONE = "web_unlocker1"
BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"

# ----------------- CACHE -----------------
CACHE_DIR = "cache"
UPLOADS_DIR = "uploads"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ----------------- DATABASE -----------------
DB_PATH = "history.db"
MAX_HISTORY = 50

def init_db():
    """Inicjalizacja bazy danych"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Tabela główna z historią
    c.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT NOT NULL UNIQUE,
            title TEXT,
            image_url TEXT,
            sku TEXT,
            notes TEXT,
            custom_description TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela z dodatkowymi zdjęciami
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT NOT NULL,
            image_path TEXT NOT NULL,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asin) REFERENCES search_history(asin)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def save_to_history_db(asin, title=None, image_url=None, sku=None):
    """Zapisz lub zaktualizuj wpis w historii"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        # Sprawdź czy istnieje
        c.execute('SELECT id FROM search_history WHERE asin = ?', (asin,))
        existing = c.fetchone()
        
        if existing:
            # Aktualizuj istniejący (ale nie nadpisuj SKU jeśli jest None)
            if sku is not None:
                c.execute('''
                    UPDATE search_history 
                    SET title = ?, image_url = ?, sku = ?, timestamp = ?
                    WHERE asin = ?
                ''', (title, image_url, sku, datetime.now(), asin))
            else:
                c.execute('''
                    UPDATE search_history 
                    SET title = ?, image_url = ?, timestamp = ?
                    WHERE asin = ?
                ''', (title, image_url, datetime.now(), asin))
        else:
            # Dodaj nowy
            c.execute('''
                INSERT INTO search_history (asin, title, image_url, sku, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (asin, title, image_url, sku, datetime.now()))
        
        # Usuń najstarsze jeśli > MAX_HISTORY
        c.execute('''
            DELETE FROM search_history
            WHERE id NOT IN (
                SELECT id FROM search_history
                ORDER BY timestamp DESC
                LIMIT ?
            )
        ''', (MAX_HISTORY,))
        
        conn.commit()
    finally:
        conn.close()

def add_product_image(asin, image_path):
    """Dodaj dodatkowe zdjęcie do produktu"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO product_images (asin, image_path, uploaded_at)
        VALUES (?, ?, ?)
    ''', (asin, image_path, datetime.now()))
    conn.commit()
    conn.close()

def get_product_images(asin):
    """Pobierz dodatkowe zdjęcia produktu"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT image_path FROM product_images WHERE asin = ? ORDER BY uploaded_at', (asin,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_product_details(asin):
    """Pobierz szczegóły produktu z bazy"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT asin, title, image_url, sku, notes, custom_description FROM search_history WHERE asin = ?', (asin,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            'asin': row[0],
            'title': row[1],
            'image_url': row[2],
            'sku': row[3] or '',
            'notes': row[4] or '',
            'custom_description': row[5] or ''
        }
    return None

def get_history_from_db(limit=50):
    """Pobierz ostatnie wyszukiwania"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT asin, title, image_url, sku, timestamp
        FROM search_history
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            'asin': row[0],
            'title': row[1],
            'image': row[2],
            'sku': row[3] or '',
            'timestamp': row[4]
        }
        for row in rows
    ]

def cache_load(key):
    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return None
    return None

def cache_save(key, data):
    path = os.path.join(CACHE_DIR, key + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ----------------- helpers -----------------

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

    for m in re.finditer(r'"hiRes"\s*:\s*"([^"]+)"', html):
        urls.append(m.group(1).replace("\\u0026", "&"))

    for m in re.finditer(r'"large"\s*:\s*"([^"]+)"', html):
        u = m.group(1).replace("\\u0026", "&")
        if u not in urls:
            urls.append(u)

    dyn = re.search(r'data-a-dynamic-image="({[^"]+})"', html)
    if dyn:
        try:
            obj = json.loads(dyn.group(1).replace("&quot;", '"'))
            urls.extend(obj.keys())
        except:
            pass

    clean, seen = [], set()
    for u in urls:
        u = u.split("?", 1)[0]
        u = re.sub(r'\._[^.]+\.', '.', u)
        if u.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) and u not in seen:
            seen.add(u)
            clean.append(u)

    return clean[:12]

# ----------------- core scraper -----------------

def fetch_amazon(url_or_asin: str):
    url_or_asin = (url_or_asin or "").strip()
    if not url_or_asin:
        return {"title": "No title found", "images": [], "bullets": [], "meta": {}}

    if "amazon" not in url_or_asin:
        asin = url_or_asin.upper()
        amazon_url = f"https://www.amazon.co.uk/dp/{asin}"
    else:
        amazon_url = url_or_asin.split("?", 1)[0]
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", amazon_url, flags=re.I)

    cache_key = md5(asin.encode()).hexdigest()
    cached = cache_load(cache_key)
    if cached:
        return cached

    headers = {
        "Authorization": f"Bearer {BRIGHTDATA_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "zone": BRIGHTDATA_ZONE,
        "url": amazon_url,
        "format": "raw"
    }

    r = requests.post(
        BRIGHTDATA_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=60
    )
    r.raise_for_status()
    html = r.text

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)

    bullets = [
        li.get_text(" ", strip=True)
        for li in soup.select("#feature-bullets li")
        if "Click to" not in li.get_text()
    ][:10]

    meta = {}
    for li in soup.select("#detailBullets_feature_div li"):
        txt = li.get_text(" ", strip=True)
        if ":" in txt:
            k, v = txt.split(":", 1)
            meta[k.strip()] = v.strip()

    result = {
        "title": title,
        "images": images,
        "bullets": bullets,
        "meta": meta
    }

    cache_save(cache_key, result)
    return result

def generate_listing_text(title, meta, bullets):
    brand = meta.get("Brand", "")
    colour = meta.get("Colour", "")

    lines = [title, ""]
    if brand: lines.append(f"Brand: {brand}")
    if colour: lines.append(f"Colour: {colour}")
    lines.append("")

    if bullets:
        lines.append("✨ Key Features\n")
        for b in bullets:
            lines.append(f"⚫️ {b}")
            lines.append("")

    lines.append("📦 Fast Dispatch from UK   |   🚚 Tracked Delivery Included")
    return "\n".join(lines)

# ----------------- routes -----------------

@app.route("/health")
def health():
    return "ok", 200

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scrape", methods=["POST"])
def scrape():
    url_input = request.form.get("url", "")
    data = fetch_amazon(url_input)
    
    if "amazon" not in url_input:
        asin = url_input.upper()
    else:
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", url_input, flags=re.I)
    
    first_image = data["images"][0] if data["images"] else None
    save_to_history_db(asin, truncate_title_80(data["title"]), first_image)
    
    # Pobierz istniejące dane (SKU, notes, dodatkowe zdjęcia, custom opis)
    existing = get_product_details(asin)
    extra_images = get_product_images(asin)
    
    # Użyj custom description jeśli istnieje, inaczej wygeneruj nowy
    listing_text = existing['custom_description'] if existing and existing['custom_description'] else generate_listing_text(data["title"], data["meta"], data["bullets"])
    
    return render_template(
        "result.html",
        asin=asin,
        title80=truncate_title_80(data["title"]),
        full_title=data["title"],
        images=data["images"],
        extra_images=extra_images,
        sku=existing['sku'] if existing else '',
        notes=existing['notes'] if existing else '',
        listing_text=listing_text
    )

@app.route("/save-product", methods=["POST"])
def save_product():
    """Zapisz SKU, notes i dodatkowe zdjęcia - ZWRACA JSON z nazwami plików"""
    try:
        asin = request.form.get("asin")
        sku = request.form.get("sku", "").strip()
        notes = request.form.get("notes", "").strip()
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'}), 400
        
        # Aktualizuj SKU i notes
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE search_history SET sku = ?, notes = ?, timestamp = ? WHERE asin = ?', 
                  (sku, notes, datetime.now(), asin))
        conn.commit()
        conn.close()
        
        # Zapisz nowe zdjęcia
        saved_count = 0
        uploaded_filenames = []
        if 'images' in request.files:
            files = request.files.getlist('images')
            for file in files:
                if file and file.filename:
                    # Bezpieczna nazwa pliku
                    original_filename = secure_filename(file.filename)
                    timestamp = str(int(datetime.now().timestamp()))
                    filename = f"{asin}_{timestamp}_{original_filename}"
                    filepath = os.path.join(UPLOADS_DIR, filename)
                    
                    # Zapisz plik
                    file.save(filepath)
                    
                    # Dodaj do bazy
                    add_product_image(asin, filename)
                    uploaded_filenames.append(filename)
                    saved_count += 1
        
        # ZWRÓĆ JSON z nazwami wgranych plików
        return jsonify({
            'success': True, 
            'message': 'Zapisano!',
            'images_saved': saved_count,
            'uploaded_images': uploaded_filenames
        }), 200
    
    except Exception as e:
        print(f"Error in save_product: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/delete-image", methods=["POST"])
def delete_image():
    """Usuń dodatkowe zdjęcie"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        filename = data.get('filename')
        
        # Usuń z bazy
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM product_images WHERE asin = ? AND image_path = ?', (asin, filename))
        conn.commit()
        conn.close()
        
        # Usuń plik
        filepath = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({'success': True}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/save-description", methods=["POST"])
def save_description():
    """Zapisz edytowany opis"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        description = data.get('description')
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE search_history SET custom_description = ? WHERE asin = ?', (description, asin))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    """Serwuj wgrane zdjęcia"""
    filepath = os.path.join(UPLOADS_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/jpeg')
    return "Not found", 404

@app.route("/api/history")
def api_history():
    """API endpoint do pobierania historii"""
    history = get_history_from_db(limit=50)
    return jsonify(history)

@app.route("/api/product/<asin>")
def api_product(asin):
    """API do pobierania szczegółów produktu"""
    details = get_product_details(asin)
    if details:
        details['extra_images'] = get_product_images(asin)
        return jsonify(details)
    return jsonify({'error': 'Not found'}), 404

@app.route("/proxy")
def proxy():
    r = requests.get(unquote(request.args.get("u", "")), timeout=25)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
def download_zip():
    mem = BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        for i, u in enumerate(request.form.getlist("selected")):
            z.writestr(
                f"image_{i+1}.jpg",
                requests.get(u, timeout=25).content
            )
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name="images.zip"
    )

if __name__ == "__main__":
    app.run(debug=True, port=8000)
