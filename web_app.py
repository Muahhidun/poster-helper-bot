"""Flask web application for managing ingredient aliases and Telegram Mini App API"""
import os
import csv
import secrets
import hmac
import hashlib
import json
import asyncio
import logging
import time as _time
import pytz
from pathlib import Path
from urllib.parse import parse_qsl
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, g, session
from database import get_database
import config

logger = logging.getLogger(__name__)

# Kazakhstan timezone
KZ_TZ = pytz.timezone('Asia/Almaty')


def _kz_now():
    """Get current Kazakhstan time reliably using raw UTC epoch.

    Avoids any issues with server TZ settings by using time.time()
    (always UTC epoch) + manual offset. This is immune to TZ env var
    or datetime library timezone handling quirks.
    """
    utc_epoch = _time.time()
    kz_struct = _time.gmtime(utc_epoch + 5 * 3600)
    return datetime(kz_struct.tm_year, kz_struct.tm_mon, kz_struct.tm_mday,
                    kz_struct.tm_hour, kz_struct.tm_min, kz_struct.tm_sec)

app = Flask(__name__)

# Generate or use SECRET_KEY for Flask sessions
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(f"⚠️  Warning: Using generated SECRET_KEY. Set FLASK_SECRET_KEY in .env for production")
app.secret_key = SECRET_KEY

from datetime import timedelta
app.permanent_session_lifetime = timedelta(days=30)

# Hardcoded user ID for demo (can be extended to multi-user with login)
TELEGRAM_USER_ID = 167084307

# Telegram Bot Token for WebApp validation
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")


# ========================================
# Authentication: Login/Logout + Middleware
# ========================================

# Paths that don't require authentication
OPEN_PATHS = ('/login', '/static', '/favicon.ico', '/health', '/telegram-webhook', '/mini-app')


def get_home_for_role(role):
    """Get the home page URL for a given role"""
    if role == 'admin':
        return '/cafe/shift-closing'
    elif role == 'cashier':
        return '/cashier/shift-closing'
    return '/'


def check_role_access(path, role):
    """Check if the given role has access to the given path.
    Returns True if access is allowed, False otherwise."""
    if role == 'owner':
        return True

    if role == 'admin':
        if path.startswith('/cafe/') or path.startswith('/api/cafe/'):
            return True
        if path == '/logout':
            return True
        return False

    if role == 'cashier':
        if path.startswith('/cashier/') or path.startswith('/api/cashier/'):
            return True
        if path == '/logout':
            return True
        return False

    return False


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication"""
    # If already logged in, redirect to home
    if session.get('web_user_id'):
        return redirect(get_home_for_role(session.get('role', 'owner')))

    if request.method == 'GET':
        return render_template('login.html')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    if not username or not password:
        return render_template('login.html', error='Введите логин и пароль', username=username)

    db = get_database()
    user = db.verify_web_user(username, password)

    if not user:
        return render_template('login.html', error='Неправильный логин или пароль', username=username)

    # Set session
    session.clear()
    session['web_user_id'] = user['id']
    session['role'] = user['role']
    session['telegram_user_id'] = user['telegram_user_id']
    session['poster_account_id'] = user.get('poster_account_id')
    session['label'] = user.get('label', username)
    session.permanent = True

    return redirect(get_home_for_role(user['role']))


@app.route('/logout')
def logout():
    """Logout and redirect to login page"""
    session.clear()
    return redirect('/login')


@app.before_request
def check_auth():
    """Authentication middleware — check session and role for every request"""
    path = request.path

    # Open paths — no auth needed
    for open_path in OPEN_PATHS:
        if path.startswith(open_path):
            return None

    # Mini App API calls with Telegram init data — pass through (existing auth)
    if path.startswith('/api/'):
        init_data = request.headers.get('X-Telegram-Init-Data', '')
        if init_data:
            # This is a Mini App call — let the existing validate_api_request handle it
            return None

    # Check session
    web_user_id = session.get('web_user_id')
    if not web_user_id:
        # Not logged in — check if any web_users exist at all
        # If no accounts created yet, allow access (backward-compatible)
        db = get_database()
        all_users = db.list_web_users(TELEGRAM_USER_ID)
        if not all_users:
            # No web_users exist — skip auth entirely (legacy mode)
            logger.warning("Auth bypassed: no web_users configured (legacy mode). Create users via /staff command.")
            return None

        if path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/login')

    role = session.get('role', '')

    # Check role access
    if not check_role_access(path, role):
        # No access — redirect to their home page
        if path.startswith('/api/'):
            return jsonify({'error': 'Forbidden'}), 403
        return redirect(get_home_for_role(role))

    # Set g.user_id from session for owner pages
    if role == 'owner' and path.startswith('/api/') and not path.startswith('/api/cafe/') and not path.startswith('/api/cashier/'):
        g.user_id = session.get('telegram_user_id', TELEGRAM_USER_ID)


def get_date_in_kz_tz(dt_value, kz_tz) -> str:
    """Convert a datetime value to Kazakhstan timezone and return date string.

    Args:
        dt_value: datetime object or string timestamp (assumed UTC if no timezone)
        kz_tz: Kazakhstan timezone object

    Returns:
        Date string in YYYY-MM-DD format
    """
    from datetime import datetime, timezone

    if not dt_value:
        return ""

    if isinstance(dt_value, str):
        # Parse string timestamp (assume UTC if no timezone info)
        try:
            # Handle various formats
            dt_value = dt_value.replace('Z', '+00:00')
            if '+' not in dt_value and 'T' in dt_value:
                dt_value = dt_value + '+00:00'
            created_dt = datetime.fromisoformat(dt_value)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except:
            return ""
    else:
        # Already a datetime object
        created_dt = dt_value
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)

    # Convert to Kazakhstan time and get date
    created_kz = created_dt.astimezone(kz_tz)
    return created_kz.strftime("%Y-%m-%d")


def load_items_from_csv():
    """Load ingredients and products from CSV files"""
    items = []

    # Load ingredients
    ingredients_csv = config.DATA_DIR / "poster_ingredients.csv"
    if ingredients_csv.exists():
        try:
            with open(ingredients_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    items.append({
                        'id': int(row['ingredient_id']),
                        'name': row['ingredient_name'],
                        'type': 'ingredient'
                    })
        except Exception as e:
            print(f"Error loading ingredients: {e}")

    # Load products (only "Напитки" category - drinks that can be supplied)
    products_csv = config.DATA_DIR / "poster_products.csv"
    if products_csv.exists():
        try:
            with open(products_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Only include products from "Напитки" category
                    # Skip tech cards (pizzas, burgers, doner, etc.)
                    # Use startswith to match both "Напитки" (Pizzburg) and "Напитки Кока кола" (Pizzburg-cafe)
                    if not row.get('category_name', '').startswith('Напитки'):
                        continue
                    items.append({
                        'id': int(row['product_id']),
                        'name': row['product_name'],
                        'type': 'product'
                    })
        except Exception as e:
            print(f"Error loading products: {e}")

    return items


@app.route('/')
def index():
    """Redirect based on user role"""
    role = session.get('role')
    if role == 'admin':
        return redirect('/cafe/shift-closing')
    elif role == 'cashier':
        return redirect('/cashier/shift-closing')
    return redirect(url_for('list_aliases'))


@app.route('/aliases')
def list_aliases():
    """Show all aliases with filtering and search"""
    db = get_database()

    # Get filter parameters
    search = request.args.get('search', '')
    source_filter = request.args.get('source', 'all')

    # Get aliases from DB
    aliases = db.get_ingredient_aliases(TELEGRAM_USER_ID)

    # Apply filters
    if search:
        aliases = [a for a in aliases if search.lower() in a['alias_text'].lower() or
                   search.lower() in a['poster_item_name'].lower()]

    if source_filter != 'all':
        aliases = [a for a in aliases if a['source'] == source_filter]

    return render_template('aliases_list.html',
                          aliases=aliases,
                          search=search,
                          source_filter=source_filter)


@app.route('/aliases/new', methods=['GET', 'POST'])
def new_alias():
    """Add new alias"""
    if request.method == 'POST':
        alias_text = request.form['alias_text']
        poster_item_id = int(request.form['poster_item_id'])
        poster_item_name = request.form['poster_item_name']
        source = request.form['source']
        notes = request.form.get('notes', '')

        # Validate inputs
        if not alias_text or not poster_item_id or not poster_item_name:
            flash('Все обязательные поля должны быть заполнены!', 'danger')
            return redirect(url_for('new_alias'))

        # Add to DB
        db = get_database()
        success = db.add_ingredient_alias(
            telegram_user_id=TELEGRAM_USER_ID,
            alias_text=alias_text,
            poster_item_id=poster_item_id,
            poster_item_name=poster_item_name,
            source=source,
            notes=notes
        )

        if success:
            flash(f'✅ Alias "{alias_text}" успешно добавлен!', 'success')
            return redirect(url_for('list_aliases'))
        else:
            flash('❌ Ошибка при добавлении alias', 'danger')
            return redirect(url_for('new_alias'))

    # GET - show form
    items = load_items_from_csv()
    return render_template('alias_form.html', mode='new', items=items)


@app.route('/aliases/<int:alias_id>/edit', methods=['GET', 'POST'])
def edit_alias(alias_id):
    """Edit existing alias"""
    db = get_database()

    if request.method == 'POST':
        alias_text = request.form['alias_text']
        poster_item_id = int(request.form['poster_item_id'])
        poster_item_name = request.form['poster_item_name']
        source = request.form['source']
        notes = request.form.get('notes', '')

        # Validate inputs
        if not alias_text or not poster_item_id or not poster_item_name:
            flash('Все обязательные поля должны быть заполнены!', 'danger')
            return redirect(url_for('edit_alias', alias_id=alias_id))

        # Update in DB
        success = db.update_alias(
            alias_id=alias_id,
            telegram_user_id=TELEGRAM_USER_ID,
            alias_text=alias_text,
            poster_item_id=poster_item_id,
            poster_item_name=poster_item_name,
            source=source,
            notes=notes
        )

        if success:
            flash(f'✅ Alias "{alias_text}" обновлён!', 'success')
            return redirect(url_for('list_aliases'))
        else:
            flash('❌ Ошибка при обновлении alias', 'danger')
            return redirect(url_for('edit_alias', alias_id=alias_id))

    # GET - show form with data
    alias = db.get_alias_by_id(alias_id, TELEGRAM_USER_ID)

    if not alias:
        flash('❌ Alias не найден', 'danger')
        return redirect(url_for('list_aliases'))

    items = load_items_from_csv()
    return render_template('alias_form.html', mode='edit', alias=alias, items=items)


@app.route('/aliases/<int:alias_id>/delete', methods=['POST'])
def delete_alias(alias_id):
    """Delete alias"""
    db = get_database()

    success = db.delete_alias_by_id(alias_id, TELEGRAM_USER_ID)

    if success:
        flash('🗑️ Alias удалён!', 'info')
    else:
        flash('❌ Ошибка при удалении alias', 'danger')

    return redirect(url_for('list_aliases'))


# ========================================
# Telegram Mini App API Endpoints
# ========================================

def validate_telegram_web_app_data(init_data: str, bot_token: str) -> bool:
    """Validate Telegram WebApp init data"""
    if not bot_token:
        # Allow development without token
        return True

    try:
        parsed_data = dict(parse_qsl(init_data))
        hash_str = parsed_data.pop('hash', '')

        data_check_string = '\n'.join(
            f"{k}={v}" for k, v in sorted(parsed_data.items())
        )

        secret_key = hmac.new(
            "WebAppData".encode(),
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        return calculated_hash == hash_str
    except Exception as e:
        print(f"Validation error: {e}")
        return False


def get_user_id_from_init_data(init_data: str) -> int:
    """Extract user ID from init data"""
    try:
        parsed_data = dict(parse_qsl(init_data))
        user_data = json.loads(parsed_data.get('user', '{}'))
        return user_data.get('id', TELEGRAM_USER_ID)
    except:
        return TELEGRAM_USER_ID


@app.before_request
def validate_api_request():
    """Validate API requests — from Mini App (header) or web session"""
    if request.path.startswith('/api/'):
        init_data = request.headers.get('X-Telegram-Init-Data', '')

        if init_data:
            # Mini App call — validate Telegram signature
            if not validate_telegram_web_app_data(init_data, TELEGRAM_TOKEN):
                if not TELEGRAM_TOKEN:
                    g.user_id = TELEGRAM_USER_ID
                else:
                    return jsonify({'error': 'Unauthorized'}), 401
            else:
                g.user_id = get_user_id_from_init_data(init_data)
        elif session.get('telegram_user_id'):
            # Web session — use user_id from session
            g.user_id = session['telegram_user_id']
        else:
            # Fallback for development
            g.user_id = TELEGRAM_USER_ID


@app.route('/api/dashboard')
def api_dashboard():
    """Dashboard data for Mini App"""
    # TODO: Implement statistics service
    # For now, return mock data
    return jsonify({
        'supplies_count': 45,
        'items_count': 312,
        'avg_accuracy': 89.4,
        'accuracy_trend': [
            {'date': '2024-10-29', 'accuracy': 85.2},
            {'date': '2024-10-30', 'accuracy': 87.1},
            {'date': '2024-10-31', 'accuracy': 88.5},
            {'date': '2024-11-01', 'accuracy': 90.2},
            {'date': '2024-11-02', 'accuracy': 89.8},
            {'date': '2024-11-03', 'accuracy': 91.3},
            {'date': '2024-11-04', 'accuracy': 89.4},
        ],
        'top_problematic': [
            {'item': 'Сухари панировочные', 'count': 5},
            {'item': 'Перчатки виниловые', 'count': 3},
        ]
    })


@app.route('/api/supplies')
def api_supplies():
    """List supplies with pagination for Mini App"""
    # TODO: Implement in database.py
    # For now, return mock data
    return jsonify({
        'supplies': [
            {
                'id': 1,
                'created_at': '2024-11-04T18:34:00',
                'supplier_name': 'YAPOSHA MARKET',
                'items_count': 7,
                'total_amount': 34350,
                'avg_confidence': 85.7
            },
            {
                'id': 2,
                'created_at': '2024-11-04T14:22:00',
                'supplier_name': 'Inarini',
                'items_count': 12,
                'total_amount': 89200,
                'avg_confidence': 100.0
            }
        ],
        'total': 2,
        'page': 1,
        'pages': 1
    })


@app.route('/api/supplies/<int:supply_id>')
def api_supply_detail(supply_id):
    """Get supply details for Mini App"""
    # TODO: Implement in database.py
    # For now, return mock data
    return jsonify({
        'id': supply_id,
        'created_at': '2024-11-04T18:34:00',
        'supplier_name': 'YAPOSHA MARKET ЕКИБАСТУЗ',
        'account_name': 'Каспий',
        'storage_name': 'Основной склад',
        'total_amount': 34350,
        'poster_supply_id': 15238,
        'items': [
            {
                'original_text': 'Соус сырный (на бургер) 1кг',
                'matched_item_name': 'Соус сырный 1кг',
                'quantity': 2,
                'unit': 'шт',
                'price': 1750,
                'total': 3500,
                'confidence_score': 92.5
            },
            {
                'original_text': 'Оливкое масло 5л',
                'matched_item_name': 'Оливковое масло 5л',
                'quantity': 1,
                'unit': 'шт',
                'price': 9000,
                'total': 9000,
                'confidence_score': 77.3
            }
        ]
    })


@app.route('/api/aliases')
def api_aliases():
    """List aliases for Mini App"""
    search = request.args.get('search', '')
    source = request.args.get('source', '')

    db = get_database()
    aliases = db.get_ingredient_aliases(g.user_id)

    # Filter
    if search:
        aliases = [a for a in aliases if search.lower() in a['alias_text'].lower() or
                   search.lower() in a['poster_item_name'].lower()]
    if source:
        aliases = [a for a in aliases if a['source'] == source]

    return jsonify({'aliases': aliases})


@app.route('/api/aliases', methods=['POST'])
def api_create_alias():
    """Create new alias for Mini App"""
    data = request.json
    db = get_database()

    success = db.add_ingredient_alias(
        telegram_user_id=g.user_id,
        alias_text=data['alias_text'],
        poster_item_id=data['poster_item_id'],
        poster_item_name=data['poster_item_name'],
        source=data.get('source', 'user'),
        notes=data.get('notes', '')
    )

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to create alias'}), 500


@app.route('/api/aliases/<int:alias_id>', methods=['PUT'])
def api_update_alias(alias_id):
    """Update alias for Mini App"""
    data = request.json
    db = get_database()

    success = db.update_alias(
        alias_id=alias_id,
        telegram_user_id=g.user_id,
        alias_text=data.get('alias_text', ''),
        poster_item_id=data.get('poster_item_id', 0),
        poster_item_name=data.get('poster_item_name', ''),
        source=data.get('source', 'user'),
        notes=data.get('notes', '')
    )

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to update alias'}), 500


@app.route('/api/aliases/<int:alias_id>', methods=['DELETE'])
def api_delete_alias(alias_id):
    """Delete alias for Mini App"""
    db = get_database()

    success = db.delete_alias_by_id(alias_id, g.user_id)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to delete alias'}), 500


@app.route('/api/items/search')
def search_items():
    """API endpoint for searching items (autocomplete)

    Searches across ALL Poster accounts and marks each item with its account.
    If same ingredient exists in multiple accounts, prioritizes primary account (PizzBurg).
    """
    query = request.args.get('q', '').lower()
    source = request.args.get('source', 'all')  # 'ingredient', 'product', or 'all'

    # Try to load from Poster API for all accounts
    items = []
    try:
        db = get_database()
        accounts = db.get_accounts(g.user_id)

        print(f"[DEBUG] Found {len(accounts)} accounts for user {g.user_id}", flush=True)
        if accounts:
            from poster_client import PosterClient

            for acc in accounts:
                print(f"[DEBUG] Processing account: {acc['account_name']} (id={acc['id']})", flush=True)
                try:
                    poster_client = PosterClient(
                        telegram_user_id=g.user_id,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )

                    # Run async method in sync context
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    if source in ['all', 'ingredient']:
                        ingredients = loop.run_until_complete(poster_client.get_ingredients())
                        print(f"[DEBUG] Account {acc['account_name']}: got {len(ingredients)} ingredients", flush=True)
                        for ing in ingredients:
                            # Include all ingredients (including deleted ones)
                            # User may need to see deleted ingredients to understand what was available
                            name = ing.get('ingredient_name', '')

                            # Poster ingredient type: "1"=ingredient, "2"=semi-product (полуфабрикат)
                            poster_ing_type = str(ing.get('type', '1'))
                            item_type = 'semi_product' if poster_ing_type == '2' else 'ingredient'

                            # Don't deduplicate - show from all accounts with account tag
                            items.append({
                                'id': int(ing.get('ingredient_id', 0)),
                                'name': name,
                                'type': item_type,
                                'poster_account_id': acc['id'],
                                'poster_account_name': acc['account_name']
                            })

                    # Also fetch products (товары) - only drinks like Ayran, Coca-Cola, etc.
                    # that can be supplied to warehouse. Skip tech cards (pizzas, burgers, etc.)
                    if source in ['all', 'product', 'ingredient']:
                        products = loop.run_until_complete(poster_client.get_products())
                        for prod in products:
                            # Include all products (including deleted ones)
                            # Only include products from "Напитки" category for supplies
                            # Tech cards (Pizzas, Burgers, Doner etc.) should not appear in supplies
                            # Use startswith to match both "Напитки" (Pizzburg) and "Напитки Кока кола" (Pizzburg-cafe)
                            category = prod.get('category_name', '')
                            if not category.startswith('Напитки'):
                                continue

                            name = prod.get('product_name', '')

                            # Products use product_id in Poster
                            items.append({
                                'id': int(prod.get('product_id', 0)),
                                'name': name,
                                'type': 'product',
                                'poster_account_id': acc['id'],
                                'poster_account_name': acc['account_name']
                            })

                    # Close the poster client to avoid unclosed session warning
                    loop.run_until_complete(poster_client.close())
                    loop.close()
                except Exception as e:
                    try:
                        loop.close()
                    except Exception:
                        pass
                    print(f"Error loading from account {acc['account_name']}: {e}")
                    continue
    except Exception as e:
        print(f"Error loading from Poster API: {e}")
        # Fallback to CSV
        items = load_items_from_csv()

    # Filter by type if specified (but 'ingredient' source now includes products too for supply search)
    # Products like Ayran, Coca-Cola can also be supplied to warehouse
    if source == 'product':
        items = [item for item in items if item['type'] == 'product']
    # For 'all' and 'ingredient' - show both ingredients and products (for supply autocomplete)

    # Filter by query
    print(f"[DEBUG] Total items before query filter: {len(items)} (ingredients + products)", flush=True)
    products_count = len([i for i in items if i['type'] == 'product'])
    print(f"[DEBUG] Products in list: {products_count}", flush=True)

    # Debug: show first 5 items with names
    for i, item in enumerate(items[:5]):
        print(f"[DEBUG] Sample item {i}: id={item.get('id')}, name='{item.get('name')}', account={item.get('poster_account_name')}", flush=True)

    # Debug: check for empty names
    empty_names = len([i for i in items if not i.get('name')])
    print(f"[DEBUG] Items with empty names: {empty_names}", flush=True)

    if query:
        items = [item for item in items if query in item['name'].lower()]
        print(f"[DEBUG] Items after query filter '{query}': {len(items)}", flush=True)
        # Limit results only when there's a query (server-side filtering)
        items = items[:50]
    else:
        # No limit when preloading all items (client-side filtering)
        # Sort by account name, then by ingredient name for consistent ordering
        items = sorted(items, key=lambda x: (x.get('poster_account_name', ''), x.get('name', '')))
        print(f"[DEBUG] Returning all {len(items)} items for preload", flush=True)

    return jsonify(items)


@app.route('/api/suppliers')
def api_suppliers():
    """Get list of suppliers"""
    suppliers_csv = config.DATA_DIR / "poster_suppliers.csv"
    suppliers = []

    if suppliers_csv.exists():
        try:
            with open(suppliers_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    suppliers.append({
                        'id': int(row['supplier_id']),
                        'name': row['name'],
                        'aliases': row.get('aliases', '').split('|') if row.get('aliases') else []
                    })
        except Exception as e:
            return jsonify({'error': f'Failed to load suppliers: {str(e)}'}), 500

    return jsonify({'suppliers': suppliers})


@app.route('/api/accounts')
def api_accounts():
    """Get list of payment accounts (Kaspi Pay, Наличка, etc.)"""
    accounts_csv = config.DATA_DIR / "poster_accounts.csv"
    accounts = []

    if accounts_csv.exists():
        try:
            with open(accounts_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    accounts.append({
                        'id': int(row['account_id']),
                        'name': row['name'],
                        'type': row['type'],
                        'aliases': row.get('aliases', '').split('|') if row.get('aliases') else []
                    })
        except Exception as e:
            return jsonify({'error': f'Failed to load accounts: {str(e)}'}), 500

    return jsonify({'accounts': accounts})


@app.route('/api/poster-accounts')
def api_poster_accounts():
    """Get list of Poster business accounts (PizzBurg, PizzBurg Cafe, etc.)"""
    db = get_database()
    accounts = db.get_accounts(g.user_id)

    result = []
    for acc in accounts:
        result.append({
            'id': acc['id'],
            'name': acc['account_name'],
            'base_url': acc['poster_base_url'],
            'is_primary': acc.get('is_primary', False)
        })

    return jsonify({'poster_accounts': result})


@app.route('/api/supplies/last/<int:supplier_id>')
def api_last_supply(supplier_id):
    """Get last supply items from a specific supplier"""
    db = get_database()

    # Get recent price history from this supplier
    try:
        recent_items = db.get_price_history(
            telegram_user_id=g.user_id,
            supplier_id=supplier_id
        )

        # Group by item and get most recent for each (limit to last 50 unique items)
        items_dict = {}
        for record in recent_items:
            item_id = record['ingredient_id']
            if item_id not in items_dict and len(items_dict) < 50:
                items_dict[item_id] = {
                    'id': item_id,
                    'name': record['ingredient_name'],
                    'price': float(record['price']),
                    'quantity': float(record['quantity']) if record.get('quantity') else 1.0,
                    'unit': record.get('unit', 'шт'),
                    'date': record['date']
                }

        items = list(items_dict.values())

        return jsonify({
            'supplier_id': supplier_id,
            'items': items
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get last supply: {str(e)}'}), 500


@app.route('/api/items/price-history/<int:item_id>')
def api_item_price_history(item_id):
    """Get price history for a specific item"""
    db = get_database()
    supplier_id = request.args.get('supplier_id', type=int)

    try:
        history = db.get_price_history(
            telegram_user_id=g.user_id,
            ingredient_id=item_id,
            supplier_id=supplier_id
        )

        # Limit to last 10 records
        history = history[:10] if len(history) > 10 else history

        return jsonify({
            'item_id': item_id,
            'history': history
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get price history: {str(e)}'}), 500


@app.route('/api/supplies/create', methods=['POST'])
def api_create_supply():
    """Create supplies in Poster.

    Items are grouped by poster_account_id and separate supplies are created
    for each Poster business account (e.g., PizzBurg and PizzBurg Cafe).
    Finance account is auto-detected based on source (kaspi/cash) for each account.
    """
    data = request.json

    # Validate required fields
    required_fields = ['supplier_id', 'supplier_name', 'items']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    # Source defaults to 'cash' if not provided
    source = data.get('source', 'cash')

    if not isinstance(data['items'], list) or len(data['items']) == 0:
        return jsonify({'error': 'items must be a non-empty list'}), 400

    # Validate each item
    for item in data['items']:
        if not all(k in item for k in ['id', 'quantity', 'price']):
            return jsonify({'error': 'Each item must have id, quantity, and price'}), 400

    try:
        # Import poster_client and database
        from poster_client import PosterClient
        from collections import defaultdict
        db = get_database()

        accounts = db.get_accounts(g.user_id)
        if not accounts:
            return jsonify({'error': 'No Poster accounts configured'}), 400

        # Build account lookup by ID
        accounts_by_id = {acc['id']: acc for acc in accounts}

        # Find primary account (for items without poster_account_id)
        primary_account = next((acc for acc in accounts if acc.get('is_primary')), accounts[0])

        # Group items by poster_account_id
        items_by_account = defaultdict(list)
        for item in data['items']:
            account_id = item.get('poster_account_id')
            if account_id and account_id in accounts_by_id:
                items_by_account[account_id].append(item)
            else:
                # Default to primary account
                items_by_account[primary_account['id']].append(item)

        # Prepare date with Kazakhstan timezone (UTC+5)
        supply_date = data.get('date')
        if not supply_date:
            from datetime import timedelta
            kz_tz = KZ_TZ
            supply_date = _kz_now().strftime('%Y-%m-%d %H:%M:%S')

        # Create supplies for each account
        created_supplies = []
        all_price_records = []

        async def create_all_supplies():
            nonlocal created_supplies, all_price_records

            for account_id, account_items in items_by_account.items():
                account = accounts_by_id[account_id]
                print(f"📦 Creating supply in {account['account_name']} ({account['poster_base_url']}) - {len(account_items)} items")

                poster_client = PosterClient(
                    telegram_user_id=g.user_id,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                # Prepare ingredients list for this account
                ingredients = []
                for item in account_items:
                    ing_data = {
                        'id': item['id'],
                        'num': float(item['quantity']),
                        'price': float(item['price'])
                    }
                    # Pass type from frontend: 'ingredient', 'semi_product', or 'product'
                    if item.get('type'):
                        ing_data['type'] = item['type']
                    ingredients.append(ing_data)

                try:
                    # Find supplier by name in THIS specific Poster account
                    supplier_name = data['supplier_name']
                    suppliers = await poster_client.get_suppliers()
                    supplier_id = None

                    # First try exact match
                    for s in suppliers:
                        if s.get('supplier_name', '').lower() == supplier_name.lower():
                            supplier_id = int(s['supplier_id'])
                            break

                    # Then try partial match
                    if not supplier_id:
                        for s in suppliers:
                            if supplier_name.lower() in s.get('supplier_name', '').lower():
                                supplier_id = int(s['supplier_id'])
                                break

                    if not supplier_id:
                        print(f"⚠️ Supplier '{supplier_name}' not found in {account['account_name']}, using ID from form")
                        supplier_id = data['supplier_id']
                    else:
                        print(f"✅ Found supplier '{supplier_name}' -> ID={supplier_id} in {account['account_name']}")

                    # Auto-detect finance account based on source (like process_supply does)
                    finance_accounts = await poster_client.get_accounts()
                    finance_account_id = None

                    if source == 'kaspi':
                        # Look for Kaspi account
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'kaspi' in acc_name:
                                finance_account_id = int(acc['account_id'])
                                break
                    elif source == 'halyk':
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'халык' in acc_name or 'halyk' in acc_name:
                                finance_account_id = int(acc['account_id'])
                                break
                    else:
                        # Look for cash/purchase accounts
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'закуп' in acc_name or 'оставил' in acc_name:
                                finance_account_id = int(acc['account_id'])
                                break

                    # Fallback to first account
                    if not finance_account_id and finance_accounts:
                        finance_account_id = int(finance_accounts[0]['account_id'])

                    print(f"💳 Using finance account ID={finance_account_id} for source='{source}' in {account['account_name']}")

                    supply_id = await poster_client.create_supply(
                        supplier_id=supplier_id,
                        storage_id=data.get('storage_id', 1),
                        date=supply_date,
                        ingredients=ingredients,
                        account_id=finance_account_id,
                        comment=data.get('comment', '')
                    )

                    if supply_id:
                        created_supplies.append({
                            'supply_id': supply_id,
                            'account_name': account['account_name'],
                            'items_count': len(account_items)
                        })

                        # Prepare price history records for this account's items
                        for item in account_items:
                            all_price_records.append({
                                'ingredient_id': item['id'],
                                'ingredient_name': item.get('name', ''),
                                'supplier_id': data['supplier_id'],
                                'supplier_name': data['supplier_name'],
                                'price': float(item['price']),
                                'quantity': float(item['quantity']),
                                'unit': item.get('unit', 'шт'),
                                'supply_id': supply_id,
                                'date': data.get('date', datetime.now().strftime('%Y-%m-%d'))
                            })
                    else:
                        print(f"⚠️ Failed to create supply in {account['account_name']}")
                except Exception as e:
                    print(f"⚠️ Error creating supply in {account['account_name']}: {e}")
                finally:
                    await poster_client.close()

        asyncio.run(create_all_supplies())

        if not created_supplies:
            return jsonify({'error': 'Failed to create any supplies in Poster'}), 500

        # Save price history to database
        if all_price_records:
            db.bulk_add_price_history(g.user_id, all_price_records)

        # Return all created supplies
        return jsonify({
            'success': True,
            'supply_id': created_supplies[0]['supply_id'],  # For backwards compatibility
            'supplies': created_supplies
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to create supply: {str(e)}'}), 500


# ========================================
# Shipment Templates API
# ========================================

@app.route('/api/templates')
def get_templates():
    """Get all shipment templates for the user"""
    db = get_database()
    user_id = g.user_id

    templates = db.get_shipment_templates(user_id)

    return jsonify({
        'templates': templates
    })


@app.route('/api/templates', methods=['POST'])
def create_template():
    """Create a new shipment template"""
    db = get_database()
    user_id = g.user_id

    data = request.get_json()

    # Validate required fields
    required_fields = ['template_name', 'supplier_id', 'supplier_name',
                      'account_id', 'account_name', 'items']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    # Validate items format
    if not isinstance(data['items'], list) or len(data['items']) == 0:
        return jsonify({'error': 'items must be a non-empty list'}), 400

    for item in data['items']:
        if not all(k in item for k in ['id', 'name', 'price']):
            return jsonify({'error': 'Each item must have id, name, and price'}), 400

    success = db.create_shipment_template(
        telegram_user_id=user_id,
        template_name=data['template_name'],
        supplier_id=data['supplier_id'],
        supplier_name=data['supplier_name'],
        account_id=data['account_id'],
        account_name=data['account_name'],
        items=data['items'],
        storage_id=data.get('storage_id', 1)
    )

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to create template'}), 400


@app.route('/api/templates/<template_name>', methods=['PUT'])
def update_template(template_name):
    """Update a shipment template"""
    db = get_database()
    user_id = g.user_id

    data = request.get_json()

    # Update template
    success = db.update_shipment_template(
        telegram_user_id=user_id,
        template_name=template_name,
        supplier_id=data.get('supplier_id'),
        supplier_name=data.get('supplier_name'),
        account_id=data.get('account_id'),
        account_name=data.get('account_name'),
        items=data.get('items'),
        storage_id=data.get('storage_id')
    )

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to update template'}), 400


@app.route('/api/templates/<template_name>', methods=['DELETE'])
def delete_template(template_name):
    """Delete a shipment template"""
    db = get_database()
    user_id = g.user_id

    success = db.delete_shipment_template(user_id, template_name)

    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to delete template'}), 400


# ========================================
# Expense Drafts Web Interface
# ========================================

@app.route('/expenses')
def list_expenses():
    """Show expense drafts for user - filter by date (default: today)"""
    from datetime import datetime, timedelta

    db = get_database()
    # Load ALL drafts (not just pending) to show completion status
    drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")

    # Get date from query param or use today (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    today = _kz_now().strftime("%Y-%m-%d")
    selected_date = request.args.get('date', today)

    # Validate date format
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = today

    # Filter drafts by selected date (using created_at date part)
    drafts = [d for d in drafts if d.get('created_at') and str(d['created_at'])[:10] == selected_date]

    # Load shift reconciliation data for selected date
    reconciliation_rows = db.get_shift_reconciliation(TELEGRAM_USER_ID, selected_date)
    # Initialize with default empty structures for all expected sources
    # For kaspi/halyk: use opening_balance to store fact_balance (user-entered actual balance)
    reconciliation = {
        'cash': {'fact_balance': None, 'total_difference': None, 'notes': None},
        'kaspi': {'fact_balance': None, 'total_difference': None, 'notes': None},
        'halyk': {'fact_balance': None, 'total_difference': None, 'notes': None},
    }
    for row in reconciliation_rows:
        source = row['source']
        # For all sources: opening_balance column stores fact_balance
        reconciliation[source] = {
            'fact_balance': row.get('opening_balance'),  # Store fact_balance in opening_balance column
            'total_difference': row.get('total_difference'),
            'notes': row.get('notes'),
        }

    # Load categories, accounts, poster_accounts, and transactions for sync check
    categories = []
    accounts = []  # Finance accounts (Kaspi, Cash, etc.)
    poster_accounts_list = []  # Business accounts (PizzBurg, PizzBurg Cafe)
    poster_transactions = []  # Transactions from Poster for sync check

    try:
        from poster_client import PosterClient
        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

        if poster_accounts:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Build poster_accounts_list for template
            for acc in poster_accounts:
                poster_accounts_list.append({
                    'id': acc['id'],
                    'name': acc['account_name'],
                    'is_primary': acc.get('is_primary', False)
                })

            async def load_data():
                all_categories = []
                all_accounts = []
                all_transactions = []
                seen_category_names = set()  # For deduplication

                # Get date range for transactions (last 30 days)
                date_to = datetime.now()
                date_from = date_to - timedelta(days=30)
                date_from_str = date_from.strftime("%Y%m%d")
                date_to_str = date_to.strftime("%Y%m%d")

                for acc in poster_accounts:
                    client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )
                    try:
                        # Load categories from each account with poster_account info
                        cats = await client.get_categories()
                        print(f"Loaded {len(cats)} categories from {acc['account_name']}")
                        for c in cats:
                            # Only include expense categories (type=1), not income (type=0)
                            cat_type = c.get('type', '1')
                            if str(cat_type) != '1':
                                continue
                            cat_name = c.get('category_name', '').lower()
                            # Add poster account info
                            c['poster_account_id'] = acc['id']
                            c['poster_account_name'] = acc['account_name']
                            # Add all categories (not deduplicated) so user can filter by department
                            all_categories.append(c)
                            print(f"  Category: {c.get('category_name')} (type={cat_type})")

                        # Load finance accounts with poster_account info
                        accs = await client.get_accounts()
                        for a in accs:
                            a['poster_account_id'] = acc['id']
                            a['poster_account_name'] = acc['account_name']
                        all_accounts.extend(accs)

                        # Load transactions for sync check
                        transactions = await client.get_transactions(date_from_str, date_to_str)
                        for t in transactions:
                            t['poster_account_id'] = acc['id']
                            t['poster_account_name'] = acc['account_name']
                        all_transactions.extend(transactions)

                    finally:
                        await client.close()

                return all_categories, all_accounts, all_transactions

            try:
                categories, accounts, poster_transactions = loop.run_until_complete(load_data())
            finally:
                loop.close()
    except Exception as e:
        print(f"Error loading categories/accounts: {e}")
        import traceback
        traceback.print_exc()

    # Sum balances by account type across all business accounts
    # Kaspi Pay PizzBurg + Kaspi Pay PizzBurg-cafe = Total Kaspi
    # Халык банк PizzBurg + Халык банк PizzBurg-cafe = Total Halyk
    # Оставил в кассе PizzBurg + Оставил в кассе PizzBurg-cafe = Total Cash
    account_totals = {
        'kaspi': 0,
        'halyk': 0,
        'cash': 0
    }
    for acc in accounts:
        name_lower = (acc.get('account_name') or acc.get('name', '')).lower()
        # Balance is in kopecks/tiyn, convert to tenge
        balance = float(acc.get('balance') or 0) / 100

        if 'kaspi' in name_lower:
            account_totals['kaspi'] += balance
        elif 'халык' in name_lower or 'halyk' in name_lower:
            account_totals['halyk'] += balance
        elif 'оставил' in name_lower:
            account_totals['cash'] += balance

    print(f"Account totals: {account_totals}")

    return render_template('expenses.html',
                          drafts=drafts,
                          categories=categories,
                          accounts=accounts,
                          poster_accounts=poster_accounts_list,
                          poster_transactions=poster_transactions,
                          selected_date=selected_date,
                          today=today,
                          reconciliation=reconciliation,
                          account_totals=account_totals)


@app.route('/expenses/toggle-type/<int:draft_id>', methods=['POST'])
def toggle_expense_type(draft_id):
    """Toggle expense type between transaction and supply.
    When switching to supply, auto-create a supply draft.
    When switching back to transaction, delete the linked supply draft.
    """
    db = get_database()
    data = request.get_json() or {}
    new_type = data.get('expense_type', 'transaction')

    # Get current expense draft to check linked supply
    all_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")
    expense_draft = next((d for d in all_drafts if d['id'] == draft_id), None)

    if not expense_draft:
        return jsonify({'success': False, 'error': 'Draft not found'})

    supply_draft_id = None

    if new_type == 'supply':
        # Create supply draft linked to this expense
        from datetime import datetime
        supply_draft_id = db.create_empty_supply_draft(
            telegram_user_id=TELEGRAM_USER_ID,
            supplier_name=expense_draft.get('description', ''),
            invoice_date=datetime.now().strftime("%Y-%m-%d"),
            total_sum=expense_draft.get('amount', 0),
            linked_expense_draft_id=draft_id,
            account_id=expense_draft.get('account_id'),
            source=expense_draft.get('source', 'cash')
        )
    else:
        # Switching to transaction - find and delete linked supply draft
        supply_drafts = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")
        linked_supply = next((s for s in supply_drafts if s.get('linked_expense_draft_id') == draft_id), None)
        if linked_supply:
            db.delete_supply_draft(linked_supply['id'])

    success = db.update_expense_draft(draft_id, expense_type=new_type)
    return jsonify({'success': success, 'supply_draft_id': supply_draft_id})


@app.route('/expenses/update/<int:draft_id>', methods=['POST'])
def update_expense(draft_id):
    """Update expense draft fields"""
    db = get_database()
    data = request.get_json() or {}

    # Allowed fields to update
    update_fields = {}

    if 'amount' in data:
        try:
            update_fields['amount'] = float(data['amount'])
        except (ValueError, TypeError):
            pass

    if 'description' in data:
        update_fields['description'] = str(data['description'])[:200]

    if 'category' in data:
        update_fields['category'] = str(data['category'])[:50] if data['category'] else None

    if 'source' in data and data['source'] in ('cash', 'kaspi'):
        update_fields['source'] = data['source']

    if 'account_id' in data:
        update_fields['account_id'] = int(data['account_id']) if data['account_id'] else None

    if 'poster_account_id' in data:
        update_fields['poster_account_id'] = int(data['poster_account_id']) if data['poster_account_id'] else None

    if 'completion_status' in data and data['completion_status'] in ('pending', 'partial', 'completed'):
        update_fields['completion_status'] = data['completion_status']

    if not update_fields:
        return jsonify({'success': False, 'error': 'No fields to update'})

    success = db.update_expense_draft(draft_id, **update_fields)
    return jsonify({'success': success})


@app.route('/api/categories/search')
def search_categories():
    """Search categories for autocomplete"""
    query = request.args.get('q', '').lower().strip()

    if not query:
        return jsonify([])

    try:
        from poster_client import PosterClient
        db = get_database()
        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

        if not poster_accounts:
            return jsonify([])

        account = poster_accounts[0]
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def get_categories():
            client = PosterClient(
                telegram_user_id=TELEGRAM_USER_ID,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )
            try:
                return await client.get_categories()
            finally:
                await client.close()

        try:
            categories = loop.run_until_complete(get_categories())
        finally:
            loop.close()

        # Filter by query
        matches = [
            {'id': c.get('category_id'), 'name': c.get('category_name', '')}
            for c in categories
            if query in c.get('category_name', '').lower()
        ][:10]

        return jsonify(matches)

    except Exception as e:
        print(f"Error searching categories: {e}")
        return jsonify([])


@app.route('/expenses/delete/<int:draft_id>', methods=['POST'])
def delete_expense(draft_id):
    """Delete single expense draft"""
    db = get_database()
    success = db.delete_expense_draft(draft_id)
    return jsonify({'success': success})


@app.route('/expenses/delete', methods=['POST'])
def delete_drafts():
    """Delete selected expense drafts"""
    draft_ids = request.form.getlist('draft_ids', type=int)

    if draft_ids:
        db = get_database()
        deleted = db.delete_expense_drafts_bulk(draft_ids)
        flash(f'Удалено {deleted} черновиков', 'success')

    return redirect(url_for('list_expenses'))


@app.route('/expenses/create', methods=['POST'])
def create_expense():
    """Create empty expense draft for manual entry"""
    db = get_database()
    data = request.get_json() or {}

    # Get default poster_account_id (primary account)
    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)
    default_poster_account_id = None
    if poster_accounts:
        primary = next((a for a in poster_accounts if a.get('is_primary')), poster_accounts[0])
        default_poster_account_id = primary['id']

    amount = data.get('amount', 0)
    description = data.get('description', '')
    expense_type = data.get('expense_type', 'transaction')
    category = data.get('category')
    source = data.get('source', 'cash')
    account_id = data.get('account_id')
    poster_account_id = data.get('poster_account_id', default_poster_account_id)

    draft_id = db.create_expense_draft(
        telegram_user_id=TELEGRAM_USER_ID,
        amount=amount,
        description=description,
        expense_type=expense_type,
        category=category,
        source=source,
        account_id=account_id,
        poster_account_id=poster_account_id
    )

    if draft_id:
        # Return full draft object for dynamic row creation
        return jsonify({
            'success': True,
            'id': draft_id,
            'draft': {
                'id': draft_id,
                'amount': amount,
                'description': description,
                'expense_type': expense_type,
                'category': category,
                'source': source,
                'account_id': account_id,
                'poster_account_id': poster_account_id
            }
        })
    return jsonify({'success': False, 'error': 'Failed to create draft'})


@app.route('/expenses/sync-from-poster', methods=['POST'])
def sync_expenses_from_poster():
    """Sync automatic transactions from Poster to expense drafts"""
    from datetime import datetime, timedelta
    from poster_client import PosterClient

    db = get_database()
    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

    if not poster_accounts:
        return jsonify({'success': False, 'error': 'Нет подключенных аккаунтов Poster'})

    # Get today's date in Kazakhstan timezone (UTC+5)
    kz_tz = KZ_TZ
    today = _kz_now()
    date_str = today.strftime('%Y%m%d')

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fetch_and_sync():
        synced_count = 0
        updated_count = 0
        skipped_count = 0
        deleted_count = 0
        errors = []

        # Track which poster_transaction_ids we see in Poster today
        seen_poster_ids = set()
        synced_account_ids = set()

        # Load all existing drafts once
        existing_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")

        for account in poster_accounts:
            try:
                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # Fetch today's transactions
                    transactions = await client.get_transactions(date_str, date_str)
                    print(f"📅 Fetched {len(transactions)} transactions for {date_str} from {account['account_name']}")

                    # Also fetch finance accounts for mapping account_id -> account name
                    finance_accounts = await client.get_accounts()
                    account_map = {str(acc['account_id']): acc for acc in finance_accounts}

                    # Debug: print account map
                    print(f"📊 Finance accounts for {account['account_name']}:")
                    for acc in finance_accounts:
                        print(f"   - ID {acc.get('account_id')}: {acc.get('account_name') or acc.get('name')}")

                    for txn in transactions:
                        # Accept both expense (type=0) and income (type=1) transactions
                        # Skip transfers (type=2)
                        txn_type = str(txn.get('type'))
                        if txn_type not in ('0', '1'):
                            continue

                        # Get category name early to check for transfers
                        category_name = txn.get('name', '') or txn.get('category_name', '')
                        category_lower = category_name.lower()

                        # Skip system categories that are not real expenses:
                        # - "Переводы" — transfers between accounts (инкассация)
                        # - "Кассовые смены" — shift closing transactions
                        # - "Актуализация" — correcting/actualisation transactions
                        skip_categories = ['перевод', 'кассовые смены', 'актуализац']
                        if any(skip in category_lower for skip in skip_categories):
                            print(f"   ⏭️ Skipping system transaction: category='{category_name}'")
                            continue

                        # Build unique poster_transaction_id
                        txn_id = txn.get('transaction_id')
                        poster_transaction_id = f"{account['id']}_{txn_id}"
                        seen_poster_ids.add(poster_transaction_id)
                        # Also track the simple txn_id format for legacy matching
                        seen_poster_ids.add(str(txn_id))

                        # Extract amount
                        amount_raw = txn.get('amount_from', 0) or txn.get('amount', 0)
                        amount = abs(float(amount_raw)) / 100

                        # Build description from comment or category
                        comment = txn.get('comment', '') or ''
                        description = comment if comment else category_name

                        # Check if already imported — find matching draft
                        # Support both formats: composite "accountId_txnId" and simple "txnId"
                        existing_draft = next(
                            (d for d in existing_drafts
                             if d.get('poster_transaction_id') == poster_transaction_id
                             or (d.get('poster_transaction_id') == str(txn_id) and
                                 d.get('poster_account_id') == account['id'])),
                            None
                        )

                        if existing_draft:
                            # Draft exists — check if amount or description changed in Poster
                            old_poster_amount = existing_draft.get('poster_amount')
                            old_amount = existing_draft.get('amount', 0)
                            old_description = existing_draft.get('description', '')

                            update_fields = {}

                            # Check amount change
                            if old_poster_amount is None or abs(float(old_poster_amount) - amount) >= 0.01:
                                update_fields['poster_amount'] = amount
                                # Update amount if user hasn't manually changed it
                                if old_poster_amount is not None and abs(float(old_amount) - float(old_poster_amount)) < 0.01:
                                    update_fields['amount'] = amount
                                if old_poster_amount is None:
                                    update_fields['amount'] = amount

                            # Check description change
                            if description and description != old_description:
                                update_fields['description'] = description

                            if update_fields:
                                db.update_expense_draft(existing_draft['id'], **update_fields)
                                updated_count += 1
                                print(f"[SYNC] Updated draft #{existing_draft['id']}: {update_fields}", flush=True)
                            else:
                                skipped_count += 1
                            continue

                        # Check if this is a supply transaction that already has a linked expense draft
                        # Poster creates transactions like "Поставка №12685 от «Омск упаковки»" for supplies

                        import re
                        supply_match = re.search(r'Поставка\s*[№N#]\s*(\d+)', description)
                        if supply_match and not existing_draft:
                            supply_num = supply_match.group(1)
                            # Look for expense draft with poster_transaction_id = "supply_12685,..."
                            supply_draft = next(
                                (d for d in existing_drafts
                                 if (d.get('poster_transaction_id') or '').startswith('supply_') and
                                    supply_num in (d.get('poster_transaction_id') or '').replace('supply_', '').split(',')),
                                None
                            )
                            if supply_draft:
                                skipped_count += 1
                                print(f"   ⏭️ Skipping supply transaction #{txn_id}: matched draft #{supply_draft['id']} (supply #{supply_num})")
                                continue

                            # Fallback: if poster_transaction_id link is missing, match by expense_type='supply' + amount
                            supply_amount_draft = next(
                                (d for d in existing_drafts
                                 if d.get('expense_type') == 'supply' and
                                    d.get('status') == 'pending' and
                                    abs(float(d.get('amount', 0)) - amount) < 1),
                                None
                            )
                            if supply_amount_draft:
                                # Link them now so future syncs find it faster
                                db.update_expense_draft(
                                    supply_amount_draft['id'],
                                    poster_transaction_id=f"supply_{supply_num}"
                                )
                                skipped_count += 1
                                print(f"   ⏭️ Skipping supply transaction #{txn_id}: fallback matched draft #{supply_amount_draft['id']} by amount {amount}₸ (linked as supply_{supply_num})")
                                continue

                        # Detect if this is an income transaction by category name
                        is_income = txn_type == '1' or 'приход' in category_lower or 'поступлен' in category_lower

                        if is_income:
                            print(f"   💰 Income detected: category='{category_name}', type={txn_type}")

                        # Determine source (cash/kaspi/halyk) from account name
                        account_from_id = txn.get('account_from_id') or txn.get('account_from')
                        txn_account_name = txn.get('account_name', '') or ''

                        finance_acc = account_map.get(str(account_from_id), {})
                        finance_acc_name = (finance_acc.get('account_name') or finance_acc.get('name') or txn_account_name or '').lower()

                        print(f"   Transaction #{txn_id}: account_from={account_from_id}, acc_name='{finance_acc_name}', desc='{description}'")

                        source = 'cash'
                        if 'kaspi' in finance_acc_name:
                            source = 'kaspi'
                        elif 'халык' in finance_acc_name or 'halyk' in finance_acc_name:
                            source = 'halyk'

                        print(f"   -> source detected: {source}, is_income: {is_income}")

                        # Create draft - mark as 'completed' since it's already in Poster
                        draft_id = db.create_expense_draft(
                            telegram_user_id=TELEGRAM_USER_ID,
                            amount=amount,
                            description=description,
                            expense_type='transaction',
                            category=category_name,
                            source=source,
                            account_id=int(account_from_id) if account_from_id else None,
                            poster_account_id=account['id'],
                            poster_transaction_id=poster_transaction_id,
                            is_income=is_income,
                            completion_status='completed',
                            poster_amount=amount
                        )

                        if draft_id:
                            synced_count += 1
                            txn_type_label = "income" if is_income else "expense"
                            print(f"✅ Synced {txn_type_label} #{txn_id} from {account['account_name']}: {description} - {amount}₸")

                    # Mark account as successfully synced ONLY after all transactions processed
                    synced_account_ids.add(str(account['id']))

                finally:
                    await client.close()

            except Exception as e:
                # Account NOT added to synced_account_ids — orphan detection will skip its drafts
                errors.append(f"{account['account_name']}: {str(e)}")
                print(f"Error syncing from {account['account_name']}: {e}")
                import traceback
                traceback.print_exc()

        # Clean up existing drafts with system categories that should be excluded
        skip_categories_cleanup = ['перевод', 'кассовые смены', 'актуализац']
        for draft in existing_drafts:
            draft_category = (draft.get('category') or '').lower()
            if draft_category and any(skip in draft_category for skip in skip_categories_cleanup):
                db.delete_expense_draft(draft['id'])
                deleted_count += 1
                print(f"[SYNC] Deleted system category draft #{draft['id']}: category='{draft.get('category')}'")

        # Detect and remove drafts whose Poster transactions were deleted
        today_str = today.strftime('%Y-%m-%d')
        for draft in existing_drafts:
            ptid = draft.get('poster_transaction_id', '') or ''
            # Only check drafts synced from Poster (not supply-linked or manual)
            if not ptid or ptid.startswith('supply_'):
                continue
            # Only check pending drafts — don't touch processed ones
            if draft.get('status') != 'pending':
                continue
            # Skip drafts already deleted above (system categories)
            draft_category = (draft.get('category') or '').lower()
            if draft_category and any(skip in draft_category for skip in skip_categories_cleanup):
                continue
            # Only check drafts created today (older drafts won't be in today's Poster fetch)
            draft_created = draft.get('created_at', '')
            if draft_created:
                draft_date = str(draft_created)[:10]  # "2026-02-14 12:00:00" -> "2026-02-14"
                if draft_date != today_str:
                    continue
            else:
                continue
            # Check if this draft's transaction still exists in Poster
            if ptid not in seen_poster_ids:
                # For composite ID format "accountId_txnId", verify the account was synced
                if '_' in ptid:
                    account_part = ptid.split('_')[0]
                    if account_part not in synced_account_ids:
                        continue  # Account wasn't synced (maybe error), don't delete
                db.delete_expense_draft(draft['id'])
                deleted_count += 1
                print(f"[SYNC] Deleted orphan draft #{draft['id']}: poster_txn={ptid} (deleted in Poster)")

        return synced_count, updated_count, skipped_count, deleted_count, errors

    try:
        synced, updated, skipped, deleted, errors = loop.run_until_complete(fetch_and_sync())
    finally:
        loop.close()

    msg_parts = []
    if synced > 0:
        msg_parts.append(f'новых: {synced}')
    if updated > 0:
        msg_parts.append(f'обновлено: {updated}')
    if deleted > 0:
        msg_parts.append(f'удалено: {deleted}')
    if skipped > 0:
        msg_parts.append(f'без изменений: {skipped}')
    message = 'Синхронизация: ' + ', '.join(msg_parts) if msg_parts else 'Нет транзакций'

    return jsonify({
        'success': True,
        'synced': synced,
        'updated': updated,
        'deleted': deleted,
        'skipped': skipped,
        'errors': errors,
        'message': message
    })


@app.route('/api/poster-transactions')
def api_poster_transactions():
    """Get today's transactions from Poster for real-time comparison"""
    from datetime import datetime, timedelta

    db = get_database()
    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

    if not poster_accounts:
        return jsonify({'success': False, 'error': 'No Poster accounts'})

    # Kazakhstan time UTC+5
    kz_tz = KZ_TZ
    today = _kz_now()
    date_str = today.strftime("%Y%m%d")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fetch_all_transactions():
        all_transactions = []

        for account in poster_accounts:
            try:
                from poster_client import PosterClient
                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    transactions = await client.get_transactions(date_str, date_str)
                    finance_accounts = await client.get_accounts()
                    account_map = {str(acc['account_id']): acc for acc in finance_accounts}

                    for txn in transactions:
                        txn_type = str(txn.get('type'))
                        # Skip transfers
                        category_name = txn.get('name', '') or txn.get('category_name', '')
                        if 'перевод' in category_name.lower():
                            continue
                        # Only expenses and income
                        if txn_type not in ('0', '1'):
                            continue

                        txn_id = txn.get('transaction_id')
                        amount_raw = txn.get('amount_from', 0) or txn.get('amount', 0)
                        amount = abs(float(amount_raw)) / 100
                        comment = txn.get('comment', '') or ''
                        description = comment if comment else category_name

                        account_from_id = txn.get('account_from_id') or txn.get('account_from')
                        finance_acc = account_map.get(str(account_from_id), {})
                        finance_acc_name = (finance_acc.get('account_name') or finance_acc.get('name', ''))

                        all_transactions.append({
                            'id': f"{account['id']}_{txn_id}",
                            'poster_account_id': account['id'],
                            'poster_account_name': account['account_name'],
                            'transaction_id': txn_id,
                            'amount': amount,
                            'description': description,
                            'category': category_name,
                            'account_id': account_from_id,
                            'account_name': finance_acc_name,
                            'type': txn_type,
                            'is_income': txn_type == '1'
                        })

                finally:
                    await client.close()

            except Exception as e:
                print(f"Error fetching from {account['account_name']}: {e}")

        return all_transactions

    try:
        transactions = loop.run_until_complete(fetch_all_transactions())

        return jsonify({
            'success': True,
            'transactions': transactions,
            'date': date_str,
            'count': len(transactions)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        loop.close()


# ==================== EXPENSES API ====================

@app.route('/api/expenses')
def api_get_expenses():
    """Get all expense drafts with categories, accounts, and poster transactions for React app"""
    from datetime import datetime, timedelta

    db = get_database()
    # Load ALL drafts (not just pending) to show completion status
    drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")

    # Filter by date - use ?date=YYYY-MM-DD param, default to today (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    filter_date = request.args.get('date')
    if not filter_date:
        filter_date = _kz_now().strftime("%Y-%m-%d")
    drafts = [d for d in drafts if d.get('created_at') and str(d['created_at'])[:10] == filter_date]

    # Load categories, accounts, poster_accounts, and transactions
    categories = []
    accounts = []
    poster_accounts_list = []
    poster_transactions = []

    try:
        from poster_client import PosterClient
        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

        if poster_accounts:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Build poster_accounts_list
            for acc in poster_accounts:
                poster_accounts_list.append({
                    'id': acc['id'],
                    'name': acc['account_name'],
                    'is_primary': acc.get('is_primary', False)
                })

            async def load_data():
                all_categories = []
                all_accounts = []
                all_transactions = []

                # Get date range for transactions (last 30 days)
                date_to = datetime.now()
                date_from = date_to - timedelta(days=30)
                date_from_str = date_from.strftime("%Y%m%d")
                date_to_str = date_to.strftime("%Y%m%d")

                for acc in poster_accounts:
                    client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )
                    try:
                        # Load categories
                        cats = await client.get_categories()
                        for c in cats:
                            cat_type = c.get('type', '1')
                            if str(cat_type) != '1':
                                continue
                            c['poster_account_id'] = acc['id']
                            c['poster_account_name'] = acc['account_name']
                            all_categories.append(c)

                        # Load finance accounts
                        accs = await client.get_accounts()
                        for a in accs:
                            a['poster_account_id'] = acc['id']
                            a['poster_account_name'] = acc['account_name']
                        all_accounts.extend(accs)

                        # Load transactions for sync check
                        transactions = await client.get_transactions(date_from_str, date_to_str)
                        for t in transactions:
                            t['poster_account_id'] = acc['id']
                            t['poster_account_name'] = acc['account_name']
                        all_transactions.extend(transactions)

                    finally:
                        await client.close()

                return all_categories, all_accounts, all_transactions

            try:
                categories, accounts, poster_transactions = loop.run_until_complete(load_data())
            finally:
                loop.close()
    except Exception as e:
        print(f"Error loading expenses data: {e}")
        import traceback
        traceback.print_exc()

    # Calculate account totals (balances) by type
    # Sum balances from "Оставил в кассе" accounts across all business accounts
    account_totals = {
        'kaspi': 0,
        'halyk': 0,
        'cash': 0  # This is the "Оставил в кассе" balance from Poster
    }
    for acc in accounts:
        name_lower = (acc.get('account_name') or acc.get('name', '')).lower()
        # Balance is in kopecks/tiyn, convert to tenge
        balance = float(acc.get('balance') or 0) / 100

        if 'kaspi' in name_lower:
            account_totals['kaspi'] += balance
        elif 'халык' in name_lower or 'halyk' in name_lower:
            account_totals['halyk'] += balance
        elif 'оставил' in name_lower:
            account_totals['cash'] += balance

    return jsonify({
        'drafts': drafts,
        'categories': categories,
        'accounts': accounts,
        'poster_accounts': poster_accounts_list,
        'poster_transactions': poster_transactions,
        'account_totals': account_totals
    })


@app.route('/api/expenses/<int:draft_id>', methods=['PUT'])
def api_update_expense(draft_id):
    """Update an expense draft"""
    db = get_database()
    data = request.get_json() or {}

    # Map frontend field names to database field names
    update_fields = {}
    if 'amount' in data:
        update_fields['amount'] = data['amount']
    if 'description' in data:
        update_fields['description'] = data['description']
    if 'category' in data:
        update_fields['category'] = data['category']
    if 'source' in data:
        update_fields['source'] = data['source']
    if 'account_id' in data:
        update_fields['account_id'] = data['account_id']
    if 'poster_account_id' in data:
        update_fields['poster_account_id'] = data['poster_account_id']
    if 'completion_status' in data:
        update_fields['completion_status'] = data['completion_status']

    success = db.update_expense_draft(draft_id, **update_fields)
    return jsonify({'success': success})


@app.route('/api/expenses/<int:draft_id>', methods=['DELETE'])
def api_delete_expense(draft_id):
    """Delete an expense draft"""
    db = get_database()
    success = db.delete_expense_draft(draft_id)
    return jsonify({'success': success})


@app.route('/api/expenses', methods=['POST'])
def api_create_expense():
    """Create a new expense draft"""
    db = get_database()
    data = request.get_json() or {}

    draft_id = db.create_expense_draft(
        telegram_user_id=TELEGRAM_USER_ID,
        amount=data.get('amount', 0),
        description=data.get('description', ''),
        expense_type=data.get('expense_type', 'transaction'),
        category=data.get('category'),
        source=data.get('source', 'cash'),
        account_id=data.get('account_id'),
        poster_account_id=data.get('poster_account_id')
    )

    return jsonify({'success': True, 'id': draft_id})


@app.route('/api/expenses/<int:draft_id>/toggle-type', methods=['POST'])
def api_toggle_expense_type(draft_id):
    """Toggle expense type between transaction and supply"""
    db = get_database()
    data = request.get_json() or {}
    new_type = data.get('expense_type', 'transaction')

    # Get current expense draft
    all_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")
    expense_draft = next((d for d in all_drafts if d['id'] == draft_id), None)

    if not expense_draft:
        return jsonify({'success': False, 'error': 'Draft not found'})

    supply_draft_id = None

    if new_type == 'supply':
        # Create supply draft linked to this expense
        from datetime import datetime
        supply_draft_id = db.create_empty_supply_draft(
            telegram_user_id=TELEGRAM_USER_ID,
            supplier_name=expense_draft.get('description', ''),
            invoice_date=datetime.now().strftime("%Y-%m-%d"),
            total_sum=expense_draft.get('amount', 0),
            linked_expense_draft_id=draft_id,
            account_id=expense_draft.get('account_id'),
            source=expense_draft.get('source', 'cash')
        )
    else:
        # Switching to transaction - find and delete linked supply draft
        supply_drafts = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")
        linked_supply = next((s for s in supply_drafts if s.get('linked_expense_draft_id') == draft_id), None)
        if linked_supply:
            db.delete_supply_draft(linked_supply['id'])

    success = db.update_expense_draft(draft_id, expense_type=new_type)
    return jsonify({'success': success, 'supply_draft_id': supply_draft_id})


@app.route('/api/expenses/<int:draft_id>/completion-status', methods=['POST'])
def api_update_completion_status(draft_id):
    """Update completion status of expense draft"""
    db = get_database()
    data = request.get_json() or {}
    status = data.get('completion_status', 'pending')

    success = db.update_expense_draft(draft_id, completion_status=status)
    return jsonify({'success': success})


# ==================== SHIFT RECONCILIATION API ====================

@app.route('/api/shift-reconciliation')
def api_get_shift_reconciliation():
    """Get shift reconciliation data for a specific date"""
    from datetime import datetime, timedelta

    db = get_database()

    # Default to today (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    date = request.args.get('date')
    if not date:
        date = _kz_now().strftime("%Y-%m-%d")

    rows = db.get_shift_reconciliation(TELEGRAM_USER_ID, date)

    # Convert to dict keyed by source for easy frontend access
    # For all sources: opening_balance column stores fact_balance
    reconciliation = {}
    for row in rows:
        source = row['source']
        reconciliation[source] = {
            'fact_balance': row.get('opening_balance'),  # Store fact_balance in opening_balance column
            'total_difference': row.get('total_difference'),
            'notes': row.get('notes'),
        }

    return jsonify({
        'date': date,
        'reconciliation': reconciliation
    })


@app.route('/api/shift-reconciliation', methods=['POST'])
def api_save_shift_reconciliation():
    """Save shift reconciliation data for a specific date and source"""
    from datetime import datetime, timedelta

    db = get_database()
    data = request.get_json() or {}

    # Default to today (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    date = data.get('date')
    if not date:
        date = _kz_now().strftime("%Y-%m-%d")

    source = data.get('source')
    if not source:
        return jsonify({'success': False, 'error': 'source is required'}), 400

    # For kaspi/halyk: fact_balance is stored in opening_balance column
    opening_balance = data.get('opening_balance')
    if data.get('fact_balance') is not None:
        opening_balance = data.get('fact_balance')

    success = db.save_shift_reconciliation(
        telegram_user_id=TELEGRAM_USER_ID,
        date=date,
        source=source,
        opening_balance=opening_balance,
        closing_balance=data.get('closing_balance'),
        total_difference=data.get('total_difference'),
        notes=data.get('notes'),
    )

    return jsonify({'success': success})


@app.route('/api/expenses/sync-from-poster', methods=['POST'])
def api_sync_expenses_from_poster():
    """Sync expenses from Poster - API version"""
    from datetime import datetime, timedelta

    db = get_database()
    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

    if not poster_accounts:
        return jsonify({'success': False, 'error': 'No Poster accounts', 'synced': 0, 'skipped': 0, 'errors': []})

    # Kazakhstan time UTC+5
    kz_tz = KZ_TZ
    today = _kz_now()
    date_str = today.strftime("%Y%m%d")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    synced = 0
    updated = 0
    skipped = 0
    deleted = 0
    errors = []

    async def sync_from_all_accounts():
        nonlocal synced, updated, skipped, deleted, errors

        # Track which poster_transaction_ids we see in Poster today
        seen_poster_ids = set()
        synced_account_ids = set()

        # Load all existing drafts once (not per-transaction)
        existing_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")

        for account in poster_accounts:
            try:
                from poster_client import PosterClient
                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    transactions = await client.get_transactions(date_str, date_str)
                    finance_accounts = await client.get_accounts()
                    account_map = {str(acc['account_id']): acc for acc in finance_accounts}

                    # Debug: log finance accounts
                    print(f"[SYNC DEBUG] Finance accounts for {account['account_name']}:", flush=True)
                    for acc in finance_accounts:
                        print(f"  - account_id={acc.get('account_id')}, name='{acc.get('account_name') or acc.get('name')}'", flush=True)

                    for idx, txn in enumerate(transactions):
                        # Debug: log first transaction structure
                        if idx == 0:
                            print(f"[SYNC DEBUG] First transaction keys: {list(txn.keys())}", flush=True)
                            print(f"[SYNC DEBUG] First transaction: {txn}", flush=True)

                        txn_type = str(txn.get('type'))
                        category_name = txn.get('name', '') or txn.get('category_name', '')

                        # Skip system categories (transfers, shift closing, actualisation)
                        category_lower = category_name.lower()
                        skip_categories = ['перевод', 'кассовые смены', 'актуализац']
                        if any(skip in category_lower for skip in skip_categories):
                            continue

                        # Only expenses and income
                        if txn_type not in ('0', '1'):
                            continue

                        txn_id = txn.get('transaction_id')
                        amount_raw = txn.get('amount_from', 0) or txn.get('amount', 0)
                        amount = abs(float(amount_raw)) / 100
                        comment = txn.get('comment', '') or ''
                        description = comment if comment else category_name

                        # Try multiple field names for account ID
                        account_from_id = (
                            txn.get('account_from_id') or
                            txn.get('account_from') or
                            txn.get('account_id') or
                            txn.get('account')
                        )
                        # Also check direct account_name field
                        txn_account_name = txn.get('account_name', '') or ''

                        finance_acc = account_map.get(str(account_from_id), {})
                        # Use finance account name or fall back to direct txn account_name
                        finance_acc_name_raw = (finance_acc.get('account_name') or finance_acc.get('name')) or txn_account_name

                        # Debug: log account lookup
                        print(f"[SYNC DEBUG] txn={txn_id}, account_from_id={account_from_id}, txn_account_name='{txn_account_name}', found_acc='{finance_acc.get('account_name') or finance_acc.get('name', 'NOT FOUND')}'", flush=True)

                        # Check if already synced - find matching draft
                        # Support both formats: composite "accountId_txnId" and simple "txnId"
                        composite_txn_id = f"{account['id']}_{txn_id}"
                        seen_poster_ids.add(composite_txn_id)
                        seen_poster_ids.add(str(txn_id))
                        existing_draft = next(
                            (d for d in existing_drafts
                             if d.get('poster_transaction_id') == composite_txn_id
                             or (d.get('poster_transaction_id') == str(txn_id) and
                                 d.get('poster_account_id') == account['id'])),
                            None
                        )

                        if existing_draft:
                            # Draft exists — check if amount or description changed in Poster
                            old_poster_amount = existing_draft.get('poster_amount')
                            old_amount = existing_draft.get('amount', 0)
                            old_description = existing_draft.get('description', '')

                            update_fields = {}

                            # Check amount change
                            if old_poster_amount is None or abs(float(old_poster_amount) - amount) >= 0.01:
                                update_fields['poster_amount'] = amount
                                # Update amount if user hasn't manually changed it
                                if old_poster_amount is not None and abs(float(old_amount) - float(old_poster_amount)) < 0.01:
                                    update_fields['amount'] = amount
                                if old_poster_amount is None:
                                    update_fields['amount'] = amount

                            # Check description change
                            if description and description != old_description:
                                update_fields['description'] = description

                            if update_fields:
                                db.update_expense_draft(existing_draft['id'], **update_fields)
                                updated += 1
                                print(f"[SYNC] Updated draft #{existing_draft['id']}: {update_fields}", flush=True)
                            else:
                                skipped += 1
                            continue

                        # Check if this is a supply transaction that already has a linked expense draft
                        import re
                        supply_match = re.search(r'Поставка\s*[№N#]\s*(\d+)', description)
                        if supply_match and not existing_draft:
                            supply_num = supply_match.group(1)
                            supply_draft = next(
                                (d for d in existing_drafts
                                 if (d.get('poster_transaction_id') or '').startswith('supply_') and
                                    supply_num in (d.get('poster_transaction_id') or '').replace('supply_', '').split(',')),
                                None
                            )
                            if supply_draft:
                                skipped += 1
                                print(f"   ⏭️ Skipping supply transaction #{txn_id}: matched draft #{supply_draft['id']} (supply #{supply_num})", flush=True)
                                continue

                            # Fallback: if poster_transaction_id link is missing, match by expense_type='supply' + amount
                            supply_amount_draft = next(
                                (d for d in existing_drafts
                                 if d.get('expense_type') == 'supply' and
                                    d.get('status') == 'pending' and
                                    abs(float(d.get('amount', 0)) - amount) < 1),
                                None
                            )
                            if supply_amount_draft:
                                # Link them now so future syncs find it faster
                                db.update_expense_draft(
                                    supply_amount_draft['id'],
                                    poster_transaction_id=f"supply_{supply_num}"
                                )
                                skipped += 1
                                print(f"   ⏭️ Skipping supply transaction #{txn_id}: fallback matched draft #{supply_amount_draft['id']} by amount {amount}₸ (linked as supply_{supply_num})", flush=True)
                                continue

                        # Determine source from finance account name (or direct txn account_name)
                        finance_acc_name = finance_acc_name_raw.lower() if finance_acc_name_raw else ''
                        source = 'cash'
                        if 'kaspi' in finance_acc_name:
                            source = 'kaspi'
                        elif 'халык' in finance_acc_name or 'halyk' in finance_acc_name:
                            source = 'halyk'

                        print(f"[SYNC DEBUG] txn={txn_id}, finance_acc_name='{finance_acc_name}', source='{source}'", flush=True)

                        # Create expense draft
                        db.create_expense_draft(
                            telegram_user_id=TELEGRAM_USER_ID,
                            amount=amount,
                            description=description,
                            expense_type='transaction',
                            category=category_name,
                            source=source,
                            account_id=finance_acc.get('account_id'),
                            poster_account_id=account['id'],
                            poster_transaction_id=composite_txn_id,
                            is_income=(txn_type == '1'),
                            completion_status='completed',
                            poster_amount=amount
                        )
                        synced += 1

                    # Mark account as successfully synced ONLY after all transactions processed
                    synced_account_ids.add(str(account['id']))

                finally:
                    await client.close()

            except Exception as e:
                # Account NOT added to synced_account_ids — orphan detection will skip its drafts
                errors.append(f"{account['account_name']}: {str(e)}")

        # Clean up existing drafts with system categories that should be excluded
        skip_categories_cleanup = ['перевод', 'кассовые смены', 'актуализац']
        for draft in existing_drafts:
            draft_category = (draft.get('category') or '').lower()
            if draft_category and any(skip in draft_category for skip in skip_categories_cleanup):
                db.delete_expense_draft(draft['id'])
                deleted += 1
                print(f"[SYNC] Deleted system category draft #{draft['id']}: category='{draft.get('category')}'")

        # Detect and remove drafts whose Poster transactions were deleted
        today_str = today.strftime('%Y-%m-%d')
        for draft in existing_drafts:
            ptid = draft.get('poster_transaction_id', '') or ''
            if not ptid or ptid.startswith('supply_'):
                continue
            if draft.get('status') != 'pending':
                continue
            # Skip drafts already deleted above (system categories)
            draft_category = (draft.get('category') or '').lower()
            if draft_category and any(skip in draft_category for skip in skip_categories_cleanup):
                continue
            draft_created = draft.get('created_at', '')
            if draft_created:
                draft_date = str(draft_created)[:10]
                if draft_date != today_str:
                    continue
            else:
                continue
            if ptid not in seen_poster_ids:
                if '_' in ptid:
                    account_part = ptid.split('_')[0]
                    if account_part not in synced_account_ids:
                        continue
                db.delete_expense_draft(draft['id'])
                deleted += 1
                print(f"[SYNC] Deleted orphan draft #{draft['id']}: poster_txn={ptid} (deleted in Poster)")

    try:
        loop.run_until_complete(sync_from_all_accounts())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'synced': synced, 'updated': updated, 'deleted': deleted, 'skipped': skipped, 'errors': errors})
    finally:
        loop.close()

    msg_parts = []
    if synced > 0:
        msg_parts.append(f'новых: {synced}')
    if updated > 0:
        msg_parts.append(f'обновлено: {updated}')
    if deleted > 0:
        msg_parts.append(f'удалено: {deleted}')
    if skipped > 0:
        msg_parts.append(f'без изменений: {skipped}')
    message = 'Синхронизация: ' + ', '.join(msg_parts) if msg_parts else 'Нет транзакций'

    return jsonify({
        'success': True,
        'synced': synced,
        'updated': updated,
        'deleted': deleted,
        'skipped': skipped,
        'errors': errors,
        'message': message
    })


@app.route('/api/expenses/process', methods=['POST'])
def api_process_expenses():
    """Process selected expense drafts - create transactions in Poster"""
    db = get_database()
    data = request.get_json() or {}
    draft_ids = data.get('draft_ids', [])

    if not draft_ids:
        return jsonify({'success': False, 'error': 'No drafts selected'})

    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)
    if not poster_accounts:
        return jsonify({'success': False, 'error': 'No Poster accounts'})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    created = 0
    errors = []

    async def process_drafts():
        nonlocal created, errors

        all_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")
        drafts_to_process = [d for d in all_drafts if d['id'] in draft_ids]

        for draft in drafts_to_process:
            try:
                # Find the poster account
                poster_account_id = draft.get('poster_account_id')
                account = next((a for a in poster_accounts if a['id'] == poster_account_id), poster_accounts[0])

                from poster_client import PosterClient
                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # Auto-detect finance account based on source + this Poster account
                    finance_accounts = await client.get_accounts()
                    account_id = None

                    if draft.get('source') == 'kaspi':
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'kaspi' in acc_name:
                                account_id = int(acc['account_id'])
                                break
                    elif draft.get('source') == 'halyk':
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'халык' in acc_name or 'halyk' in acc_name:
                                account_id = int(acc['account_id'])
                                break
                    else:
                        for acc in finance_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'закуп' in acc_name or 'оставил' in acc_name:
                                account_id = int(acc['account_id'])
                                break

                    if not account_id and finance_accounts:
                        account_id = int(finance_accounts[0]['account_id'])

                    # Create transaction in Poster
                    is_income = bool(draft.get('is_income'))
                    txn_type = 1 if is_income else 0
                    new_txn_id = await client.create_transaction(
                        transaction_type=txn_type,
                        category_id=1,  # default category
                        account_from_id=account_id or 1,
                        amount=int(draft['amount']),
                        comment=draft.get('description', '')
                    )

                    if new_txn_id:
                        # Store poster_transaction_id and mark as completed
                        db.update_expense_draft(
                            draft['id'],
                            completion_status='completed',
                            poster_transaction_id=f"{account['id']}_{new_txn_id}",
                            poster_amount=draft['amount']
                        )
                        created += 1
                    else:
                        errors.append(f"Draft {draft['id']}: Transaction creation returned no ID")

                finally:
                    await client.close()

            except Exception as e:
                errors.append(f"Draft {draft['id']}: {str(e)}")

    try:
        loop.run_until_complete(process_drafts())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'created': created, 'errors': errors})
    finally:
        loop.close()

    return jsonify({
        'success': True,
        'created': created,
        'errors': errors,
        'message': f'Создано транзакций: {created}'
    })


# ==================== SUPPLY DRAFTS API ====================

@app.route('/api/supply-drafts')
def api_get_supply_drafts():
    """Get all supply drafts with items for React app (today only)"""
    from datetime import datetime, timedelta

    db = get_database()
    drafts_raw = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")

    # Filter to only today's drafts (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    today = _kz_now().strftime("%Y-%m-%d")

    # Load items for each draft and linked expense amount
    drafts = []
    for draft_raw in drafts_raw:
        # Filter by today's date (convert created_at from UTC to Kazakhstan time)
        created_date = get_date_in_kz_tz(draft_raw.get('created_at'), kz_tz)
        if created_date != today:
            continue

        draft = db.get_supply_draft_with_items(draft_raw['id'])
        if draft:
            # Map database column names to frontend expected names
            if 'items' in draft:
                for item in draft['items']:
                    # Map poster_ingredient_id -> ingredient_id
                    if 'poster_ingredient_id' in item and 'ingredient_id' not in item:
                        item['ingredient_id'] = item['poster_ingredient_id']
                    # Map poster_ingredient_name or item_name -> ingredient_name
                    if 'ingredient_name' not in item:
                        item['ingredient_name'] = item.get('poster_ingredient_name') or item.get('item_name', '')
                    # Map price_per_unit -> price
                    if 'price_per_unit' in item and 'price' not in item:
                        item['price'] = item['price_per_unit']
            # Get linked expense info if available
            if draft.get('linked_expense_draft_id'):
                expense = db.get_expense_draft(draft['linked_expense_draft_id'])
                if expense:
                    draft['linked_expense_amount'] = expense.get('amount', 0)
                    draft['linked_expense_source'] = expense.get('source', 'cash')
            # Ensure source has a default value
            if not draft.get('source'):
                draft['source'] = 'cash'
            drafts.append(draft)

    # Get pending expense items of type 'supply' for linking
    pending_supplies = db.get_pending_supply_items(TELEGRAM_USER_ID)

    # Get poster accounts
    poster_accounts_list = []
    try:
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        if accounts:
            for acc in accounts:
                poster_accounts_list.append({
                    'id': acc['id'],
                    'name': acc['account_name'],
                    'is_primary': acc.get('is_primary', False)
                })
    except Exception as e:
        print(f"Error loading poster accounts: {e}")

    return jsonify({
        'drafts': drafts,
        'pending_supplies': pending_supplies,
        'poster_accounts': poster_accounts_list
    })


@app.route('/api/supply-drafts', methods=['POST'])
def api_create_supply_draft():
    """Create a new empty supply draft"""
    from datetime import datetime
    db = get_database()
    data = request.get_json() or {}

    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TELEGRAM_USER_ID,
        supplier_name=data.get('supplier_name', ''),
        invoice_date=data.get('invoice_date') or datetime.now().strftime("%Y-%m-%d"),
        total_sum=0,
        linked_expense_draft_id=data.get('linked_expense_draft_id'),
        poster_account_id=data.get('poster_account_id'),
        source=data.get('source', 'cash')
    )

    if draft_id:
        return jsonify({'success': True, 'id': draft_id})
    return jsonify({'success': False, 'error': 'Failed to create draft'}), 500


@app.route('/api/supply-drafts/<int:draft_id>', methods=['PUT'])
def api_update_supply_draft(draft_id):
    """Update a supply draft"""
    db = get_database()
    data = request.get_json() or {}

    updates = {}
    if 'supplier_name' in data:
        updates['supplier_name'] = data['supplier_name']
    if 'poster_account_id' in data:
        updates['poster_account_id'] = data['poster_account_id']
    if 'linked_expense_draft_id' in data:
        updates['linked_expense_draft_id'] = data['linked_expense_draft_id']
    if 'invoice_date' in data:
        updates['invoice_date'] = data['invoice_date']
    if 'source' in data:
        updates['source'] = data['source']

    if updates:
        db.update_supply_draft(draft_id, **updates)

    return jsonify({'success': True})


@app.route('/api/supply-drafts/<int:draft_id>', methods=['DELETE'])
def api_delete_supply_draft(draft_id):
    """Delete a supply draft"""
    db = get_database()
    db.delete_supply_draft(draft_id)
    return jsonify({'success': True})


@app.route('/api/supply-drafts/<int:draft_id>/items', methods=['POST'])
def api_add_supply_draft_item(draft_id):
    """Add item to supply draft"""
    db = get_database()
    data = request.get_json() or {}

    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        poster_ingredient_id=data.get('ingredient_id'),
        poster_ingredient_name=data.get('ingredient_name', ''),
        item_name=data.get('ingredient_name', ''),
        quantity=data.get('quantity', 1),
        price_per_unit=data.get('price', 0),
        unit=data.get('unit', 'шт'),
        poster_account_id=data.get('poster_account_id'),
        item_type=data.get('item_type', 'ingredient'),
        storage_id=data.get('storage_id'),
        storage_name=data.get('storage_name')
    )

    return jsonify({'success': True, 'id': item_id})


@app.route('/api/supply-drafts/items/<int:item_id>', methods=['PUT'])
def api_update_supply_draft_item(item_id):
    """Update supply draft item"""
    db = get_database()
    data = request.get_json() or {}

    # Map frontend field names to database column names
    field_mapping = {
        'ingredient_id': 'poster_ingredient_id',
        'ingredient_name': 'poster_ingredient_name',
        'price': 'price_per_unit',
        'quantity': 'quantity',
        'unit': 'unit',
        'poster_account_id': 'poster_account_id',
        'poster_account_name': 'poster_account_name',
    }

    updates = {}
    for frontend_field, db_field in field_mapping.items():
        if frontend_field in data:
            updates[db_field] = data[frontend_field]

    # Also update item_name when ingredient_name changes
    if 'ingredient_name' in data:
        updates['item_name'] = data['ingredient_name']

    if updates:
        db.update_supply_draft_item(item_id, **updates)

    return jsonify({'success': True})


@app.route('/api/supply-drafts/items/<int:item_id>', methods=['DELETE'])
def api_delete_supply_draft_item(item_id):
    """Delete supply draft item"""
    db = get_database()
    db.delete_supply_draft_item(item_id)
    return jsonify({'success': True})


@app.route('/api/supply-drafts/<int:draft_id>/create', methods=['POST'])
def api_create_supply_in_poster(draft_id):
    """Create supply in Poster from draft"""
    db = get_database()
    draft = db.get_supply_draft_with_items(draft_id)

    if not draft:
        return jsonify({'success': False, 'error': 'Draft not found'})

    if not draft.get('items'):
        return jsonify({'success': False, 'error': 'No items in draft'})

    poster_accounts = db.get_accounts(TELEGRAM_USER_ID)
    if not poster_accounts:
        return jsonify({'success': False, 'error': 'No Poster accounts'})

    # Get target account
    poster_account_id = draft.get('poster_account_id')
    account = None
    if poster_account_id:
        account = next((a for a in poster_accounts if a['id'] == poster_account_id), None)
    if not account:
        account = next((a for a in poster_accounts if a.get('is_primary')), poster_accounts[0])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        from poster_client import PosterClient
        client = PosterClient(
            telegram_user_id=TELEGRAM_USER_ID,
            poster_token=account['poster_token'],
            poster_user_id=account['poster_user_id'],
            poster_base_url=account['poster_base_url']
        )

        async def create_supply():
            try:
                # Format items for Poster
                items = []
                for item in draft['items']:
                    items.append({
                        'ingredient_id': item.get('poster_ingredient_id') or item.get('ingredient_id'),
                        'count': item['quantity'],
                        'price': item.get('price_per_unit') or item.get('price', 0)
                    })

                supply_data = {
                    'supplier_name': draft.get('supplier_name', 'Поставщик'),
                    'items': items
                }

                result = await client.create_supply(supply_data)
                return result
            finally:
                await client.close()

        try:
            result = loop.run_until_complete(create_supply())
        finally:
            loop.close()

        if result.get('success'):
            # Mark draft as processed
            db.update_supply_draft(draft_id, status='processed')
            return jsonify({'success': True, 'supply_id': result.get('supply_id')})
        else:
            return jsonify({'success': False, 'error': result.get('error', 'Unknown error')})

    except Exception as e:
        loop.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/expenses/process', methods=['POST'])
def process_drafts():
    """Process selected drafts - create transactions in Poster (multi-account support)"""
    draft_ids = request.form.getlist('draft_ids', type=int)

    if not draft_ids:
        flash('Выберите черновики для обработки', 'warning')
        return redirect(url_for('list_expenses'))

    db = get_database()

    # Get drafts
    all_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="pending")
    selected_drafts = [d for d in all_drafts if d['id'] in draft_ids]

    # Filter only transactions (not supplies)
    transactions = [d for d in selected_drafts if d['expense_type'] == 'transaction']

    if not transactions:
        flash('Нет транзакций для создания (только поставки)', 'warning')
        return redirect(url_for('list_expenses'))

    # Create transactions in Poster
    try:
        from poster_client import PosterClient
        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)

        if not poster_accounts:
            flash('Нет подключенных аккаунтов Poster', 'error')
            return redirect(url_for('list_expenses'))

        # Build account lookup by id
        accounts_by_id = {acc['id']: acc for acc in poster_accounts}

        # Get primary account for defaults
        primary_account = next((a for a in poster_accounts if a.get('is_primary')), poster_accounts[0])

        # Group transactions by poster_account_id
        transactions_by_account = {}
        for t in transactions:
            acc_id = t.get('poster_account_id') or primary_account['id']
            if acc_id not in transactions_by_account:
                transactions_by_account[acc_id] = []
            transactions_by_account[acc_id].append(t)

        # Run async code in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create_all_transactions():
            total_success = 0
            all_processed_ids = []

            for poster_account_id, account_transactions in transactions_by_account.items():
                account = accounts_by_id.get(poster_account_id, primary_account)

                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    finance_accounts = await client.get_accounts()
                    categories = await client.get_categories()

                    # Debug: print all categories from Poster
                    print(f"[DEBUG] Categories from Poster for {account['account_name']}:", flush=True)
                    for cat in categories:
                        print(f"  - id={cat.get('category_id')}, name='{cat.get('category_name')}', raw={cat}", flush=True)

                    # Build category map (name -> id)
                    category_map = {}
                    for cat in categories:
                        cat_name = cat.get('category_name', '') or cat.get('name', '')
                        if cat_name:
                            category_map[cat_name.lower()] = int(cat.get('category_id', 1))

                    print(f"[DEBUG] Category map: {category_map}", flush=True)

                    # Define default category priority
                    default_categories = ['хозяйственные расходы', 'прочее', 'единовременный расход']
                    default_cat_id = 1
                    for default_name in default_categories:
                        if default_name in category_map:
                            default_cat_id = category_map[default_name]
                            break
                    if not default_cat_id and category_map:
                        default_cat_id = list(category_map.values())[0]

                    # Debug: log available finance accounts for this Poster account
                    print(f"[DEBUG] Finance accounts for {account['account_name']}:", flush=True)
                    for acc in finance_accounts:
                        acc_name = (acc.get('account_name') or acc.get('name', ''))
                        print(f"  - id={acc.get('account_id')}, name='{acc_name}'", flush=True)

                    for draft in account_transactions:
                        # Always auto-detect finance account based on source + this Poster account's finance accounts.
                        # Don't use draft's stored account_id because finance account IDs differ between
                        # Poster accounts (e.g., id=4 is "Оставил в кассе" in Pizzburg but "Деньги дома" in Cafe).
                        account_id = None

                        if draft['source'] == 'kaspi':
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'kaspi' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break
                        elif draft['source'] == 'halyk':
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'халык' in acc_name or 'halyk' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break
                        else:
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'закуп' in acc_name or 'оставил' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break

                        if not account_id and finance_accounts:
                            account_id = int(finance_accounts[0]['account_id'])

                        print(f"[DEBUG] Draft '{draft.get('description')}' source='{draft['source']}' -> account_id={account_id}", flush=True)

                        # Find category: exact match, partial match, or default
                        draft_category = (draft.get('category') or '').lower().strip()
                        cat_id = None

                        print(f"[DEBUG] Looking for category: draft_category='{draft_category}', in_map={draft_category in category_map}", flush=True)

                        # 1. Exact match
                        if draft_category in category_map:
                            cat_id = category_map[draft_category]
                            print(f"[DEBUG] Exact match found: {cat_id}", flush=True)

                        # 2. Partial match (draft category contains Poster category or vice versa)
                        if not cat_id:
                            for poster_cat_name, poster_cat_id in category_map.items():
                                if draft_category in poster_cat_name or poster_cat_name in draft_category:
                                    cat_id = poster_cat_id
                                    break

                        # 3. Smart mapping based on common keywords
                        if not cat_id and draft_category:
                            keyword_mapping = {
                                ('зарплата', 'зп', 'аванс', 'оклад'): 'зарплата',
                                ('доставка', 'логистика', 'курьер', 'транспорт'): 'логистика',
                                ('маркетинг', 'реклама', 'продвижение'): 'маркетинг',
                                ('аренда', 'офис', 'помещение'): 'аренда',
                                ('коммуналка', 'свет', 'вода', 'газ', 'отопление'): 'коммунальные',
                                ('банк', 'комиссия', 'эквайринг'): 'банковские',
                                ('уборка', 'мыло', 'моющ', 'хоз', 'расход'): 'хозяйственные расходы',
                            }
                            for keywords, target_cat in keyword_mapping.items():
                                if any(kw in draft_category for kw in keywords):
                                    for poster_cat_name, poster_cat_id in category_map.items():
                                        if target_cat in poster_cat_name:
                                            cat_id = poster_cat_id
                                            break
                                    break

                        # 4. Default fallback
                        if not cat_id:
                            cat_id = default_cat_id

                        print(f"[CATEGORY] draft='{draft.get('category')}' -> cat_id={cat_id}")

                        try:
                            # Check if this draft was synced from Poster (has poster_transaction_id)
                            poster_txn_id = draft.get('poster_transaction_id')
                            if poster_txn_id:
                                # Format: "account_id_transaction_id" - extract the actual transaction_id
                                parts = poster_txn_id.split('_')
                                if len(parts) >= 2:
                                    original_txn_id = int(parts[-1])
                                    # Update existing transaction instead of creating new
                                    await client.update_transaction(
                                        transaction_id=original_txn_id,
                                        amount=int(draft['amount']),
                                        comment=draft['description'],
                                        category_id=cat_id
                                    )
                                    total_success += 1
                                    all_processed_ids.append(draft['id'])
                                    print(f"✅ Updated transaction #{original_txn_id} in {account['account_name']}: {draft['description']} - {draft['amount']}₸")
                                else:
                                    raise Exception(f"Invalid poster_transaction_id format: {poster_txn_id}")
                            else:
                                # Determine transaction type: 0 = expense, 1 = income
                                is_income = bool(draft.get('is_income'))
                                txn_type = 1 if is_income else 0

                                # Create new transaction
                                new_txn_id = await client.create_transaction(
                                    transaction_type=txn_type,
                                    category_id=cat_id,
                                    account_from_id=account_id,
                                    amount=int(draft['amount']),
                                    comment=draft['description']
                                )
                                # Store poster_transaction_id so sync won't create duplicates
                                if new_txn_id:
                                    db.update_expense_draft(
                                        draft['id'],
                                        poster_transaction_id=f"{poster_account_id}_{new_txn_id}",
                                        poster_amount=draft['amount']
                                    )
                                total_success += 1
                                all_processed_ids.append(draft['id'])
                                type_label = "доход" if is_income else "расход"
                                print(f"✅ Created {type_label} in {account['account_name']}: {draft['description']} - {draft['amount']}₸")
                        except Exception as e:
                            print(f"Error processing transaction in {account['account_name']}: {e}")

                finally:
                    await client.close()

            return total_success, all_processed_ids

        try:
            success, processed_ids = loop.run_until_complete(create_all_transactions())
        finally:
            loop.close()

        # Mark as in Poster (stay visible with green status)
        if processed_ids:
            db.mark_drafts_in_poster(processed_ids)

        flash(f'Создано {success} транзакций в Poster ✅', 'success')

    except Exception as e:
        flash(f'Ошибка: {str(e)}', 'error')
        import traceback
        traceback.print_exc()

    return redirect(url_for('list_expenses'))


# ========================================
# Supply Drafts Web Interface
# ========================================

@app.route('/supplies')
def list_supplies():
    """Show supply drafts for user with ingredients for search - only today's"""
    from datetime import datetime, timedelta

    db = get_database()
    drafts_raw = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")

    # Filter to only today's drafts (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    today = _kz_now().strftime("%Y-%m-%d")

    # Load items for each draft and linked expense amount
    drafts = []
    for draft_raw in drafts_raw:
        # Filter by today's date (convert created_at from UTC to Kazakhstan time)
        created_date = get_date_in_kz_tz(draft_raw.get('created_at'), kz_tz)
        if created_date != today:
            continue

        draft = db.get_supply_draft_with_items(draft_raw['id'])
        if draft:
            # Map database column names to frontend expected names
            if 'items' in draft:
                for item in draft['items']:
                    if 'poster_ingredient_id' in item and 'ingredient_id' not in item:
                        item['ingredient_id'] = item['poster_ingredient_id']
                    if 'ingredient_name' not in item:
                        item['ingredient_name'] = item.get('poster_ingredient_name') or item.get('item_name', '')
                    if 'price_per_unit' in item and 'price' not in item:
                        item['price'] = item['price_per_unit']
            # Get linked expense info if available
            if draft.get('linked_expense_draft_id'):
                expense = db.get_expense_draft(draft['linked_expense_draft_id'])
                if expense:
                    draft['linked_expense_amount'] = expense.get('amount', 0)
                    draft['linked_expense_source'] = expense.get('source', 'cash')
            # Ensure source has a default value
            if not draft.get('source'):
                draft['source'] = 'cash'
            drafts.append(draft)

    # Get pending expense items of type 'supply' for linking
    pending_supplies = db.get_pending_supply_items(TELEGRAM_USER_ID)

    # Load ingredients from ALL Poster accounts for search
    items = []
    poster_accounts_list = []

    try:
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        if accounts:
            from poster_client import PosterClient

            # Build poster accounts list
            for acc in accounts:
                poster_accounts_list.append({
                    'id': acc['id'],
                    'name': acc['account_name'],
                    'is_primary': acc.get('is_primary', False)
                })

            # Load ingredients from all accounts (no deduplication)
            for acc in accounts:
                try:
                    poster_client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    # Load storages for this account
                    storages = loop.run_until_complete(poster_client.get_storages())
                    # Build storage map: storage_id -> storage_name
                    storage_map = {int(s.get('storage_id', 0)): s.get('storage_name', '') for s in storages}
                    # Get first storage ID as default
                    default_storage_id = int(storages[0]['storage_id']) if storages else 1
                    logger.info(f"Storages for {acc.get('account_name', '')}: {storage_map}, default={default_storage_id}")

                    ingredients = loop.run_until_complete(poster_client.get_ingredients())
                    for ing in ingredients:
                        # Skip deleted ingredients
                        if str(ing.get('delete', '0')) == '1':
                            continue
                        name = ing.get('ingredient_name', '')
                        # Always use default (main) storage for the establishment
                        storage_id = default_storage_id
                        storage_name = storage_map.get(storage_id, f'Storage {storage_id}')
                        # Poster ingredient type: "1"=ingredient, "2"=semi-product (полуфабрикат)
                        poster_ing_type = str(ing.get('type', '1'))
                        item_type = 'semi_product' if poster_ing_type == '2' else 'ingredient'
                        # Don't deduplicate - show from all accounts with account tag
                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': item_type,
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc.get('account_name', ''),
                            'storage_id': storage_id,
                            'storage_name': storage_name
                        })

                    # Also fetch products (товары) - only drinks like Ayran, Coca-Cola, etc.
                    # Skip tech cards (pizzas, burgers, doner, etc.)
                    products = loop.run_until_complete(poster_client.get_products())
                    for prod in products:
                        # Skip deleted products
                        if str(prod.get('delete', '0')) == '1':
                            continue
                        # Only include products from "Напитки" category for supplies
                        # Use startswith to match both "Напитки" (Pizzburg) and "Напитки Кока кола" (Pizzburg-cafe)
                        category = prod.get('category_name', '')
                        if not category.startswith('Напитки'):
                            continue
                        name = prod.get('product_name', '')
                        # Always use default (main) storage for the establishment
                        storage_id = default_storage_id
                        storage_name = storage_map.get(storage_id, f'Storage {storage_id}')
                        items.append({
                            'id': int(prod.get('product_id', 0)),
                            'name': name,
                            'type': 'product',
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc.get('account_name', ''),
                            'storage_id': storage_id,
                            'storage_name': storage_name
                        })

                    loop.run_until_complete(poster_client.close())
                    loop.close()
                except Exception as e:
                    try:
                        loop.close()
                    except Exception:
                        pass
                    logger.error(f"Error loading ingredients from account {acc.get('account_name', acc['id'])}: {e}")

    except Exception as e:
        logger.error(f"Error loading ingredients: {e}")

    # Load suppliers for autocomplete
    suppliers = load_suppliers_from_csv()

    return render_template('supplies.html',
                          drafts=drafts,
                          pending_supplies=pending_supplies,
                          items=items,
                          poster_accounts=poster_accounts_list,
                          suppliers=suppliers)


@app.route('/supplies/all')
def view_all_supplies():
    """View all supply drafts expanded on one page"""
    db = get_database()
    drafts_raw = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")

    # Load items for each draft
    drafts = []
    for draft_raw in drafts_raw:
        draft = db.get_supply_draft_with_items(draft_raw['id'])
        if draft:
            # Map database column names to frontend expected names
            if 'items' in draft:
                for item in draft['items']:
                    if 'poster_ingredient_id' in item and 'ingredient_id' not in item:
                        item['ingredient_id'] = item['poster_ingredient_id']
                    if 'ingredient_name' not in item:
                        item['ingredient_name'] = item.get('poster_ingredient_name') or item.get('item_name', '')
                    if 'price_per_unit' in item and 'price' not in item:
                        item['price'] = item['price_per_unit']
            drafts.append(draft)

    # Load ingredients from ALL Poster accounts
    items = []
    try:
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        if accounts:
            from poster_client import PosterClient

            for acc in accounts:
                try:
                    poster_client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    ingredients = loop.run_until_complete(poster_client.get_ingredients())
                    for ing in ingredients:
                        # Skip deleted ingredients
                        if str(ing.get('delete', '0')) == '1':
                            continue
                        name = ing.get('ingredient_name', '')

                        # Poster ingredient type: "1"=ingredient, "2"=semi-product (полуфабрикат)
                        poster_ing_type = str(ing.get('type', '1'))
                        item_type = 'semi_product' if poster_ing_type == '2' else 'ingredient'

                        # Don't deduplicate - show from all accounts with account tag
                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': item_type,
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc.get('account_name', '')
                        })

                    loop.run_until_complete(poster_client.close())
                    loop.close()
                except Exception as e:
                    try:
                        loop.close()
                    except Exception:
                        pass
                    print(f"Error loading ingredients from account {acc.get('account_name', acc['id'])}: {e}")

    except Exception as e:
        print(f"Error loading ingredients: {e}")

    return render_template('supplies_all.html', drafts=drafts, items=items)


@app.route('/supplies/create', methods=['POST'])
def create_supply_draft():
    """Create empty supply draft for manual entry"""
    db = get_database()
    data = request.get_json() or {}
    from datetime import datetime

    draft_id = db.create_empty_supply_draft(
        telegram_user_id=TELEGRAM_USER_ID,
        supplier_name=data.get('supplier_name', ''),
        invoice_date=data.get('invoice_date') or datetime.now().strftime("%Y-%m-%d"),
        total_sum=data.get('total_sum', 0),
        linked_expense_draft_id=data.get('linked_expense_draft_id'),
        account_id=data.get('account_id'),
        source=data.get('source', 'cash')
    )

    if draft_id:
        return jsonify({'success': True, 'id': draft_id})
    return jsonify({'success': False, 'error': 'Failed to create draft'})


@app.route('/supplies/update/<int:draft_id>', methods=['POST'])
def update_supply_draft(draft_id):
    """Update supply draft fields"""
    db = get_database()
    data = request.get_json() or {}

    update_fields = {}
    if 'supplier_name' in data:
        update_fields['supplier_name'] = str(data['supplier_name'])
    if 'supplier_id' in data:
        update_fields['supplier_id'] = int(data['supplier_id']) if data['supplier_id'] else None
    if 'invoice_date' in data:
        update_fields['invoice_date'] = str(data['invoice_date'])
    if 'total_sum' in data:
        try:
            update_fields['total_sum'] = float(data['total_sum'])
        except (ValueError, TypeError):
            pass
    if 'account_id' in data:
        update_fields['account_id'] = int(data['account_id']) if data['account_id'] else None
    if 'source' in data:
        update_fields['source'] = str(data['source'])

    if update_fields:
        success = db.update_supply_draft(draft_id, **update_fields)

        # Sync source to linked expense draft if changed
        if 'source' in update_fields:
            draft = db.get_supply_draft_with_items(draft_id)
            if draft and draft.get('linked_expense_draft_id'):
                db.update_expense_draft(
                    draft['linked_expense_draft_id'],
                    source=update_fields['source']
                )

        return jsonify({'success': success})
    return jsonify({'success': False, 'error': 'No fields to update'})


@app.route('/supplies/add-item/<int:draft_id>', methods=['POST'])
def add_supply_item(draft_id):
    """Add item to supply draft"""
    db = get_database()
    data = request.get_json() or {}

    # Get storage_id from data, default to 1 if not provided
    storage_id = data.get('storage_id')
    if storage_id is not None:
        storage_id = int(storage_id)

    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name=data.get('item_name', data.get('name', '')),
        quantity=float(data.get('quantity', 0)),
        unit=data.get('unit', 'шт'),
        price_per_unit=float(data.get('price', data.get('price_per_unit', 0))),
        poster_ingredient_id=data.get('poster_ingredient_id') or data.get('id'),
        poster_ingredient_name=data.get('poster_ingredient_name') or data.get('name'),
        poster_account_id=data.get('poster_account_id'),
        poster_account_name=data.get('poster_account_name'),
        item_type=data.get('item_type', 'ingredient'),  # 'ingredient' or 'product'
        storage_id=storage_id,
        storage_name=data.get('storage_name')
    )

    if item_id:
        return jsonify({'success': True, 'item_id': item_id})
    return jsonify({'success': False, 'error': 'Failed to add item'})


@app.route('/supplies/delete-item/<int:item_id>', methods=['POST'])
def delete_supply_item(item_id):
    """Delete item from supply draft"""
    db = get_database()
    success = db.delete_supply_draft_item(item_id)
    return jsonify({'success': success})


@app.route('/supplies/process-all', methods=['POST'])
def process_all_supplies():
    """Process all supply drafts - create supplies in Poster"""
    db = get_database()
    drafts_raw = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")

    results = []
    errors = []

    for draft_raw in drafts_raw:
        draft = db.get_supply_draft_with_items(draft_raw['id'])
        if not draft:
            continue

        items = draft.get('items', [])
        unmatched = [i for i in items if not i.get('poster_ingredient_id')]

        if unmatched:
            errors.append(f"#{draft['id']}: не привязано {len(unmatched)} товаров")
            continue

        try:
            from poster_client import PosterClient

            accounts = db.get_accounts(TELEGRAM_USER_ID)
            if not accounts:
                errors.append(f"#{draft['id']}: нет аккаунтов Poster")
                continue

            # Group items by poster_account_id
            items_by_account = {}
            for item in items:
                acc_id = item.get('poster_account_id')
                if acc_id not in items_by_account:
                    items_by_account[acc_id] = []
                items_by_account[acc_id].append(item)

            # Create supply for each account
            for acc_id, acc_items in items_by_account.items():
                account = None
                for a in accounts:
                    if a['id'] == acc_id:
                        account = a
                        break
                if not account:
                    account = accounts[0]

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def create_supply_in_poster():
                    client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=account['poster_token'],
                        poster_user_id=account['poster_user_id'],
                        poster_base_url=account['poster_base_url']
                    )

                    try:
                        suppliers = await client.get_suppliers()
                        supplier_name = draft.get('supplier_name', 'Неизвестный поставщик')
                        supplier_id = None

                        for s in suppliers:
                            if supplier_name.lower() in s.get('supplier_name', '').lower():
                                supplier_id = int(s['supplier_id'])
                                break

                        if not supplier_id and suppliers:
                            supplier_id = int(suppliers[0]['supplier_id'])

                        poster_accounts = await client.get_accounts()
                        account_id_poster = None

                        for acc in poster_accounts:
                            acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                            if 'закуп' in acc_name or 'оставил' in acc_name:
                                account_id_poster = int(acc['account_id'])
                                break

                        if not account_id_poster and poster_accounts:
                            account_id_poster = int(poster_accounts[0]['account_id'])

                        ingredients = []

                        # Get correct default storage for this account from Poster API
                        try:
                            storages = await client.get_storages()
                            api_default_storage_id = int(storages[0]['storage_id']) if storages else 1
                        except Exception:
                            api_default_storage_id = 1

                        # Use item's storage_id if available, otherwise use API default
                        supply_storage_id = api_default_storage_id
                        for item in acc_items:
                            item_storage_id = item.get('storage_id')
                            if item_storage_id:
                                supply_storage_id = int(item_storage_id)
                                break

                        for item in acc_items:
                            ingredients.append({
                                'id': item['poster_ingredient_id'],
                                'num': float(item['quantity']),
                                'price': float(item['price_per_unit']),
                                'type': item.get('item_type', 'ingredient')  # 'ingredient', 'semi_product', or 'product'
                            })

                        supply_date = draft.get('invoice_date') or datetime.now().strftime('%Y-%m-%d')

                        logger.info(f"Supply (batch) for {account.get('account_name', acc_id)}: "
                                    f"{len(acc_items)} items, storage_id={supply_storage_id}, "
                                    f"api_default={api_default_storage_id}")
                        supply_id = await client.create_supply(
                            supplier_id=supplier_id,
                            storage_id=supply_storage_id,
                            date=f"{supply_date} 12:00:00",
                            ingredients=ingredients,
                            account_id=account_id_poster,
                            comment=f"Накладная от {draft.get('supplier_name', 'поставщика')}"
                        )

                        return supply_id
                    finally:
                        await client.close()

                try:
                    supply_id = loop.run_until_complete(create_supply_in_poster())
                finally:
                    loop.close()

                if supply_id:
                    results.append({
                        'draft_id': draft['id'],
                        'supply_id': supply_id,
                        'account': account.get('name', '')
                    })

            # Mark draft as processed
            db.mark_supply_draft_processed(draft['id'])

            if draft.get('linked_expense_draft_id'):
                db.mark_drafts_processed([draft['linked_expense_draft_id']])

        except Exception as e:
            import traceback
            traceback.print_exc()
            errors.append(f"#{draft['id']}: {str(e)}")

    return jsonify({
        'success': len(errors) == 0,
        'results': results,
        'errors': errors,
        'processed': len(results),
        'failed': len(errors)
    })


@app.route('/supplies/<int:draft_id>')
def view_supply(draft_id):
    """View supply draft details with items"""
    db = get_database()
    draft = db.get_supply_draft_with_items(draft_id)

    if not draft:
        flash('Черновик не найден', 'error')
        return redirect(url_for('list_supplies'))

    # Load items from ALL Poster accounts (not just CSV)
    items = []
    try:
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        if accounts:
            from poster_client import PosterClient

            for acc in accounts:
                try:
                    poster_client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=acc['poster_token'],
                        poster_user_id=acc['poster_user_id'],
                        poster_base_url=acc['poster_base_url']
                    )

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    ingredients = loop.run_until_complete(poster_client.get_ingredients())
                    for ing in ingredients:
                        # Skip deleted ingredients
                        if str(ing.get('delete', '0')) == '1':
                            continue
                        name = ing.get('ingredient_name', '')

                        # Poster ingredient type: "1"=ingredient, "2"=semi-product (полуфабрикат)
                        poster_ing_type = str(ing.get('type', '1'))
                        item_type = 'semi_product' if poster_ing_type == '2' else 'ingredient'

                        # Don't deduplicate - show from all accounts with account tag
                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': item_type,
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc['account_name']
                        })

                    loop.close()
                except Exception as e:
                    try:
                        loop.close()
                    except Exception:
                        pass
                    print(f"Error loading from account {acc['account_name']}: {e}")
                    continue
    except Exception as e:
        print(f"Error loading from Poster API: {e}")
        # Fallback to CSV
        items = load_items_from_csv()

    return render_template('supply_detail.html', draft=draft, items=items)


@app.route('/supplies/delete/<int:draft_id>', methods=['POST'])
def delete_supply(draft_id):
    """Delete supply draft"""
    db = get_database()
    success = db.delete_supply_draft(draft_id)
    return jsonify({'success': success})


@app.route('/supplies/update-item/<int:item_id>', methods=['POST'])
def update_supply_item(item_id):
    """Update supply draft item (ingredient matching, quantity, price, poster_account_id)"""
    db = get_database()
    data = request.get_json() or {}

    update_fields = {}
    if 'poster_ingredient_id' in data:
        update_fields['poster_ingredient_id'] = data['poster_ingredient_id']
    if 'poster_ingredient_name' in data:
        update_fields['poster_ingredient_name'] = data['poster_ingredient_name']
    if 'poster_account_id' in data:
        update_fields['poster_account_id'] = data['poster_account_id']
    if 'poster_account_name' in data:
        update_fields['poster_account_name'] = data['poster_account_name']
    if 'quantity' in data:
        update_fields['quantity'] = data['quantity']
    if 'price_per_unit' in data:
        update_fields['price_per_unit'] = data['price_per_unit']
        # Recalculate total
        if 'quantity' in update_fields:
            update_fields['total'] = update_fields['quantity'] * update_fields['price_per_unit']
        else:
            update_fields['total'] = data.get('quantity', 1) * update_fields['price_per_unit']

    success = db.update_supply_draft_item(item_id, **update_fields) if update_fields else False
    return jsonify({'success': success})


@app.route('/supplies/process/<int:draft_id>', methods=['POST'])
def process_supply(draft_id):
    """Process supply draft - create supply in Poster (multi-account support)

    Items can have different poster_account_id, so we create separate supplies for each account.
    """
    db = get_database()
    draft = db.get_supply_draft_with_items(draft_id)

    if not draft:
        return jsonify({'success': False, 'error': 'Черновик не найден'})

    # Check all items have matched ingredients
    items = draft.get('items', [])
    unmatched = [i for i in items if not i.get('poster_ingredient_id')]

    if unmatched:
        return jsonify({
            'success': False,
            'error': f'Не все товары привязаны к ингредиентам ({len(unmatched)} из {len(items)})'
        })

    if not items:
        return jsonify({'success': False, 'error': 'Нет товаров в поставке'})

    try:
        from poster_client import PosterClient
        from collections import defaultdict

        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)
        if not poster_accounts:
            return jsonify({'success': False, 'error': 'Нет подключенных аккаунтов Poster'})

        # Build account lookup
        accounts_by_id = {acc['id']: acc for acc in poster_accounts}
        primary_account = next((a for a in poster_accounts if a.get('is_primary')), poster_accounts[0])

        # Group items by poster_account_id
        items_by_account = defaultdict(list)
        for item in items:
            acc_id = item.get('poster_account_id') or primary_account['id']
            items_by_account[acc_id].append(item)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create_supplies_in_poster():
            created_supplies = []

            for poster_account_id, account_items in items_by_account.items():
                account = accounts_by_id.get(poster_account_id, primary_account)

                # Log account details for debugging multi-account issues
                token_prefix = account['poster_token'][:8] if account.get('poster_token') else 'N/A'
                logger.info(f"Processing supply for account '{account.get('account_name')}' "
                           f"(db_id={poster_account_id}, base_url={account.get('poster_base_url')}, "
                           f"token={token_prefix}...)")

                client = PosterClient(
                    telegram_user_id=TELEGRAM_USER_ID,
                    poster_token=account['poster_token'],
                    poster_user_id=account['poster_user_id'],
                    poster_base_url=account['poster_base_url']
                )

                try:
                    # Get suppliers list for this account
                    suppliers = await client.get_suppliers()
                    supplier_name = draft.get('supplier_name', 'Неизвестный поставщик')
                    supplier_id = None

                    for s in suppliers:
                        if supplier_name.lower() in s.get('supplier_name', '').lower():
                            supplier_id = int(s['supplier_id'])
                            break

                    if not supplier_id and suppliers:
                        supplier_id = int(suppliers[0]['supplier_id'])

                    # Get finance accounts for this Poster account
                    finance_accounts = await client.get_accounts()

                    # Use account_id from draft if set, otherwise auto-detect
                    account_id = draft.get('account_id')
                    if not account_id:
                        source = draft.get('source', 'cash')
                        if source == 'kaspi':
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'kaspi' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break
                        elif source == 'halyk':
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'халык' in acc_name or 'halyk' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break
                        else:
                            for acc in finance_accounts:
                                acc_name = (acc.get('account_name') or acc.get('name', '')).lower()
                                if 'закуп' in acc_name or 'оставил' in acc_name:
                                    account_id = int(acc['account_id'])
                                    break

                    if not account_id and finance_accounts:
                        account_id = int(finance_accounts[0]['account_id'])

                    # Prepare ingredients for this account
                    ingredients = []

                    # Get correct default storage for this account from Poster API
                    try:
                        storages = await client.get_storages()
                        api_default_storage_id = int(storages[0]['storage_id']) if storages else 1
                    except Exception:
                        api_default_storage_id = 1

                    # Fetch actual ingredients/products from this account to validate IDs
                    account_ingredients = await client.get_ingredients()
                    account_products = await client.get_products()

                    # Build SEPARATE lookups for ingredients and products (different ID namespaces in Poster)
                    valid_ingredient_ids = {}  # ingredient_id -> (name, type_str)
                    valid_product_ids = {}     # product_id -> name
                    ingredient_name_to_id = {}  # lowercase_name -> (id, type)
                    for ing in account_ingredients:
                        # Skip deleted ingredients
                        if str(ing.get('delete', '0')) == '1':
                            continue
                        ing_id = int(ing.get('ingredient_id', 0))
                        ing_name = ing.get('ingredient_name', '')
                        poster_ing_type = str(ing.get('type', '1'))
                        item_type = 'semi_product' if poster_ing_type == '2' else 'ingredient'
                        valid_ingredient_ids[ing_id] = (ing_name, item_type)
                        ingredient_name_to_id[ing_name.lower()] = (ing_id, item_type)

                    for prod in account_products:
                        # Skip deleted products
                        if str(prod.get('delete', '0')) == '1':
                            continue
                        prod_id = int(prod.get('product_id', 0))
                        prod_name = prod.get('product_name', '')
                        valid_product_ids[prod_id] = prod_name
                        ingredient_name_to_id[prod_name.lower()] = (prod_id, 'product')

                    acc_name = account.get('account_name', poster_account_id)
                    deleted_count = sum(1 for ing in account_ingredients if str(ing.get('delete', '0')) == '1')
                    hidden_count = sum(1 for ing in account_ingredients if str(ing.get('hidden', '0')) == '1')
                    logger.info(f"Validation for {acc_name}: {len(account_ingredients)} total ingredients "
                               f"({deleted_count} deleted, {hidden_count} hidden), "
                               f"{len(valid_ingredient_ids)} valid ingredient IDs, "
                               f"{len(valid_product_ids)} valid product IDs")

                    # Log details for each item being validated
                    for item in account_items:
                        item_id = item['poster_ingredient_id']
                        item_name = item.get('poster_ingredient_name', item.get('item_name', ''))
                        in_ingredients = item_id in valid_ingredient_ids
                        in_products = item_id in valid_product_ids
                        logger.info(f"  Item '{item_name}' (ID={item_id}): "
                                   f"in_ingredients={in_ingredients}, in_products={in_products}")

                    # Use item's storage_id if available, otherwise use API default
                    supply_storage_id = api_default_storage_id
                    for item in account_items:
                        item_storage_id = item.get('storage_id')
                        if item_storage_id:
                            supply_storage_id = int(item_storage_id)
                            break  # Use first item's storage_id

                    missing_items = []
                    for item in account_items:
                        item_id = item['poster_ingredient_id']
                        item_name = item.get('poster_ingredient_name', item.get('item_name', ''))
                        item_type = item.get('item_type', 'ingredient')

                        # Type-aware validation: ingredient_id and product_id are separate namespaces in Poster
                        id_valid = False
                        if item_type in ('ingredient', 'semi_product') and item_id in valid_ingredient_ids:
                            # ID exists as ingredient/semi-product in this account - correct type from account data
                            _, resolved_type = valid_ingredient_ids[item_id]
                            item_type = resolved_type
                            id_valid = True
                        elif item_type == 'product' and item_id in valid_product_ids:
                            # ID exists as product in this account
                            id_valid = True
                        elif item_id in valid_ingredient_ids:
                            # ID exists as ingredient but item was typed as product - fix type
                            _, resolved_type = valid_ingredient_ids[item_id]
                            logger.info(f"Type correction for '{item_name}' in {account.get('account_name')}: "
                                       f"type '{item_type}' -> '{resolved_type}' (ID {item_id})")
                            item_type = resolved_type
                            id_valid = True
                        elif item_id in valid_product_ids:
                            # ID exists as product but item was typed as ingredient - fix type
                            logger.info(f"Type correction for '{item_name}' in {account.get('account_name')}: "
                                       f"type '{item_type}' -> 'product' (ID {item_id})")
                            item_type = 'product'
                            id_valid = True

                        if not id_valid:
                            # ID not found in any namespace - try to find by name
                            name_lower = item_name.lower()
                            if name_lower in ingredient_name_to_id:
                                resolved_id, resolved_type = ingredient_name_to_id[name_lower]
                                logger.info(f"Resolved ingredient '{item_name}' for {account.get('account_name')}: "
                                           f"ID {item_id} -> {resolved_id} (type: {resolved_type})")
                                item_id = resolved_id
                                item_type = resolved_type
                                id_valid = True
                            else:
                                missing_items.append(item_name)
                                continue

                        ingredients.append({
                            'id': item_id,
                            'num': float(item['quantity']),
                            'price': float(item['price_per_unit']),
                            'type': item_type
                        })

                    if missing_items:
                        acc_name = account.get('account_name', poster_account_id)
                        raise Exception(
                            f"В аккаунте {acc_name} не найдены ингредиенты: {', '.join(missing_items)}. "
                            f"Проверьте, что ингредиенты существуют в этом заведении."
                        )

                    ingredient_types = {i['id']: i['type'] for i in ingredients}
                    logger.info(f"Supply for {account.get('account_name', poster_account_id)}: "
                                f"{len(account_items)} items, storage_id={supply_storage_id}, "
                                f"api_default={api_default_storage_id}, "
                                f"ingredient types: {ingredient_types}")

                    # Create supply
                    supply_date = draft.get('invoice_date') or datetime.now().strftime('%Y-%m-%d')

                    supply_id = await client.create_supply(
                        supplier_id=supplier_id,
                        storage_id=supply_storage_id,
                        date=f"{supply_date} 12:00:00",
                        ingredients=ingredients,
                        account_id=account_id,
                        comment=f"Накладная от {supplier_name}"
                    )

                    if supply_id:
                        account_total = sum(i['quantity'] * i['price_per_unit'] for i in account_items)
                        created_supplies.append({
                            'supply_id': supply_id,
                            'account_name': account['account_name'],
                            'items_count': len(account_items),
                            'total': account_total
                        })
                        logger.info(f"Created supply #{supply_id} in {account['account_name']}: {len(account_items)} items, {account_total} tg")

                finally:
                    await client.close()

            return created_supplies

        try:
            created_supplies = loop.run_until_complete(create_supplies_in_poster())
        finally:
            loop.close()

        if created_supplies:
            # Mark draft as processed
            db.mark_supply_draft_processed(draft_id)

            # Also mark linked expense draft as in_poster (stay visible, show green)
            # and sync the source from supply to expense
            if draft.get('linked_expense_draft_id'):
                # Store supply IDs so sync can match Poster transactions
                # Poster creates finance transactions with description "Поставка №{supply_id} от «...»"
                supply_ids_str = ','.join(str(s['supply_id']) for s in created_supplies)
                poster_txn_id = f"supply_{supply_ids_str}"
                # Update source and poster_transaction_id on expense draft
                update_ok = db.update_expense_draft(
                    draft['linked_expense_draft_id'],
                    source=draft.get('source', 'cash'),
                    poster_transaction_id=poster_txn_id
                )
                logger.info(f"🔗 Linked expense draft #{draft['linked_expense_draft_id']} → {poster_txn_id} (update_ok={update_ok})")
                # Mark as in Poster (keeps it visible with green status)
                db.mark_drafts_in_poster([draft['linked_expense_draft_id']])
            else:
                logger.warning(f"⚠️ Supply draft #{draft_id} has no linked_expense_draft_id — sync may create duplicate!")

            # Format response
            supply_ids = [s['supply_id'] for s in created_supplies]
            return jsonify({
                'success': True,
                'supply_id': supply_ids[0] if len(supply_ids) == 1 else supply_ids,
                'supplies': created_supplies
            })
        else:
            return jsonify({'success': False, 'error': 'Не удалось создать поставку'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


# ========================================
# Supplier Aliases Web Interface
# ========================================

def load_suppliers_from_csv():
    """Load suppliers from CSV file"""
    suppliers = []
    suppliers_csv = config.DATA_DIR / "poster_suppliers.csv"

    if suppliers_csv.exists():
        try:
            with open(suppliers_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    suppliers.append({
                        'id': int(row['supplier_id']),
                        'name': row['name']
                    })
        except Exception as e:
            print(f"Error loading suppliers: {e}")

    return suppliers


@app.route('/supplier-aliases')
def list_supplier_aliases():
    """Show supplier aliases"""
    db = get_database()
    aliases = db.get_supplier_aliases(TELEGRAM_USER_ID)
    suppliers = load_suppliers_from_csv()
    return render_template('supplier_aliases.html', aliases=aliases, suppliers=suppliers)


@app.route('/supplier-aliases/add', methods=['POST'])
def add_supplier_alias():
    """Add new supplier alias"""
    db = get_database()
    data = request.get_json() or request.form

    alias_text = data.get('alias_text', '').strip()
    poster_supplier_id = data.get('poster_supplier_id')
    poster_supplier_name = data.get('poster_supplier_name', '').strip()
    notes = data.get('notes', '').strip()

    if not alias_text or not poster_supplier_id or not poster_supplier_name:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        flash('Заполните все обязательные поля', 'error')
        return redirect(url_for('list_supplier_aliases'))

    success = db.add_supplier_alias(
        telegram_user_id=TELEGRAM_USER_ID,
        alias_text=alias_text,
        poster_supplier_id=int(poster_supplier_id),
        poster_supplier_name=poster_supplier_name,
        notes=notes
    )

    if request.is_json:
        return jsonify({'success': success})

    if success:
        flash(f'Алиас "{alias_text}" добавлен', 'success')
    else:
        flash('Ошибка добавления алиаса', 'error')

    return redirect(url_for('list_supplier_aliases'))


@app.route('/supplier-aliases/delete/<int:alias_id>', methods=['POST'])
def delete_supplier_alias_route(alias_id):
    """Delete supplier alias"""
    db = get_database()
    success = db.delete_supplier_alias(TELEGRAM_USER_ID, alias_id)
    return jsonify({'success': success})


@app.route('/api/suppliers/search')
def search_suppliers():
    """API endpoint for searching suppliers (autocomplete)"""
    query = request.args.get('q', '').lower()
    suppliers = load_suppliers_from_csv()

    if query:
        suppliers = [s for s in suppliers if query in s['name'].lower()]

    return jsonify(suppliers[:20])


# ========================================
# Shift Closing Web Interface (Закрытие смены)
# ========================================

@app.route('/shift-closing')
def shift_closing():
    """Show shift closing page"""
    return render_template('shift_closing.html')


# ========================================
# Shift Closing API (Закрытие смены)
# ========================================

@app.route('/api/shift-closing/poster-data')
def api_shift_closing_poster_data():
    """Get Poster data for shift closing - sales breakdown from PRIMARY business account only"""
    date = request.args.get('date')  # Format: YYYYMMDD

    try:
        from poster_client import PosterClient
        db = get_database()
        accounts = db.get_accounts(g.user_id)

        if not accounts:
            return jsonify({'error': 'No Poster accounts configured'}), 400

        # Shift closing only uses primary account (PizzBurg)
        primary_account = next((a for a in accounts if a.get('is_primary')), accounts[0])

        from datetime import datetime, timedelta
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def get_poster_data_primary():
            if date is None:
                # Business day logic: before 6 AM KZ time, use yesterday
                kz_tz = KZ_TZ
                kz_now = _kz_now()
                if kz_now.hour < 6:
                    date_param = (kz_now - timedelta(days=1)).strftime("%Y%m%d")
                else:
                    date_param = kz_now.strftime("%Y%m%d")
            else:
                date_param = date

            client = PosterClient(
                telegram_user_id=g.user_id,
                poster_token=primary_account['poster_token'],
                poster_user_id=primary_account['poster_user_id'],
                poster_base_url=primary_account['poster_base_url']
            )

            account_name = primary_account.get('account_name', 'unknown')

            try:
                # 1. Get sales data from dash.getTransactions (correct source for payment breakdown)
                result = await client._request('GET', 'dash.getTransactions', params={
                    'dateFrom': date_param,
                    'dateTo': date_param
                })
                transactions = result.get('response', [])
                closed_transactions = [tx for tx in transactions if tx.get('status') == '2']

                total_cash = 0    # payed_cash
                total_card = 0    # payed_card (Карточки)
                total_sum = 0     # payed_sum (Оборот)

                for tx in closed_transactions:
                    total_cash += int(tx.get('payed_cash', 0))
                    total_card += int(tx.get('payed_card', 0))
                    total_sum += int(tx.get('payed_sum', 0))

                # Бонусы (онлайн-оплата) = Оборот - (наличные + карта)
                # Same formula as cash_shift_closing.py
                bonus = total_sum - total_cash - total_card

                # Торговля = cash + card (без бонусов)
                trade_total = total_cash + total_card

                print(f"[SHIFT] {account_name}: оборот={total_sum/100:,.0f}₸, "
                      f"нал={total_cash/100:,.0f}₸, карта={total_card/100:,.0f}₸, "
                      f"бонусы={bonus/100:,.0f}₸, заказов={len(closed_transactions)}", flush=True)

                # 2. Get shift_start from Poster getCashShifts API
                # This is the "Фактический баланс" (actual closing balance) from the previous day's shift
                poster_prev_shift_left = None
                try:
                    target = datetime.strptime(date_param, '%Y%m%d')
                    prev_day = (target - timedelta(days=1)).strftime('%Y%m%d')
                    print(f"[SHIFT] Calling getCashShifts for prev_day={prev_day}", flush=True)
                    cash_shifts = await client.get_cash_shifts(prev_day, prev_day)
                    print(f"[SHIFT] getCashShifts raw response: {cash_shifts}", flush=True)
                    if cash_shifts:
                        last_shift = cash_shifts[-1]
                        # amount_end might be string or int — handle both
                        amount_end = int(float(last_shift.get('amount_end', 0)))
                        print(f"[SHIFT] Last shift: id={last_shift.get('cash_shift_id')}, "
                              f"amount_start={last_shift.get('amount_start')}, "
                              f"amount_end={last_shift.get('amount_end')} (parsed: {amount_end}), "
                              f"date_start={last_shift.get('date_start')}, "
                              f"date_end={last_shift.get('date_end')}", flush=True)
                        if amount_end > 0:
                            poster_prev_shift_left = amount_end
                            print(f"[SHIFT] ✅ poster_prev_shift_left={amount_end/100:,.0f}₸", flush=True)
                        else:
                            print(f"[SHIFT] ⚠️ amount_end=0 for prev day", flush=True)
                    else:
                        print(f"[SHIFT] ⚠️ No cash shifts found for {prev_day}", flush=True)
                except Exception as e:
                    import traceback
                    print(f"[SHIFT] getCashShifts error: {e}", flush=True)
                    traceback.print_exc()

                return {
                    'success': True,
                    'date': date_param,
                    'transactions_count': len(closed_transactions),
                    'account_name': account_name,
                    # For shift closing calculator (all in tiyins):
                    'trade_total': total_sum,              # Оборот (с бонусами)
                    'bonus': bonus,                        # Бонусы (онлайн-оплата)
                    'poster_card': total_card,             # Безнал картой (payed_card)
                    'poster_cash': total_cash,             # Наличка (payed_cash)
                    'poster_prev_shift_left': poster_prev_shift_left,  # amount_end предыдущего дня
                }
            finally:
                await client.close()

        try:
            data = loop.run_until_complete(get_poster_data_primary())
        finally:
            loop.close()

        # Look up Cafe's kaspi_pizzburg for auto-fill into kaspi_cafe
        try:
            from datetime import datetime
            query_date = data.get('date', date or '')
            target_date = datetime.strptime(query_date, '%Y%m%d').date()
            date_str = target_date.strftime('%Y-%m-%d')

            # Find Cafe account
            cafe_account = next((a for a in accounts if not a.get('is_primary')), None)
            if cafe_account:
                cafe_closing = db.get_shift_closing(
                    g.user_id, date_str,
                    poster_account_id=cafe_account['id']
                )
                if cafe_closing and cafe_closing.get('kaspi_pizzburg'):
                    data['cafe_kaspi_pizzburg'] = float(cafe_closing['kaspi_pizzburg'])
        except Exception as e:
            print(f"[SHIFT] Error looking up cafe kaspi_pizzburg: {e}", flush=True)

        # Look up cashier shift data for auto-fill
        try:
            from datetime import datetime
            query_date = data.get('date', date or '')
            target_date = datetime.strptime(query_date, '%Y%m%d').date()
            date_str = target_date.strftime('%Y-%m-%d')

            cashier_data = db.get_cashier_shift_data(g.user_id, date_str)
            if cashier_data and (cashier_data.get('shift_data_submitted') or cashier_data.get('shift_data_submitted') == 1):
                data['cashier_wolt'] = float(cashier_data.get('wolt', 0))
                data['cashier_halyk'] = float(cashier_data.get('halyk', 0))
                data['cashier_cash_bills'] = float(cashier_data.get('cash_bills', 0))
                data['cashier_cash_coins'] = float(cashier_data.get('cash_coins', 0))
                data['cashier_expenses'] = float(cashier_data.get('expenses', 0))
                data['cashier_data_submitted'] = True
        except Exception as e:
            print(f"[SHIFT] Error looking up cashier shift data: {e}", flush=True)

        return jsonify(data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/shift-closing/calculate', methods=['POST'])
def api_shift_closing_calculate():
    """Calculate shift closing totals based on input data"""
    data = request.json

    try:
        # Input values (all in tenge, whole numbers)
        wolt = float(data.get('wolt', 0))
        halyk = float(data.get('halyk', 0))
        kaspi = float(data.get('kaspi', 0))
        kaspi_cafe = float(data.get('kaspi_cafe', 0))  # Deducted from Kaspi
        cash_bills = float(data.get('cash_bills', 0))
        cash_coins = float(data.get('cash_coins', 0))
        shift_start = float(data.get('shift_start', 0))
        expenses = float(data.get('expenses', 0))
        cash_to_leave = float(data.get('cash_to_leave', 15000))

        # Poster data (from API, in tiyins - convert to tenge)
        poster_trade = float(data.get('poster_trade', 0)) / 100  # С учётом скидок
        poster_bonus = float(data.get('poster_bonus', 0)) / 100  # Онлайн-оплата
        poster_card = float(data.get('poster_card', 0)) / 100    # Картой (для проверки)

        # Calculations
        # 1. Итого безнал факт = Wolt + Halyk + (Kaspi - Kaspi от Cafe)
        fact_cashless = wolt + halyk + (kaspi - kaspi_cafe)

        # 2. Фактический = безнал + наличка (бумажная + мелочь)
        fact_total = fact_cashless + cash_bills + cash_coins

        # 3. Итого фактический = Фактический - Смена + Расходы
        fact_adjusted = fact_total - shift_start + expenses

        # 4. Итого Poster = Торговля - Бонусы (но trade_total уже без бонусов!)
        # Значит: poster_total = poster_trade (которая уже "С учётом скидок")
        poster_total = poster_trade - poster_bonus

        # 5. Итого день = Итого фактический - Итого Poster
        day_result = fact_adjusted - poster_total

        # 6. Смена оставили = бумажные оставить + мелочь
        shift_left = cash_to_leave + cash_coins

        # 7. Инкассация = Бумажные - оставить бумажными + расходы
        # Или: вся наличка - то что оставили
        collection = cash_bills - cash_to_leave + expenses

        # 8. Разница безнала (для проверки)
        cashless_diff = fact_cashless - poster_card

        return jsonify({
            'success': True,
            'calculations': {
                # Input echo
                'wolt': wolt,
                'halyk': halyk,
                'kaspi': kaspi,
                'kaspi_cafe': kaspi_cafe,
                'cash_bills': cash_bills,
                'cash_coins': cash_coins,
                'shift_start': shift_start,
                'expenses': expenses,
                'cash_to_leave': cash_to_leave,

                # Poster data
                'poster_trade': poster_trade,
                'poster_bonus': poster_bonus,
                'poster_card': poster_card,

                # Calculated values
                'fact_cashless': fact_cashless,      # Итого безнал факт
                'fact_total': fact_total,            # Фактический
                'fact_adjusted': fact_adjusted,      # Итого фактический
                'poster_total': poster_total,        # Итого Poster
                'day_result': day_result,            # ИТОГО ДЕНЬ (излишек/недостача)
                'shift_left': shift_left,            # Смена оставили
                'collection': collection,            # Инкассация
                'cashless_diff': cashless_diff,      # Разница безнала
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/shift-closing/save', methods=['POST'])
def api_shift_closing_save():
    """Save shift closing data for a specific date"""
    from datetime import datetime, timedelta
    data = request.json
    db = get_database()

    try:
        kz_tz = KZ_TZ
        date = data.get('date') or _kz_now().strftime('%Y-%m-%d')

        db.save_shift_closing(g.user_id, date, data)

        return jsonify({'success': True, 'date': date})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shift-closing/history')
def api_shift_closing_history():
    """Get shift closing data for a specific date"""
    date = request.args.get('date')
    db = get_database()

    if not date:
        kz_tz = KZ_TZ
        date = _kz_now().strftime('%Y-%m-%d')

    try:
        closing = db.get_shift_closing(g.user_id, date)
        return jsonify({
            'success': True,
            'date': date,
            'closing': closing
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shift-closing/dates')
def api_shift_closing_dates():
    """Get list of dates with shift closing history"""
    db = get_database()

    try:
        dates = db.get_shift_closing_dates(g.user_id)
        return jsonify({
            'success': True,
            'dates': dates
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shift-closing/report')
def api_shift_closing_report():
    """Generate text report for shift closing (for copying to WhatsApp)"""
    from datetime import datetime, timedelta

    db = get_database()
    kz_tz = KZ_TZ

    date = request.args.get('date')
    if not date:
        date = _kz_now().strftime('%Y-%m-%d')

    try:
        closing = db.get_shift_closing(g.user_id, date)

        if not closing:
            return jsonify({'success': False, 'error': 'Нет данных закрытия смены за эту дату'})

        # Format date for display
        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            date_display = dt.strftime('%d.%m.%Y')
        except Exception:
            date_display = date

        def fmt(val):
            """Format number with space as thousands separator"""
            return f"{int(round(float(val or 0))):,}".replace(',', ' ')

        day_result = float(closing.get('day_result', 0))
        day_label = "излишек" if day_result > 0 else "недостача" if day_result < 0 else "ровно"
        day_sign = "+" if day_result > 0 else ""
        day_emoji = "📈" if day_result > 0 else "📉" if day_result < 0 else "✅"

        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        subsep = "   ──────────────────"

        lines = [
            f"📋 Закрытие смены {date_display}",
            sep,
            "💳 Безнал терминалы:",
            f"   Wolt: {fmt(closing.get('wolt'))}₸",
            f"   Halyk: {fmt(closing.get('halyk'))}₸",
            f"   Kaspi: {fmt(closing.get('kaspi'))}₸",
        ]

        kaspi_cafe = float(closing.get('kaspi_cafe', 0))
        if kaspi_cafe > 0:
            lines.append(f"   Kaspi Cafe: -{fmt(kaspi_cafe)}₸")

        lines += [
            subsep,
            f"   Итого безнал: {fmt(closing.get('fact_cashless'))}₸",
            "",
            "💵 Наличные:",
            f"   Бумажные: {fmt(closing.get('cash_bills'))}₸",
            f"   Мелочь: {fmt(closing.get('cash_coins'))}₸",
            sep,
            "📊 Сверка:",
            f"   Фактический: {fmt(closing.get('fact_total'))}₸",
            f"   Смена начало: {fmt(closing.get('shift_start'))}₸",
        ]

        deposits = float(closing.get('deposits', 0))
        if deposits > 0:
            lines.append(f"   Внесения: {fmt(deposits)}₸")

        expenses = float(closing.get('expenses', 0))
        if expenses > 0:
            lines.append(f"   Расходы: {fmt(expenses)}₸")

        lines += [
            subsep,
            f"   Итого факт: {fmt(closing.get('fact_adjusted'))}₸",
            "",
            f"   Poster торговля: {fmt(closing.get('poster_trade'))}₸",
            f"   Poster бонусы: -{fmt(closing.get('poster_bonus'))}₸",
            subsep,
            f"   Итого Poster: {fmt(closing.get('poster_total'))}₸",
            "",
            sep,
            f"{day_emoji} ИТОГО ДЕНЬ: {day_sign}{fmt(day_result)}₸ ({day_label})",
            sep,
            "",
            f"💰 Инкассация: {fmt(closing.get('collection'))}₸",
            f"🔄 Смена оставили: {fmt(closing.get('shift_left'))}₸",
        ]

        cashless_diff = float(closing.get('cashless_diff', 0))
        if abs(cashless_diff) >= 1:
            diff_sign = "+" if cashless_diff > 0 else ""
            lines.append("")
            lines.append(f"⚠️ Разница безнал: {diff_sign}{fmt(cashless_diff)}₸")

        report = "\n".join(lines)

        return jsonify({
            'success': True,
            'report': report,
            'date': date
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/expense-report')
def api_expense_report():
    """Generate expense report in text format for shift closing"""
    from datetime import datetime, timedelta

    db = get_database()

    # Get today's pending drafts (Kazakhstan time UTC+5)
    kz_tz = KZ_TZ
    today = _kz_now()
    today_str = today.strftime("%Y-%m-%d")
    date_display = today.strftime("%d.%m")

    try:
        # Get all pending expense drafts for today
        all_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="pending")

        # Filter to today's drafts
        drafts = []
        for d in all_drafts:
            created_at = str(d.get('created_at', ''))[:10]
            if created_at == today_str:
                drafts.append(d)

        # Group by source (cash, kaspi, halyk)
        by_source = {'cash': [], 'kaspi': [], 'halyk': []}
        for d in drafts:
            source = d.get('source', 'cash')
            if source not in by_source:
                source = 'cash'
            by_source[source].append(d)

        # Build report
        lines = [f"Закрытие смены {date_display}", ""]

        # Kaspi section
        if by_source['kaspi']:
            lines.append("Каспий")
            total = 0
            for d in by_source['kaspi']:
                amount = int(d.get('amount', 0))
                desc = d.get('description', '')
                is_income = d.get('is_income', 0)
                if is_income:
                    lines.append(f"- [x] +{amount} {desc}")
                    total += amount
                else:
                    lines.append(f"- [x] {amount} {desc}")
                    total -= amount
            lines.append(f"Итого {'+' if total >= 0 else ''}{total}")
            lines.append("")

        # Cash section
        if by_source['cash']:
            lines.append("Наличка")
            total_expense = 0
            total_income = 0
            for d in by_source['cash']:
                amount = int(d.get('amount', 0))
                desc = d.get('description', '')
                is_income = d.get('is_income', 0)
                if is_income:
                    lines.append(f"- [x] +{amount} {desc}")
                    total_income += amount
                else:
                    lines.append(f"- [x] {amount} {desc}")
                    total_expense += amount
            net = total_income - total_expense
            lines.append(f"Расход: {total_expense}, Приход: +{total_income}")
            lines.append(f"Итого {'+' if net >= 0 else ''}{net}")
            lines.append("")

        # Halyk section
        if by_source['halyk']:
            lines.append("Халык банк")
            total = 0
            for d in by_source['halyk']:
                amount = int(d.get('amount', 0))
                desc = d.get('description', '')
                is_income = d.get('is_income', 0)
                if is_income:
                    lines.append(f"- [x] +{amount} {desc}")
                    total += amount
                else:
                    lines.append(f"- [x] {amount} {desc}")
                    total -= amount
            lines.append(f"Итого {'+' if total >= 0 else ''}{total}")
            lines.append("")

        report = "\n".join(lines)

        return jsonify({
            'success': True,
            'report': report,
            'date': today_str,
            'counts': {
                'cash': len(by_source['cash']),
                'kaspi': len(by_source['kaspi']),
                'halyk': len(by_source['halyk'])
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================================
# Serve Mini App static files
# ========================================

@app.route('/mini-app')
@app.route('/mini-app/')
@app.route('/mini-app/<path:path>')
def serve_mini_app(path=''):
    """Serve Mini App frontend"""
    mini_app_dir = os.path.join(os.path.dirname(__file__), 'mini_app', 'dist')

    # Check if dist exists
    if not os.path.exists(mini_app_dir):
        return jsonify({
            'error': 'Mini App not built',
            'message': 'Run "cd mini_app && npm install && npm run build" first'
        }), 404

    if path and os.path.exists(os.path.join(mini_app_dir, path)):
        return send_from_directory(mini_app_dir, path)
    else:
        return send_from_directory(mini_app_dir, 'index.html')


# ========================================
# Cafe Shift Closing (isolated for employees)
# ========================================

def resolve_cafe_info():
    """Resolve cafe account info from session (admin/owner) or abort."""
    db = get_database()
    role = session.get('role')
    if role == 'admin':
        poster_account_id = session.get('poster_account_id')
        if poster_account_id:
            info = db.get_web_user_poster_info(session['web_user_id'])
            if info:
                return info
    elif role == 'owner':
        telegram_user_id = session.get('telegram_user_id')
        accounts = db.get_accounts(telegram_user_id)
        cafe_account = next((a for a in accounts if not a.get('is_primary')), None)
        if cafe_account:
            return {
                'telegram_user_id': telegram_user_id,
                'poster_account_id': cafe_account['id'],
                'account_name': cafe_account.get('account_name', 'Cafe'),
                'poster_token': cafe_account.get('poster_token'),
                'poster_user_id': cafe_account.get('poster_user_id'),
                'poster_base_url': cafe_account.get('poster_base_url'),
            }
    from flask import abort
    abort(403)


# Legacy token-based URL → redirect to session-based
@app.route('/cafe/<token>/shift-closing')
def cafe_shift_closing_legacy(token):
    return redirect('/cafe/shift-closing')


@app.route('/cafe/shift-closing')
def cafe_shift_closing():
    info = resolve_cafe_info()
    role = session.get('role', 'admin')
    return render_template('shift_closing_cafe.html',
                           account_name=info.get('account_name', 'Cafe'),
                           user_role=role)


@app.route('/api/cafe/<token>/poster-data')
def api_cafe_poster_data_legacy(token):
    return redirect(f'/api/cafe/poster-data?{request.query_string.decode()}')


@app.route('/api/cafe/poster-data')
def api_cafe_poster_data():
    """Get Poster data for cafe shift closing"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    date = request.args.get('date')  # YYYYMMDD

    try:
        from poster_client import PosterClient
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def get_cafe_poster_data():
            if date is None:
                kz_tz = KZ_TZ
                kz_now = _kz_now()
                if kz_now.hour < 6:
                    date_param = (kz_now - timedelta(days=1)).strftime("%Y%m%d")
                else:
                    date_param = kz_now.strftime("%Y%m%d")
            else:
                date_param = date

            client = PosterClient(
                telegram_user_id=info['telegram_user_id'],
                poster_token=info['poster_token'],
                poster_user_id=info['poster_user_id'],
                poster_base_url=info['poster_base_url']
            )

            try:
                # 1. Sales data from dash.getTransactions
                result = await client._request('GET', 'dash.getTransactions', params={
                    'dateFrom': date_param,
                    'dateTo': date_param
                })
                transactions = result.get('response', [])
                closed_transactions = [tx for tx in transactions if tx.get('status') == '2']

                total_cash = 0
                total_card = 0
                total_sum = 0

                for tx in closed_transactions:
                    total_cash += abs(int(tx.get('payed_cash', 0)))
                    total_card += abs(int(tx.get('payed_card', 0)))
                    total_sum += abs(int(tx.get('payed_sum', 0)))

                bonus = total_sum - total_cash - total_card
                if bonus < 0:
                    bonus = 0

                account_name = info.get('account_name', 'Cafe')
                print(f"[CAFE SHIFT] {account_name}: оборот={total_sum/100:,.0f}₸, "
                      f"нал={total_cash/100:,.0f}₸, карта={total_card/100:,.0f}₸, "
                      f"бонусы={bonus/100:,.0f}₸, заказов={len(closed_transactions)}", flush=True)

                # 2. Get shift_start from getCashShifts
                poster_prev_shift_left = None
                try:
                    target = datetime.strptime(date_param, '%Y%m%d')
                    prev_day = (target - timedelta(days=1)).strftime('%Y%m%d')
                    cash_shifts = await client.get_cash_shifts(prev_day, prev_day)
                    if cash_shifts:
                        last_shift = cash_shifts[-1]
                        amount_end = int(float(last_shift.get('amount_end', 0)))
                        if amount_end > 0:
                            poster_prev_shift_left = amount_end
                            print(f"[CAFE SHIFT] getCashShifts prev day ({prev_day}): "
                                  f"amount_end={amount_end/100:,.0f}₸", flush=True)
                except Exception as e:
                    print(f"[CAFE SHIFT] getCashShifts error: {e}", flush=True)

                return {
                    'success': True,
                    'date': date_param,
                    'transactions_count': len(closed_transactions),
                    'account_name': account_name,
                    'trade_total': total_sum,
                    'bonus': bonus,
                    'poster_card': total_card,
                    'poster_cash': total_cash,
                    'poster_prev_shift_left': poster_prev_shift_left,
                }
            finally:
                await client.close()

        try:
            data = loop.run_until_complete(get_cafe_poster_data())
        finally:
            loop.close()

        # Look up main shift closing's kaspi_cafe for auto-fill into kaspi_pizzburg
        try:
            db = get_database()
            query_date = data.get('date', date or '')
            target_date = datetime.strptime(query_date, '%Y%m%d').date()
            date_str = target_date.strftime('%Y-%m-%d')

            # Get main shift closing (poster_account_id=None)
            main_closing = db.get_shift_closing(info['telegram_user_id'], date_str)
            if main_closing and main_closing.get('kaspi_cafe'):
                data['main_kaspi_cafe'] = float(main_closing['kaspi_cafe'])
        except Exception as e:
            print(f"[CAFE SHIFT] Error looking up main kaspi_cafe: {e}", flush=True)

        return jsonify(data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/cafe/<token>/calculate', methods=['POST'])
def api_cafe_calculate_legacy(token):
    return redirect('/api/cafe/calculate', code=307)


@app.route('/api/cafe/calculate', methods=['POST'])
def api_cafe_calculate():
    """Calculate cafe shift closing totals"""
    resolve_cafe_info()  # validate access
    data = request.json

    try:
        wolt = float(data.get('wolt', 0))
        kaspi = float(data.get('kaspi', 0))
        kaspi_pizzburg = float(data.get('kaspi_pizzburg', 0))
        cash_bills = float(data.get('cash_bills', 0))
        cash_coins = float(data.get('cash_coins', 0))
        shift_start = float(data.get('shift_start', 0))
        expenses = float(data.get('expenses', 0))
        cash_to_leave = float(data.get('cash_to_leave', 10000))

        poster_trade = float(data.get('poster_trade', 0)) / 100
        poster_bonus = float(data.get('poster_bonus', 0)) / 100
        poster_card = float(data.get('poster_card', 0)) / 100

        # Cafe formulas: kaspi_pizzburg ADDS to cashless (deliveries via Pizzburg couriers)
        fact_cashless = wolt + kaspi + kaspi_pizzburg
        fact_total = fact_cashless + cash_bills + cash_coins
        fact_adjusted = fact_total - shift_start + expenses
        poster_total = poster_trade - poster_bonus
        day_result = fact_adjusted - poster_total
        shift_left = cash_to_leave + cash_coins
        collection = cash_bills - cash_to_leave + expenses
        cashless_diff = fact_cashless - poster_card

        return jsonify({
            'success': True,
            'calculations': {
                'wolt': wolt, 'kaspi': kaspi,
                'kaspi_pizzburg': kaspi_pizzburg,
                'cash_bills': cash_bills, 'cash_coins': cash_coins,
                'shift_start': shift_start,
                'expenses': expenses, 'cash_to_leave': cash_to_leave,
                'poster_trade': poster_trade, 'poster_bonus': poster_bonus,
                'poster_card': poster_card,
                'fact_cashless': fact_cashless,
                'fact_total': fact_total,
                'fact_adjusted': fact_adjusted,
                'poster_total': poster_total,
                'day_result': day_result,
                'shift_left': shift_left,
                'collection': collection,
                'cashless_diff': cashless_diff,
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/cafe/<token>/save', methods=['POST'])
def api_cafe_save_legacy(token):
    return redirect('/api/cafe/save', code=307)


@app.route('/api/cafe/save', methods=['POST'])
def api_cafe_save():
    """Save cafe shift closing data"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    data = request.json
    db = get_database()

    try:
        kz_tz = KZ_TZ
        date = data.get('date') or _kz_now().strftime('%Y-%m-%d')

        db.save_shift_closing(
            info['telegram_user_id'], date, data,
            poster_account_id=info['poster_account_id']
        )

        return jsonify({'success': True, 'date': date})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/<token>/history')
def api_cafe_history_legacy(token):
    return redirect(f'/api/cafe/history?{request.query_string.decode()}')


@app.route('/api/cafe/history')
def api_cafe_history():
    """Get cafe shift closing data for a specific date"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    date = request.args.get('date')
    db = get_database()

    if not date:
        kz_tz = KZ_TZ
        date = _kz_now().strftime('%Y-%m-%d')

    try:
        closing = db.get_shift_closing(
            info['telegram_user_id'], date,
            poster_account_id=info['poster_account_id']
        )
        return jsonify({'success': True, 'date': date, 'closing': closing})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/<token>/dates')
def api_cafe_dates_legacy(token):
    return redirect(f'/api/cafe/dates?{request.query_string.decode()}')


@app.route('/api/cafe/dates')
def api_cafe_dates():
    """Get list of dates with cafe shift closing history"""
    info = resolve_cafe_info()
    db = get_database()

    try:
        dates = db.get_shift_closing_dates(
            info['telegram_user_id'],
            poster_account_id=info['poster_account_id']
        )
        return jsonify({'success': True, 'dates': dates})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/<token>/report')
def api_cafe_report_legacy(token):
    return redirect(f'/api/cafe/report?{request.query_string.decode()}')


@app.route('/api/cafe/report')
def api_cafe_report():
    """Generate text report for cafe shift closing"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    db = get_database()
    kz_tz = KZ_TZ

    date = request.args.get('date')
    if not date:
        date = _kz_now().strftime('%Y-%m-%d')

    try:
        closing = db.get_shift_closing(
            info['telegram_user_id'], date,
            poster_account_id=info['poster_account_id']
        )

        if not closing:
            return jsonify({'success': False, 'error': 'Нет данных закрытия смены за эту дату'})

        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            date_display = dt.strftime('%d.%m.%Y')
        except Exception:
            date_display = date

        def fmt(val):
            return f"{int(round(float(val or 0))):,}".replace(',', ' ')

        account_name = info.get('account_name', 'Cafe')
        day_result = float(closing.get('day_result', 0))
        day_label = "излишек" if day_result > 0 else "недостача" if day_result < 0 else "ровно"
        day_sign = "+" if day_result > 0 else ""
        day_emoji = "📈" if day_result > 0 else "📉" if day_result < 0 else "✅"

        sep = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        subsep = "   ──────────────────"

        lines = [
            f"📋 {account_name} — Закрытие смены {date_display}",
            sep,
            "💳 Безнал терминалы:",
            f"   Wolt: {fmt(closing.get('wolt'))}₸",
            f"   Kaspi: {fmt(closing.get('kaspi'))}₸",
        ]

        kaspi_pizzburg = float(closing.get('kaspi_pizzburg', 0))
        if kaspi_pizzburg > 0:
            lines.append(f"   Kaspi Pizzburg (курьеры): +{fmt(kaspi_pizzburg)}₸")

        lines += [
            subsep,
            f"   Итого безнал: {fmt(closing.get('fact_cashless'))}₸",
            "",
            "💵 Наличные:",
            f"   Бумажные: {fmt(closing.get('cash_bills'))}₸",
            f"   Мелочь: {fmt(closing.get('cash_coins'))}₸",
            sep,
            "📊 Сверка:",
            f"   Фактический: {fmt(closing.get('fact_total'))}₸",
            f"   Смена начало: {fmt(closing.get('shift_start'))}₸",
        ]

        expenses = float(closing.get('expenses', 0))
        if expenses > 0:
            lines.append(f"   Расходы: {fmt(expenses)}₸")

        lines += [
            subsep,
            f"   Итого факт: {fmt(closing.get('fact_adjusted'))}₸",
            "",
            f"   Poster торговля: {fmt(closing.get('poster_trade'))}₸",
            f"   Poster бонусы: -{fmt(closing.get('poster_bonus'))}₸",
            subsep,
            f"   Итого Poster: {fmt(closing.get('poster_total'))}₸",
            "",
            sep,
            f"{day_emoji} ИТОГО ДЕНЬ: {day_sign}{fmt(day_result)}₸ ({day_label})",
            sep,
            "",
            f"💰 Инкассация: {fmt(closing.get('collection'))}₸",
            f"🔄 Смена оставили: {fmt(closing.get('shift_left'))}₸",
        ]

        cashless_diff = float(closing.get('cashless_diff', 0))
        if abs(cashless_diff) >= 1:
            diff_sign = "+" if cashless_diff > 0 else ""
            lines.append("")
            lines.append(f"⚠️ Разница безнал: {diff_sign}{fmt(cashless_diff)}₸")

        report = "\n".join(lines)
        return jsonify({'success': True, 'report': report, 'date': date})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Cafe Transfers ====================

# Account IDs for Cafe (Pizzburg-cafe)
CAFE_ACCOUNTS = {
    'kaspi': 1,        # Каспи Пей
    'inkassacia': 2,   # Инкассация (вечером)
    'cash_left': 5,    # Оставил в кассе (на закупы)
    'wolt': 7,         # Wolt доставка
}


@app.route('/api/cafe/salaries/status')
def api_cafe_salaries_status():
    """Check if cafe salaries were already created today"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    db = get_database()

    try:
        kz_tz = KZ_TZ
        kz_now = _kz_now()
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        closing = db.get_shift_closing(
            info['telegram_user_id'], date_str,
            poster_account_id=info['poster_account_id']
        )

        salaries_created = False
        salaries_data = None
        if closing:
            salaries_created = closing.get('salaries_created') in (True, 1)
            if salaries_created and closing.get('salaries_data'):
                import json
                try:
                    salaries_data = json.loads(closing['salaries_data'])
                except Exception:
                    pass

        return jsonify({
            'success': True,
            'salaries_created': salaries_created,
            'salaries_data': salaries_data,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/employees/last')
def api_cafe_employees_last():
    """Get last used cafe employee names for auto-fill"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    db = get_database()

    try:
        # Find last shift closing with salaries_data
        kz_tz = KZ_TZ
        kz_now = _kz_now()

        from database import DB_TYPE
        conn = db._get_connection()
        cursor = conn.cursor()
        placeholder = "?" if DB_TYPE == "sqlite" else "%s"

        cursor.execute(f"""
            SELECT salaries_data FROM shift_closings
            WHERE telegram_user_id = {placeholder}
            AND poster_account_id = {placeholder}
            AND salaries_data IS NOT NULL
            ORDER BY date DESC LIMIT 1
        """, (info['telegram_user_id'], info['poster_account_id']))

        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            import json
            try:
                salaries = json.loads(row[0])
                return jsonify({'success': True, 'salaries': salaries})
            except Exception:
                pass

        return jsonify({'success': True, 'salaries': None})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/salaries/create', methods=['POST'])
def api_cafe_salaries_create():
    """Create cafe salary transactions in Poster (Кассир, Сушист, Повар Сандей)"""
    from datetime import datetime, timedelta
    from poster_client import PosterClient
    info = resolve_cafe_info()
    db = get_database()
    data = request.json

    try:
        kz_tz = KZ_TZ
        kz_now = _kz_now()
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Duplicate protection
        closing = db.get_shift_closing(
            info['telegram_user_id'], date_str,
            poster_account_id=info['poster_account_id']
        )
        if closing and closing.get('salaries_created') in (True, 1):
            return jsonify({
                'success': False,
                'error': 'Зарплаты за сегодня уже созданы'
            }), 400

        salaries = data.get('salaries', [])
        if not salaries:
            return jsonify({'success': False, 'error': 'Нет данных о зарплатах'}), 400

        # Category mapping for cafe
        CAFE_SALARY_CATEGORIES = {
            'Кассир': 16,
            'Сушист': 17,
            'Повар Сандей': None,  # Auto-detect from API
        }
        CAFE_ACCOUNT_FROM = 5  # Оставил в кассе (на закупы)

        current_time = kz_now.strftime("%Y-%m-%d %H:%M:%S")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create():
            poster_client = PosterClient(
                telegram_user_id=info['telegram_user_id'],
                poster_token=info['poster_token'],
                poster_user_id=info['poster_user_id'],
                poster_base_url=info['poster_base_url']
            )

            created = []
            povar_sandey_id = None

            try:
                for s in salaries:
                    role = s.get('role', '')
                    name = s.get('name', '')
                    amount = int(s.get('amount', 0))

                    if amount <= 0:
                        continue

                    cat_id = CAFE_SALARY_CATEGORIES.get(role)

                    # Auto-detect Повар Сандей category
                    if cat_id is None and 'повар' in role.lower():
                        if povar_sandey_id is None:
                            categories = await poster_client.get_categories()
                            for cat in categories:
                                cat_name = cat.get('finance_category_name', '').lower()
                                if 'повар' in cat_name and 'санд' in cat_name:
                                    povar_sandey_id = int(cat.get('finance_category_id'))
                                    break
                        cat_id = povar_sandey_id

                    if cat_id is None:
                        logger.warning(f"⚠️ Категория не найдена для роли '{role}', пропускаю")
                        continue

                    tx_id = await poster_client.create_transaction(
                        transaction_type=0,  # expense
                        category_id=cat_id,
                        account_from_id=CAFE_ACCOUNT_FROM,
                        amount=amount,
                        date=current_time,
                        comment=name
                    )
                    created.append({
                        'role': role,
                        'name': name,
                        'amount': amount,
                        'tx_id': tx_id,
                    })
                    logger.info(f"✅ Cafe salary: {role} {name} = {amount}₸, tx_id={tx_id}")

            finally:
                await poster_client.close()

            return created

        try:
            created = loop.run_until_complete(create())
        finally:
            loop.close()

        if not created:
            return jsonify({'success': False, 'error': 'Не удалось создать транзакции'}), 500

        # Save to shift_closings
        import json
        salaries_json = json.dumps(
            [{'role': c['role'], 'name': c['name'], 'amount': c['amount']} for c in created],
            ensure_ascii=False
        )
        db.set_cafe_salaries(
            info['telegram_user_id'], date_str,
            info['poster_account_id'], salaries_json
        )

        total = sum(c['amount'] for c in created)
        return jsonify({
            'success': True,
            'salaries': created,
            'total': total,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cafe/<token>/transfers', methods=['POST'])
def api_cafe_transfers_legacy(token):
    return redirect('/api/cafe/transfers', code=307)


@app.route('/api/cafe/transfers', methods=['POST'])
def api_cafe_transfers():
    """Create auto-transfers for cafe shift closing"""
    from datetime import datetime, timedelta
    info = resolve_cafe_info()
    db = get_database()

    data = request.json or {}
    date = data.get('date')

    try:
        kz_tz = KZ_TZ
        if not date:
            kz_now = _kz_now()
            date = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Get shift closing data
        closing = db.get_shift_closing(
            info['telegram_user_id'], date,
            poster_account_id=info['poster_account_id']
        )

        if not closing:
            return jsonify({'success': False, 'error': 'Нет данных закрытия смены за эту дату'}), 400

        # Check if already created
        if closing.get('transfers_created') in (True, 1):
            return jsonify({'success': True, 'already_created': True, 'message': 'Переводы уже созданы ранее'})

        collection = float(closing.get('collection', 0))
        wolt = float(closing.get('wolt', 0))
        cashless_diff = float(closing.get('cashless_diff', 0))

        # Корректировка инкассации на разницу безнала
        adjusted_collection = collection + cashless_diff

        # Build transfer list
        transfers = []
        if adjusted_collection > 0:
            transfers.append({
                'name': 'Инкассация → Оставил в кассе',
                'from': CAFE_ACCOUNTS['inkassacia'],
                'to': CAFE_ACCOUNTS['cash_left'],
                'amount': int(round(adjusted_collection)),
            })
        if wolt > 0:
            transfers.append({
                'name': 'Каспий → Вольт',
                'from': CAFE_ACCOUNTS['kaspi'],
                'to': CAFE_ACCOUNTS['wolt'],
                'amount': int(round(wolt)),
            })
        # Перевод разницы безнала: выравнивает Каспий и Оставил в кассе
        if cashless_diff < -0.5:
            transfers.append({
                'name': 'Корр. безнала: Каспий → Оставил',
                'from': CAFE_ACCOUNTS['kaspi'],
                'to': CAFE_ACCOUNTS['cash_left'],
                'amount': int(round(abs(cashless_diff))),
            })
            print(f"[CAFE TRANSFER] Корректировка: инкассация {int(round(collection))} → {int(round(adjusted_collection))}₸ "
                  f"(diff={cashless_diff:+,.0f}), перевод Каспий→Оставил: {int(round(abs(cashless_diff)))}₸", flush=True)
        elif cashless_diff > 0.5:
            transfers.append({
                'name': 'Корр. безнала: Оставил → Каспий',
                'from': CAFE_ACCOUNTS['cash_left'],
                'to': CAFE_ACCOUNTS['kaspi'],
                'amount': int(round(cashless_diff)),
            })
            print(f"[CAFE TRANSFER] Корректировка: инкассация {int(round(collection))} → {int(round(adjusted_collection))}₸ "
                  f"(diff={cashless_diff:+,.0f}), перевод Оставил→Каспий: {int(round(cashless_diff))}₸", flush=True)

        if not transfers:
            return jsonify({'success': True, 'created_count': 0, 'message': 'Нет переводов для создания (суммы = 0)'})

        # Create transfers in Poster
        from poster_client import PosterClient
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create_transfers():
            client = PosterClient(
                telegram_user_id=info['telegram_user_id'],
                poster_token=info['poster_token'],
                poster_user_id=info['poster_user_id'],
                poster_base_url=info['poster_base_url']
            )
            results = []
            try:
                # Format date for Poster API
                dt = datetime.strptime(date, '%Y-%m-%d')
                tx_date = dt.strftime('%Y-%m-%d') + ' 22:00:00'

                for t in transfers:
                    tx_id = await client.create_transaction(
                        transaction_type=2,
                        category_id=0,
                        account_from_id=t['from'],
                        account_to_id=t['to'],
                        amount=t['amount'],
                        date=tx_date,
                        comment=''
                    )
                    results.append({'name': t['name'], 'amount': t['amount'], 'tx_id': tx_id})
                    print(f"[CAFE TRANSFER] {t['name']}: {t['amount']}₸ → tx_id={tx_id}", flush=True)
            finally:
                await client.close()
            return results

        try:
            results = loop.run_until_complete(create_transfers())
        finally:
            loop.close()

        # Mark as created
        db.set_transfers_created(
            info['telegram_user_id'], date,
            poster_account_id=info['poster_account_id']
        )

        return jsonify({
            'success': True,
            'created_count': len(results),
            'transfers': results,
            'message': f'{len(results)} перевод(а) создано'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Main Dept Transfers ====================

# Account IDs for Main dept (Pizzburg)
MAIN_ACCOUNTS = {
    'kaspi': 1,        # Каспи Пей
    'inkassacia': 2,   # Инкассация (вечером)
    'cash_left': 4,    # Оставил в кассе
    'wolt': 8,         # Wolt доставка
    'halyk': 10,       # Халык банк
}


@app.route('/api/shift-closing/transfers', methods=['POST'])
def api_shift_closing_transfers():
    """Create auto-transfers for main dept shift closing"""
    from datetime import datetime, timedelta

    db = get_database()
    user_id = g.user_id

    data = request.json or {}
    date = data.get('date')

    try:
        kz_tz = KZ_TZ
        if not date:
            kz_now = _kz_now()
            date = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Get shift closing data (main dept: poster_account_id=NULL)
        closing = db.get_shift_closing(user_id, date)

        if not closing:
            return jsonify({'success': False, 'error': 'Нет данных закрытия смены за эту дату'}), 400

        # Check if already created
        if closing.get('transfers_created') in (True, 1):
            return jsonify({'success': True, 'already_created': True, 'message': 'Переводы уже созданы ранее'})

        collection = float(closing.get('collection', 0))
        wolt = float(closing.get('wolt', 0))
        halyk = float(closing.get('halyk', 0))
        cashless_diff = float(closing.get('cashless_diff', 0))

        # Корректировка инкассации на разницу безнала:
        # cashless_diff = fact_cashless - poster_card
        # < 0: Poster думает карты больше → наличных меньше → уменьшаем инкассацию
        # > 0: Poster думает карты меньше → наличных больше → увеличиваем инкассацию
        adjusted_collection = collection + cashless_diff

        # Build transfer list
        transfers = []
        if adjusted_collection > 0:
            transfers.append({
                'name': 'Инкассация → Оставил в кассе',
                'from': MAIN_ACCOUNTS['inkassacia'],
                'to': MAIN_ACCOUNTS['cash_left'],
                'amount': int(round(adjusted_collection)),
            })
        if wolt > 0:
            transfers.append({
                'name': 'Каспий → Вольт',
                'from': MAIN_ACCOUNTS['kaspi'],
                'to': MAIN_ACCOUNTS['wolt'],
                'amount': int(round(wolt)),
            })
        if halyk > 0:
            transfers.append({
                'name': 'Каспий → Халык',
                'from': MAIN_ACCOUNTS['kaspi'],
                'to': MAIN_ACCOUNTS['halyk'],
                'amount': int(round(halyk)),
            })
        # Перевод разницы безнала: выравнивает Каспий и Оставил в кассе
        # Инкассация уже скорректирована, поэтому двойной ошибки нет
        if cashless_diff < -0.5:
            # Poster карты > факт → переводим излишек Каспий → Оставил в кассе
            transfers.append({
                'name': 'Корр. безнала: Каспий → Оставил',
                'from': MAIN_ACCOUNTS['kaspi'],
                'to': MAIN_ACCOUNTS['cash_left'],
                'amount': int(round(abs(cashless_diff))),
            })
            print(f"[MAIN TRANSFER] Корректировка: инкассация {int(round(collection))} → {int(round(adjusted_collection))}₸ "
                  f"(diff={cashless_diff:+,.0f}), перевод Каспий→Оставил: {int(round(abs(cashless_diff)))}₸", flush=True)
        elif cashless_diff > 0.5:
            # Poster карты < факт → переводим недостачу Оставил → Каспий
            transfers.append({
                'name': 'Корр. безнала: Оставил → Каспий',
                'from': MAIN_ACCOUNTS['cash_left'],
                'to': MAIN_ACCOUNTS['kaspi'],
                'amount': int(round(cashless_diff)),
            })
            print(f"[MAIN TRANSFER] Корректировка: инкассация {int(round(collection))} → {int(round(adjusted_collection))}₸ "
                  f"(diff={cashless_diff:+,.0f}), перевод Оставил→Каспий: {int(round(cashless_diff))}₸", flush=True)

        if not transfers:
            return jsonify({'success': True, 'created_count': 0, 'message': 'Нет переводов для создания (суммы = 0)'})

        # Create transfers in Poster (using primary account)
        from poster_client import PosterClient
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create_transfers():
            client = PosterClient(telegram_user_id=user_id)
            results = []
            try:
                dt = datetime.strptime(date, '%Y-%m-%d')
                tx_date = dt.strftime('%Y-%m-%d') + ' 22:00:00'

                for t in transfers:
                    tx_id = await client.create_transaction(
                        transaction_type=2,
                        category_id=0,
                        account_from_id=t['from'],
                        account_to_id=t['to'],
                        amount=t['amount'],
                        date=tx_date,
                        comment=''
                    )
                    results.append({'name': t['name'], 'amount': t['amount'], 'tx_id': tx_id})
                    print(f"[MAIN TRANSFER] {t['name']}: {t['amount']}₸ → tx_id={tx_id}", flush=True)
            finally:
                await client.close()
            return results

        try:
            results = loop.run_until_complete(create_transfers())
        finally:
            loop.close()

        # Mark as created
        db.set_transfers_created(user_id, date)

        return jsonify({
            'success': True,
            'created_count': len(results),
            'transfers': results,
            'message': f'{len(results)} перевод(а) создано'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Cashier Shift Closing Routes ====================

def resolve_cashier_info():
    """Resolve cashier account info from session (cashier/owner) or abort."""
    db = get_database()
    role = session.get('role')
    if role == 'cashier':
        poster_account_id = session.get('poster_account_id')
        if poster_account_id:
            info = db.get_web_user_poster_info(session['web_user_id'])
            if info:
                return info
    elif role == 'owner':
        telegram_user_id = session.get('telegram_user_id')
        accounts = db.get_accounts(telegram_user_id)
        primary_account = next((a for a in accounts if a.get('is_primary')), None)
        if primary_account:
            return {
                'telegram_user_id': telegram_user_id,
                'poster_account_id': primary_account['id'],
                'account_name': primary_account.get('account_name', 'Основной отдел'),
                'poster_token': primary_account.get('poster_token'),
                'poster_user_id': primary_account.get('poster_user_id'),
                'poster_base_url': primary_account.get('poster_base_url'),
            }
    from flask import abort
    abort(403)


# Legacy token-based URL → redirect
@app.route('/cashier/<token>/shift-closing')
def cashier_shift_closing_legacy(token):
    return redirect('/cashier/shift-closing')


@app.route('/cashier/shift-closing')
def cashier_shift_closing():
    info = resolve_cashier_info()
    role = session.get('role', 'cashier')
    return render_template('shift_closing_cashier.html',
                           account_name=info.get('account_name', 'Основной отдел'),
                           user_role=role)


@app.route('/api/cashier/<token>/employees/last')
def api_cashier_employees_last_legacy(token):
    return redirect('/api/cashier/employees/last')


@app.route('/api/cashier/employees/last')
def api_cashier_employees_last():
    """Get last used employee names for auto-fill"""
    info = resolve_cashier_info()
    db = get_database()

    try:
        last = db.get_cashier_last_employees(info['telegram_user_id'])
        if last:
            return jsonify({'success': True, **last})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cashier/<token>/salaries/calculate', methods=['POST'])
def api_cashier_salaries_calculate_legacy(token):
    return redirect('/api/cashier/salaries/calculate', code=307)


@app.route('/api/cashier/salaries/calculate', methods=['POST'])
def api_cashier_salaries_calculate():
    """Calculate salaries without creating transactions"""
    info = resolve_cashier_info()
    data = request.json

    try:
        from datetime import datetime, timedelta
        kz_tz = KZ_TZ
        kz_now = _kz_now()

        cashier_count = int(data.get('cashier_count', 2))
        assistant_start_time = data.get('assistant_start_time', '10:00')

        from cashier_salary import CashierSalaryCalculator
        from doner_salary import DonerSalaryCalculator

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def calc():
            # Calculate cashier salary
            cashier_calc = CashierSalaryCalculator(info['telegram_user_id'])
            sales_data = await cashier_calc.get_total_sales()
            cashier_salary = cashier_calc.calculate_salary(sales_data['total_sales'], cashier_count)

            # Calculate doner salary
            doner_calc = DonerSalaryCalculator(info['telegram_user_id'])
            doner_data = await doner_calc.get_doner_sales_count()
            doner_base_salary = doner_calc.calculate_salary(int(doner_data['total_count']))

            # Bonus based on assistant start time
            if assistant_start_time == "10:00":
                doner_bonus = 0
                assistant_salary = 9000
            elif assistant_start_time == "12:00":
                doner_bonus = 750
                assistant_salary = 8000
            elif assistant_start_time == "14:00":
                doner_bonus = 1500
                assistant_salary = 7000
            else:
                doner_bonus = 0
                assistant_salary = 9000

            doner_salary = doner_base_salary + doner_bonus

            return {
                'cashier_salary': cashier_salary,
                'doner_salary': doner_salary,
                'assistant_salary': assistant_salary,
            }

        try:
            result = loop.run_until_complete(calc())
        finally:
            loop.close()

        return jsonify({'success': True, **result})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cashier/<token>/salaries/create', methods=['POST'])
def api_cashier_salaries_create_legacy(token):
    return redirect('/api/cashier/salaries/create', code=307)


@app.route('/api/cashier/salaries/create', methods=['POST'])
def api_cashier_salaries_create():
    """Create salary transactions in Poster"""
    info = resolve_cashier_info()
    data = request.json
    db = get_database()

    try:
        from datetime import datetime, timedelta
        kz_tz = KZ_TZ
        kz_now = _kz_now()

        # Duplicate protection: check if salaries already created today
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')
        existing = db.get_cashier_shift_data(info['telegram_user_id'], date_str)
        if existing and existing.get('salaries_created') in (True, 1):
            return jsonify({
                'success': False,
                'error': 'Зарплаты за сегодня уже созданы. Повторное создание заблокировано.'
            }), 400

        cashier_count = int(data.get('cashier_count', 2))
        cashier_names = data.get('cashier_names', [])
        assistant_start_time = data.get('assistant_start_time', '10:00')
        doner_name = data.get('doner_name', '')
        assistant_name = data.get('assistant_name', '')

        from cashier_salary import CashierSalaryCalculator
        from doner_salary import DonerSalaryCalculator

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def create():
            # Create cashier salary transactions
            cashier_calc = CashierSalaryCalculator(info['telegram_user_id'])
            cashier_result = await cashier_calc.create_salary_transactions(
                cashier_count=cashier_count,
                cashier_names=cashier_names
            )

            # Create doner salary transactions
            doner_calc = DonerSalaryCalculator(info['telegram_user_id'])
            doner_result = await doner_calc.create_salary_transaction(
                assistant_start_time=assistant_start_time,
                doner_name=doner_name,
                assistant_name=assistant_name
            )

            return cashier_result, doner_result

        try:
            cashier_result, doner_result = loop.run_until_complete(create())
        finally:
            loop.close()

        if not cashier_result.get('success') or not doner_result.get('success'):
            errors = []
            if not cashier_result.get('success'):
                errors.append(f"Кассиры: {cashier_result.get('error', '?')}")
            if not doner_result.get('success'):
                errors.append(f"Донерщик: {doner_result.get('error', '?')}")
            return jsonify({'success': False, 'error': '; '.join(errors)}), 500

        # Build salaries list for response
        salaries = []
        for s in cashier_result.get('salaries', []):
            salaries.append({
                'name': s['name'],
                'role': 'Кассир',
                'salary': s['salary'],
            })
        salaries.append({
            'name': doner_result.get('doner_name', 'Донерщик'),
            'role': 'Донерщик',
            'salary': doner_result.get('salary', 0),
        })
        salaries.append({
            'name': doner_result.get('assistant_name', 'Помощник'),
            'role': 'Помощник',
            'salary': doner_result.get('assistant_salary', 0),
        })

        # Save to cashier_shift_data
        import json
        from datetime import datetime, timedelta
        kz_tz = KZ_TZ
        kz_now = _kz_now()
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        db.save_cashier_shift_data(info['telegram_user_id'], date_str, {
            'cashier_count': cashier_count,
            'cashier_names': json.dumps(cashier_names, ensure_ascii=False),
            'assistant_start_time': assistant_start_time,
            'doner_name': doner_name,
            'assistant_name': assistant_name,
            'salaries_data': json.dumps(salaries, ensure_ascii=False),
            'salaries_created': True,
        })

        return jsonify({
            'success': True,
            'salaries': salaries,
            'total': sum(s['salary'] for s in salaries),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cashier/<token>/shift-data/save', methods=['POST'])
def api_cashier_shift_data_save_legacy(token):
    return redirect('/api/cashier/shift-data/save', code=307)


@app.route('/api/cashier/shift-data/save', methods=['POST'])
def api_cashier_shift_data_save():
    """Save cashier's 5 shift values"""
    info = resolve_cashier_info()
    data = request.json
    db = get_database()

    try:
        from datetime import datetime, timedelta
        kz_tz = KZ_TZ
        kz_now = _kz_now()
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Get existing data to preserve salary fields
        existing = db.get_cashier_shift_data(info['telegram_user_id'], date_str)
        save_data = {}
        if existing:
            # Preserve salary fields
            for f in ('cashier_count', 'cashier_names', 'assistant_start_time',
                      'doner_name', 'assistant_name', 'salaries_data', 'salaries_created'):
                if existing.get(f) is not None:
                    save_data[f] = existing[f]

        # Add shift data
        save_data['wolt'] = float(data.get('wolt', 0))
        save_data['halyk'] = float(data.get('halyk', 0))
        save_data['cash_bills'] = float(data.get('cash_bills', 0))
        save_data['cash_coins'] = float(data.get('cash_coins', 0))
        save_data['expenses'] = float(data.get('expenses', 0))
        save_data['shift_data_submitted'] = True

        db.save_cashier_shift_data(info['telegram_user_id'], date_str, save_data)

        print(f"[CASHIER] Shift data saved for {date_str}: wolt={save_data['wolt']}, "
              f"halyk={save_data['halyk']}, cash_bills={save_data['cash_bills']}, "
              f"cash_coins={save_data['cash_coins']}, expenses={save_data['expenses']}", flush=True)

        return jsonify({'success': True, 'date': date_str})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cashier/<token>/shift-data/status')
def api_cashier_shift_data_status_legacy(token):
    return redirect('/api/cashier/shift-data/status')


@app.route('/api/cashier/shift-data/status')
def api_cashier_shift_data_status():
    """Check if shift data was already submitted today"""
    info = resolve_cashier_info()
    db = get_database()

    try:
        from datetime import datetime, timedelta
        kz_tz = KZ_TZ
        kz_now = _kz_now()
        date_str = kz_now.strftime('%Y-%m-%d') if kz_now.hour >= 6 else (kz_now - timedelta(days=1)).strftime('%Y-%m-%d')

        existing = db.get_cashier_shift_data(info['telegram_user_id'], date_str)

        salaries_created = bool(existing and (existing.get('salaries_created') or existing.get('salaries_created') == 1))
        shift_data_submitted = bool(existing and (existing.get('shift_data_submitted') or existing.get('shift_data_submitted') == 1))

        result = {
            'success': True,
            'date': date_str,
            'salaries_created': salaries_created,
            'shift_data_submitted': shift_data_submitted,
        }

        # Include details for owner view
        role = session.get('role')
        if role == 'owner' and existing:
            result['salaries_data'] = existing.get('salaries_data')
            if shift_data_submitted:
                result['shift_data'] = {
                    'wolt': existing.get('wolt', 0),
                    'halyk': existing.get('halyk', 0),
                    'cash_bills': existing.get('cash_bills', 0),
                    'cash_coins': existing.get('cash_coins', 0),
                    'expenses': existing.get('expenses', 0),
                }

        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================================
# Background Expense Sync (every 5 min)
# ========================================

def _background_expense_sync():
    """Background job: sync expenses from Poster for all users with accounts"""
    try:
        from datetime import datetime, timedelta
        from poster_client import PosterClient

        db = get_database()
        poster_accounts = db.get_accounts(TELEGRAM_USER_ID)
        if not poster_accounts:
            return

        kz_tz = KZ_TZ
        today = _kz_now()
        date_str = today.strftime("%Y%m%d")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        synced = 0
        updated = 0
        deleted = 0

        async def _sync():
            nonlocal synced, updated, deleted
            seen_poster_ids = set()
            synced_account_ids = set()
            existing_drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="all")

            for account in poster_accounts:
                try:
                    client = PosterClient(
                        telegram_user_id=TELEGRAM_USER_ID,
                        poster_token=account['poster_token'],
                        poster_user_id=account['poster_user_id'],
                        poster_base_url=account['poster_base_url']
                    )
                    try:
                        transactions = await client.get_transactions(date_str, date_str)
                        finance_accounts = await client.get_accounts()
                        account_map = {str(acc['account_id']): acc for acc in finance_accounts}

                        for txn in transactions:
                            txn_type = str(txn.get('type'))
                            category_name = txn.get('name', '') or txn.get('category_name', '')
                            category_lower = category_name.lower()

                            skip_categories = ['перевод', 'кассовые смены', 'актуализац']
                            if any(skip in category_lower for skip in skip_categories):
                                continue
                            if txn_type not in ('0', '1'):
                                continue

                            txn_id = txn.get('transaction_id')
                            amount_raw = txn.get('amount_from', 0) or txn.get('amount', 0)
                            amount = abs(float(amount_raw)) / 100
                            comment = txn.get('comment', '') or ''
                            description = comment if comment else category_name

                            account_from_id = (
                                txn.get('account_from_id') or txn.get('account_from') or
                                txn.get('account_id') or txn.get('account')
                            )
                            txn_account_name = txn.get('account_name', '') or ''
                            finance_acc = account_map.get(str(account_from_id), {})
                            finance_acc_name_raw = (finance_acc.get('account_name') or finance_acc.get('name')) or txn_account_name

                            composite_txn_id = f"{account['id']}_{txn_id}"
                            seen_poster_ids.add(composite_txn_id)
                            seen_poster_ids.add(str(txn_id))

                            existing_draft = next(
                                (d for d in existing_drafts
                                 if d.get('poster_transaction_id') == composite_txn_id
                                 or (d.get('poster_transaction_id') == str(txn_id) and
                                     d.get('poster_account_id') == account['id'])),
                                None
                            )

                            if existing_draft:
                                old_poster_amount = existing_draft.get('poster_amount')
                                old_amount = existing_draft.get('amount', 0)
                                update_fields = {}
                                if old_poster_amount is None or abs(float(old_poster_amount) - amount) >= 0.01:
                                    update_fields['poster_amount'] = amount
                                    if old_poster_amount is not None and abs(float(old_amount) - float(old_poster_amount)) < 0.01:
                                        update_fields['amount'] = amount
                                    if old_poster_amount is None:
                                        update_fields['amount'] = amount
                                old_description = existing_draft.get('description', '')
                                if description and description != old_description:
                                    update_fields['description'] = description
                                if update_fields:
                                    db.update_expense_draft(existing_draft['id'], **update_fields)
                                    updated += 1
                                continue

                            # Skip supply transactions
                            import re
                            supply_match = re.search(r'Поставка\s*[№N#]\s*(\d+)', description)
                            if supply_match:
                                supply_num = supply_match.group(1)
                                supply_draft = next(
                                    (d for d in existing_drafts
                                     if (d.get('poster_transaction_id') or '').startswith('supply_') and
                                        supply_num in (d.get('poster_transaction_id') or '').replace('supply_', '').split(',')),
                                    None
                                )
                                if supply_draft:
                                    continue
                                supply_amount_draft = next(
                                    (d for d in existing_drafts
                                     if d.get('expense_type') == 'supply' and
                                        d.get('status') == 'pending' and
                                        abs(float(d.get('amount', 0)) - amount) < 1),
                                    None
                                )
                                if supply_amount_draft:
                                    db.update_expense_draft(supply_amount_draft['id'], poster_transaction_id=f"supply_{supply_num}")
                                    continue

                            finance_acc_name = finance_acc_name_raw.lower() if finance_acc_name_raw else ''
                            source = 'cash'
                            if 'kaspi' in finance_acc_name:
                                source = 'kaspi'
                            elif 'халык' in finance_acc_name or 'halyk' in finance_acc_name:
                                source = 'halyk'

                            db.create_expense_draft(
                                telegram_user_id=TELEGRAM_USER_ID,
                                amount=amount,
                                description=description,
                                expense_type='transaction',
                                category=category_name,
                                source=source,
                                account_id=finance_acc.get('account_id'),
                                poster_account_id=account['id'],
                                poster_transaction_id=composite_txn_id,
                                is_income=(txn_type == '1'),
                                completion_status='completed',
                                poster_amount=amount
                            )
                            synced += 1

                        synced_account_ids.add(str(account['id']))
                    finally:
                        await client.close()
                except Exception as e:
                    logger.warning(f"[BG SYNC] Error syncing {account.get('account_name', '?')}: {e}")

            # Clean up system category drafts
            skip_cleanup = ['перевод', 'кассовые смены', 'актуализац']
            for draft in existing_drafts:
                draft_category = (draft.get('category') or '').lower()
                if draft_category and any(skip in draft_category for skip in skip_cleanup):
                    db.delete_expense_draft(draft['id'])
                    deleted += 1

            # Delete orphaned drafts (from today only)
            today_str = today.strftime('%Y-%m-%d')
            for draft in existing_drafts:
                ptid = draft.get('poster_transaction_id', '') or ''
                if not ptid or ptid.startswith('supply_'):
                    continue
                if draft.get('status') != 'pending':
                    continue
                draft_category = (draft.get('category') or '').lower()
                if draft_category and any(skip in draft_category for skip in skip_cleanup):
                    continue
                draft_created = draft.get('created_at', '')
                if not draft_created or str(draft_created)[:10] != today_str:
                    continue
                if ptid not in seen_poster_ids:
                    if '_' in ptid:
                        account_part = ptid.split('_')[0]
                        if account_part not in synced_account_ids:
                            continue
                    db.delete_expense_draft(draft['id'])
                    deleted += 1

        try:
            loop.run_until_complete(_sync())
        finally:
            loop.close()

        if synced > 0 or updated > 0 or deleted > 0:
            logger.info(f"[BG SYNC] Expenses: +{synced} new, ~{updated} updated, -{deleted} deleted")

    except Exception as e:
        logger.error(f"[BG SYNC] Expense sync error: {e}")


def start_background_sync():
    """Start background expense sync scheduler"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        bg_scheduler = BackgroundScheduler()
        bg_scheduler.add_job(
            _background_expense_sync,
            'interval',
            minutes=5,
            id='bg_expense_sync',
            name='Background expense sync from Poster',
            replace_existing=True
        )
        bg_scheduler.start()
        logger.info("✅ Background expense sync started (every 5 min)")
    except Exception as e:
        logger.error(f"Failed to start background sync: {e}")


# Auto-start background sync when module is imported (production)
if os.getenv('USE_WEBHOOK') == 'true' or os.getenv('RAILWAY_ENVIRONMENT'):
    start_background_sync()


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Starting Poster Helper Web Interface")
    print("=" * 60)
    print(f"📂 Data directory: {config.DATA_DIR}")
    print(f"👤 User ID: {TELEGRAM_USER_ID}")
    print(f"🌐 Access at: http://localhost:5000/aliases")
    print("=" * 60)

    app.run(debug=True, port=5000, host='0.0.0.0')
