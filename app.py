from flask import Flask, render_template, request, send_file, Response, jsonify, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
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
from datetime import datetime, timedelta
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# ================== LOGIN SETUP ==================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

DB_PATH = "history.db"
CACHE_DIR = "cache"
UPLOADS_DIR = "uploads"
MAX_HISTORY = 50

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

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

# ================== DATABASE ==================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

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

    # EBAY ACCOUNTS
    c.execute('''
        CREATE TABLE IF NOT EXISTS ebay_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ebay_user_id TEXT,
            access_token TEXT,
            refresh_token TEXT,
            token_expiry DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()

init_db()

# ================== AUTH ROUTES ==================

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT id, email, password_hash FROM users WHERE email = ?', (email,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            login_user(User(user[0], user[1]))
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if password != confirm:
            flash("Passwords do not match")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters")
            return render_template("register.html")

        password_hash = generate_password_hash(password)

        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', (email, password_hash))
            conn.commit()
            user_id = c.lastrowid
            conn.close()

            login_user(User(user_id, email))
            return redirect(url_for('dashboard'))

        except sqlite3.IntegrityError:
            flash("Email already registered")

    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ================== EBAY OAUTH ==================

@app.route("/connect-ebay")
@login_required
def connect_ebay():
    client_id = os.environ.get("EBAY_CLIENT_ID")
    redirect_uri = os.environ.get("EBAY_REDIRECT_URI")

    scope = (
        "https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.inventory "
        "https://api.ebay.com/oauth/api_scope/sell.account "
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
    )

    auth_url = (
        "https://auth.ebay.com/oauth2/authorize?"
        f"client_id={client_id}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
    )

    return redirect(auth_url)

@app.route("/ebay-callback")
@login_required
def ebay_callback():
    code = request.args.get("code")
    if not code:
        return "Missing authorization code", 400

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    redirect_uri = os.environ.get("EBAY_REDIRECT_URI")

    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri
    }

    token_response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data
    )

    if token_response.status_code != 200:
        return f"Token error: {token_response.text}", 400

    token_data = token_response.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    expiry_time = datetime.utcnow() + timedelta(seconds=expires_in)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO ebay_accounts (user_id, access_token, refresh_token, token_expiry)
        VALUES (?, ?, ?, ?)
    ''', (current_user.id, access_token, refresh_token, expiry_time))
    conn.commit()
    conn.close()

    flash("eBay account connected successfully!", "success")
    return redirect(url_for("dashboard"))

# ================== HEALTH ==================

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(debug=True)
