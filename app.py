from flask import Flask, render_template, request, send_file, Response, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
import re
import json
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote, urlencode
import zipfile
from hashlib import md5
import sqlite3
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

# eBay OAuth Configuration
EBAY_APP_ID = os.environ.get('EBAY_APP_ID', '')
EBAY_CERT_ID = os.environ.get('EBAY_CERT_ID', '')
EBAY_REDIRECT_URI = os.environ.get('EBAY_REDIRECT_URI', 'https://amazon-to-ebay-1.onrender.com/ebay/callback')
EBAY_RUNAME = 'Everyday_Deals_-Everyday-Everyd-qnxbthvql'

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

EBAY_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
]

# Bright Data Configuration
BRIGHTDATA_API_KEY = "1bbcee91427624e79bfbc87c146ae2dbf0ddce6f55f0ed8ef2f448b49ca3e93d"
BRIGHTDATA_ZONE = "web_unlocker1"
BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"

# Directories
CACHE_DIR = "cache"
UPLOADS_DIR = "uploads"
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Database
DB_PATH = "/opt/render/project/data/history.db"
MAX_HISTORY = 50

# User Model
class User(UserMixin):
    def __init__(self, id, email):
        self.id = id
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, email FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return User(id=user[0], email=user[1])
    return None

def init_db():
    """Initialize database"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # eBay accounts table
    c.execute('''
        CREATE TABLE IF NOT EXISTS ebay_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ebay_user_id TEXT,
            email TEXT,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at DATETIME NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Search history
    c.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asin TEXT NOT NULL,
            title TEXT,
            image_url TEXT,
            sku TEXT,
            notes TEXT,
            custom_description TEXT,
            price TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, asin)
        )
    ''')
    
    # Product images
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            asin TEXT NOT NULL,
            image_path TEXT NOT NULL,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def save_to_history_db(user_id, asin, title=None, image_url=None, sku=None, price=None):
    """Save or update search history"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('SELECT id FROM search_history WHERE user_id = ? AND asin = ?', (user_id, asin))
        existing = c.fetchone()
        
        if existing:
            c.execute('''
                UPDATE search_history 
                SET title = ?, image_url = ?, sku = ?, price = ?, timestamp = ?
                WHERE user_id = ? AND asin = ?
            ''', (title, image_url, sku, price, datetime.now(), user_id, asin))
        else:
            c.execute('''
                INSERT INTO search_history (user_id, asin, title, image_url, sku, price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, asin, title, image_url, sku, price, datetime.now()))
        
        c.execute('''
            DELETE FROM search_history
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM search_history
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
        ''', (user_id, user_id, MAX_HISTORY))
        
        conn.commit()
    finally:
        conn.close()

def get_history_from_db(user_id, limit=50):
    """Get user's search history"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT asin, title, image_url, sku, price, timestamp
        FROM search_history
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            'asin': row[0],
            'title': row[1],
            'image': row[2],
            'sku': row[3] or '',
            'price': row[4] or '',
            'timestamp': row[5]
        }
        for row in rows
    ]

def get_product_details(user_id, asin):
    """Get product details"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT asin, title, image_url, sku, notes, custom_description, price 
        FROM search_history 
        WHERE user_id = ? AND asin = ?
    ''', (user_id, asin))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {
            'asin': row[0],
            'title': row[1],
            'image_url': row[2],
            'sku': row[3] or '',
            'notes': row[4] or '',
            'custom_description': row[5] or '',
            'price': row[6] or ''
        }
    return None

def add_product_image(user_id, asin, image_path):
    """Add product image"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO product_images (user_id, asin, image_path, uploaded_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, asin, image_path, datetime.now()))
    conn.commit()
    conn.close()

def get_product_images(user_id, asin):
    """Get product images"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT image_path FROM product_images WHERE user_id = ? AND asin = ? ORDER BY uploaded_at', (user_id, asin))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_user_ebay_accounts(user_id):
    """Get user's eBay accounts"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, ebay_user_id, email, is_active, created_at
        FROM ebay_accounts
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (user_id,))
    rows = c.fetchall()
    conn.close()
    
    return [
        {
            'id': row[0],
            'ebay_user_id': row[1],
            'email': row[2],
            'is_active': row[3],
            'created_at': row[4]
        }
        for row in rows
    ]

def save_ebay_tokens(user_id, access_token, refresh_token, expires_in, ebay_user_id=None, email=None):
    """Save eBay OAuth tokens"""
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO ebay_accounts (user_id, ebay_user_id, email, access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, ebay_user_id, email, access_token, refresh_token, expires_at))
    conn.commit()
    conn.close()

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

def extract_price(html: str):
    """Extract price from Amazon HTML"""
    soup = BeautifulSoup(html, "html.parser")
    price_selectors = [
        ("span", {"class": "a-price-whole"}),
        ("span", {"class": "a-offscreen"}),
        ("span", {"id": "priceblock_ourprice"}),
        ("span", {"id": "priceblock_dealprice"}),
        ("span", {"class": "a-color-price"}),
    ]
    for tag, attrs in price_selectors:
        price_tag = soup.find(tag, attrs)
        if price_tag:
            price_text = price_tag.get_text(strip=True)
            price_clean = re.sub(r'\s+', '', price_text)
            if '£' in price_clean or '$' in price_clean or '€' in price_clean:
                return price_clean
    price_match = re.search(r'£\s*(\d+[.,]\d{2})', html)
    if price_match:
        return f"£{price_match.group(1)}"
    return None

def fetch_amazon(url_or_asin: str):
    url_or_asin = (url_or_asin or "").strip()
    if not url_or_asin:
        return {"title": "No title found", "images": [], "bullets": [], "meta": {}, "price": None}

    if "amazon" not in url_or_asin:
        asin = url_or_asin.upper()
        amazon_url = f"https://www.amazon.co.uk/dp/{asin}"
    else:
        amazon_url = url_or_asin.split("?", 1)[0]
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", amazon_url, flags=re.I)

    cache_key = md5(asin.encode()).hexdigest()
    cached = cache_load(cache_key)
    if cached:
        print(f"✓ Cache hit for {asin}")
        return cached

    print(f"→ Fetching {asin} from BrightData...")

    headers = {
        "Authorization": f"Bearer {BRIGHTDATA_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "zone": BRIGHTDATA_ZONE,
        "url": amazon_url,
        "format": "raw"
    }

    try:
        r = requests.post(
            BRIGHTDATA_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=150
        )
        r.raise_for_status()
        html = r.text
        print(f"✓ Successfully fetched {asin}")
        
    except requests.exceptions.Timeout:
        print(f"⚠️ TIMEOUT for {asin}")
        return {
            "title": f"[TIMEOUT] {asin} - Try again",
            "images": [],
            "bullets": ["BrightData timeout - try again later"],
            "meta": {},
            "price": None
        }
    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR for {asin}: {str(e)}")
        return {
            "title": f"[ERROR] {asin}",
            "images": [],
            "bullets": [f"Error: {str(e)}"],
            "meta": {},
            "price": None
        }

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("span", {"id": "productTitle"})
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    images = extract_highres_images(html)
    price = extract_price(html)

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
        "meta": meta,
        "price": price
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

# ========== AUTH ROUTES ==========

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        
        if not email or not password:
            flash("Please enter email and password", "error")
            return render_template("login.html")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, email, password_hash FROM users WHERE email = ?', (email,))
        user = c.fetchone()
        conn.close()
        
        if user and check_password_hash(user[2], password):
            user_obj = User(id=user[0], email=user[1])
            login_user(user_obj, remember=remember)
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password", "error")
    
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        
        if not email or not password:
            flash("Please enter email and password", "error")
            return render_template("register.html")
        
        if password != confirm:
            flash("Passwords do not match", "error")
            return render_template("register.html")
        
        if len(password) < 8:
            flash("Password must be at least 8 characters", "error")
            return render_template("register.html")
        
        password_hash = generate_password_hash(password)
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, password_hash))
            conn.commit()
            user_id = c.lastrowid
            conn.close()
            
            user_obj = User(id=user_id, email=email)
            login_user(user_obj)
            flash("Account created successfully!", "success")
            return redirect(url_for('dashboard'))
            
        except sqlite3.IntegrityError:
            flash("Email already registered", "error")
        except Exception as e:
            flash(f"Registration error: {str(e)}", "error")
    
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route("/dashboard")
@login_required
def dashboard():
    ebay_accounts = get_user_ebay_accounts(current_user.id)
    return render_template("index.html", ebay_accounts=ebay_accounts)

# (Kontynuacja z części 1...)

# ========== EBAY OAUTH ROUTES ==========

@app.route("/ebay/connect")
@login_required
def ebay_connect():
    """Step 1: Redirect to eBay OAuth"""
    if not EBAY_APP_ID or not EBAY_CERT_ID:
        flash("eBay credentials not configured", "error")
        return redirect(url_for('dashboard'))
    
    # eBay OAuth wymaga "state" parameter dla bezpieczeństwa
    import secrets
    state = secrets.token_urlsafe(32)
    session['ebay_oauth_state'] = state
    
    params = {
        'client_id': EBAY_APP_ID,
        'redirect_uri': EBAY_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(EBAY_SCOPES),
        'state': state
    }
    
    auth_url = f"{EBAY_AUTH_URL}?{urlencode(params)}"
    return redirect(auth_url)

@app.route("/ebay/callback")
@login_required
def ebay_callback():
    """Step 2: Exchange code for token"""
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state parameter
    if state != session.get('ebay_oauth_state'):
        flash("eBay authorization failed - invalid state", "error")
        return redirect(url_for('dashboard'))
    
    if not code:
        flash("eBay authorization failed - no code received", "error")
        return redirect(url_for('dashboard'))
    
    # Clear state from session
    session.pop('ebay_oauth_state', None)
    
    # Exchange code for tokens
    auth_string = f"{EBAY_APP_ID}:{EBAY_CERT_ID}"
    auth_header = base64.b64encode(auth_string.encode()).decode()
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f'Basic {auth_header}'
    }
    
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': EBAY_REDIRECT_URI
    }
    
    try:
        response = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        
        token_data = response.json()
        access_token = token_data['access_token']
        refresh_token = token_data['refresh_token']
        expires_in = token_data['expires_in']
        
        # Save tokens to database
        save_ebay_tokens(current_user.id, access_token, refresh_token, expires_in)
        
        flash("eBay account connected successfully!", "success")
        return redirect(url_for('dashboard'))
        
    except requests.exceptions.HTTPError as e:
        error_detail = e.response.text if e.response else str(e)
        flash(f"eBay OAuth Error: {error_detail}", "error")
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash(f"Error: {str(e)}", "error")
        return redirect(url_for('dashboard'))

# ========== SCRAPER ROUTES ==========

@app.route("/scrape", methods=["POST"])
@login_required
def scrape():
    url_input = request.form.get("url", "")
    data = fetch_amazon(url_input)
    
    if "amazon" not in url_input:
        asin = url_input.upper()
    else:
        asin = re.sub(r".*?/dp/([A-Z0-9]+).*", r"\1", url_input, flags=re.I)
    
    first_image = data["images"][0] if data["images"] else None
    price = data.get("price")
    save_to_history_db(current_user.id, asin, truncate_title_80(data["title"]), first_image, price=price)
    
    existing = get_product_details(current_user.id, asin)
    extra_images = get_product_images(current_user.id, asin)
    
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
        listing_text=listing_text,
        price=price or ''
    )

@app.route("/save-product", methods=["POST"])
@login_required
def save_product():
    """Save SKU, notes and additional images"""
    try:
        asin = request.form.get("asin")
        sku = request.form.get("sku", "").strip()
        notes = request.form.get("notes", "").strip()
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'}), 400
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE search_history SET sku = ?, notes = ?, timestamp = ? WHERE user_id = ? AND asin = ?', 
                  (sku, notes, datetime.now(), current_user.id, asin))
        conn.commit()
        conn.close()
        
        saved_count = 0
        uploaded_filenames = []
        if 'images' in request.files:
            files = request.files.getlist('images')
            for file in files:
                if file and file.filename:
                    original_filename = secure_filename(file.filename)
                    timestamp = str(int(datetime.now().timestamp()))
                    filename = f"{asin}_{timestamp}_{original_filename}"
                    filepath = os.path.join(UPLOADS_DIR, filename)
                    
                    file.save(filepath)
                    add_product_image(current_user.id, asin, filename)
                    uploaded_filenames.append(filename)
                    saved_count += 1
        
        return jsonify({
            'success': True, 
            'message': 'Saved!',
            'images_saved': saved_count,
            'uploaded_images': uploaded_filenames
        }), 200
    
    except Exception as e:
        print(f"Error in save_product: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/delete-image", methods=["POST"])
@login_required
def delete_image():
    """Delete additional image"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        filename = data.get('filename')
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM product_images WHERE user_id = ? AND asin = ? AND image_path = ?', 
                  (current_user.id, asin, filename))
        conn.commit()
        conn.close()
        
        filepath = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({'success': True}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/save-description", methods=["POST"])
@login_required
def save_description():
    """Save edited description"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        description = data.get('description')
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE search_history SET custom_description = ? WHERE user_id = ? AND asin = ?', 
                  (description, current_user.id, asin))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/save-title", methods=["POST"])
@login_required
def save_title():
    """Save edited title"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        title = data.get('title', '').strip()
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'}), 400
        
        title = title[:80]
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE search_history SET title = ? WHERE user_id = ? AND asin = ?', 
                  (title, current_user.id, asin))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True}), 200
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    """Serve uploaded images"""
    filepath = os.path.join(UPLOADS_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/jpeg')
    return "Not found", 404

@app.route("/api/history")
@login_required
def api_history():
    """API endpoint for history"""
    history = get_history_from_db(current_user.id, limit=50)
    return jsonify(history)

@app.route("/api/product/<asin>")
@login_required
def api_product(asin):
    """API for product details"""
    details = get_product_details(current_user.id, asin)
    if details:
        details['extra_images'] = get_product_images(current_user.id, asin)
        return jsonify(details)
    return jsonify({'error': 'Not found'}), 404

@app.route("/clear-cache", methods=["POST"])
@login_required
def clear_cache():
    """Clear scraped products cache"""
    try:
        deleted_count = 0
        
        if os.path.exists(CACHE_DIR):
            for filename in os.listdir(CACHE_DIR):
                filepath = os.path.join(CACHE_DIR, filename)
                if os.path.isfile(filepath) and filename.endswith('.json'):
                    os.remove(filepath)
                    deleted_count += 1
        
        return jsonify({
            'success': True,
            'deleted': deleted_count,
            'message': f'Cleared {deleted_count} products from cache'
        }), 200
    
    except Exception as e:
        print(f"Error clearing cache: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/clear-single-cache", methods=["POST"])
@login_required
def clear_single_cache():
    """Clear cache for single product"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'}), 400
        
        cache_key = md5(asin.encode()).hexdigest()
        cache_file = os.path.join(CACHE_DIR, cache_key + ".json")
        
        if os.path.exists(cache_file):
            os.remove(cache_file)
            return jsonify({
                'success': True,
                'message': f'Cache cleared for {asin}'
            }), 200
        else:
            return jsonify({
                'success': True,
                'message': f'No cache for {asin}'
            }), 200
    
    except Exception as e:
        print(f"Error clearing single cache: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/delete-from-history", methods=["POST"])
@login_required
def delete_from_history():
    """Delete product from history"""
    try:
        data = request.get_json()
        asin = data.get('asin')
        
        if not asin:
            return jsonify({'success': False, 'error': 'Missing ASIN'}), 400
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('DELETE FROM search_history WHERE user_id = ? AND asin = ?', (current_user.id, asin))
        
        c.execute('SELECT image_path FROM product_images WHERE user_id = ? AND asin = ?', (current_user.id, asin))
        images = c.fetchall()
        for img in images:
            filepath = os.path.join(UPLOADS_DIR, img[0])
            if os.path.exists(filepath):
                os.remove(filepath)
        
        c.execute('DELETE FROM product_images WHERE user_id = ? AND asin = ?', (current_user.id, asin))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Deleted {asin} from history'
        }), 200
    
    except Exception as e:
        print(f"Error deleting from history: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/history")
@login_required
def history_page():
    """History page"""
    history = get_history_from_db(current_user.id, limit=50)
    return render_template("history.html", history=history)


@app.route("/proxy")
def proxy():
    r = requests.get(unquote(request.args.get("u", "")), timeout=25)
    return Response(r.content, mimetype="image/jpeg")

@app.route("/download-zip", methods=["POST"])
@login_required
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

@app.route("/privacy")
def privacy():
    """Privacy policy page (required by eBay)"""
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Privacy Policy - Everyday Deals UK</title>
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
                padding: 40px 20px; 
                max-width: 800px; 
                margin: 0 auto;
                line-height: 1.6;
                color: #333;
            }
            h1 { color: #0f0c29; margin-bottom: 10px; }
            h2 { color: #302b63; margin-top: 30px; }
            .date { color: #666; font-size: 14px; margin-bottom: 20px; }
            ul { margin-left: 20px; }
            li { margin: 8px 0; }
        </style>
    </head>
    <body>
        <h1>Privacy Policy</h1>
        <p class="date">Last updated: February 13, 2026</p>
        
        <h2>Introduction</h2>
        <p>Everyday Deals UK respects your privacy. This policy explains how we handle your data when you use our Amazon to eBay listing tool.</p>
        
        <h2>Data We Access</h2>
        <p>When you connect your eBay account, we access:</p>
        <ul>
            <li>Your eBay seller account information</li>
            <li>Inventory management permissions</li>
            <li>Listing and fulfillment permissions</li>
        </ul>
        
        <h2>How We Use Your Data</h2>
        <p>We use your eBay account data only to:</p>
        <ul>
            <li>Create and manage product listings on your behalf</li>
            <li>Update inventory and pricing information</li>
            <li>Process orders and fulfillment</li>
        </ul>
        
        <h2>Data Storage</h2>
        <p>Your eBay OAuth tokens are stored securely and encrypted. We do not store your eBay password.</p>
        
        <h2>Data Sharing</h2>
        <p>We do NOT share your data with any third parties. Your information is used exclusively for the functionality of our service.</p>
        
        <h2>Your Rights</h2>
        <p>You can disconnect your eBay account at any time. You can also request deletion of your data by contacting us.</p>
        
        <h2>Contact</h2>
        <p>For questions about this privacy policy, contact us at: support@everydaydeals.co.uk</p>
        
        <p style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
            <a href="/dashboard" style="color: #302b63; text-decoration: none;">Back to Dashboard</a>
        </p>
    </body>
    </html>
    '''
    return html

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(debug=True, port=8000)
