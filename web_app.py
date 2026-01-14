"""Flask web application for managing ingredient aliases and Telegram Mini App API"""
import os
import csv
import secrets
import hmac
import hashlib
import json
import asyncio
from pathlib import Path
from urllib.parse import parse_qsl
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, g
from database import get_database
import config

app = Flask(__name__)

# Generate or use SECRET_KEY for Flask sessions
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print(f"⚠️  Warning: Using generated SECRET_KEY. Set FLASK_SECRET_KEY in .env for production")
app.secret_key = SECRET_KEY

# Hardcoded user ID for demo (can be extended to multi-user with login)
TELEGRAM_USER_ID = 167084307

# Telegram Bot Token for WebApp validation
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")


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

    # Load products
    products_csv = config.DATA_DIR / "poster_products.csv"
    if products_csv.exists():
        try:
            with open(products_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
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
    """Redirect to aliases list"""
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
    """Validate API requests from Mini App"""
    if request.path.startswith('/api/'):
        init_data = request.headers.get('X-Telegram-Init-Data', '')

        # Validate init data
        if not validate_telegram_web_app_data(init_data, TELEGRAM_TOKEN):
            # In development, allow without validation
            if not TELEGRAM_TOKEN:
                g.user_id = TELEGRAM_USER_ID
            else:
                return jsonify({'error': 'Unauthorized'}), 401
        else:
            g.user_id = get_user_id_from_init_data(init_data)


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
    """API endpoint for searching items (autocomplete)"""
    query = request.args.get('q', '').lower()
    source = request.args.get('source', 'all')  # 'ingredient', 'product', or 'all'

    items = load_items_from_csv()

    # Filter by type if specified
    if source != 'all':
        items = [item for item in items if item['type'] == source]

    # Filter by query
    if query:
        items = [item for item in items if query in item['name'].lower()]

    # Limit results
    items = items[:50]

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
    """Get list of accounts"""
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
    """Create a new supply in Poster"""
    data = request.json

    # Validate required fields
    required_fields = ['supplier_id', 'supplier_name', 'account_id', 'items']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    if not isinstance(data['items'], list) or len(data['items']) == 0:
        return jsonify({'error': 'items must be a non-empty list'}), 400

    # Validate each item
    for item in data['items']:
        if not all(k in item for k in ['id', 'quantity', 'price']):
            return jsonify({'error': 'Each item must have id, quantity, and price'}), 400

    try:
        # Import poster_client and database
        from poster_client import PosterClient

        # Create supply via Poster API
        poster_client = PosterClient(g.user_id)

        # Prepare supply data
        supply_data = {
            'supplier_id': data['supplier_id'],
            'storage_id': data.get('storage_id', 1),  # Default main storage
            'account_id': data['account_id'],
            'date': data.get('date'),  # Optional, defaults to now in poster_client
            'items': []
        }

        # Format items for Poster API
        for item in data['items']:
            supply_data['items'].append({
                'id': item['id'],
                'type': item.get('type', 'ingredient'),
                'sum': float(item['quantity']) * float(item['price']),
                'cost': float(item['price'])
            })

        # Create supply using async function wrapped in asyncio.run()
        async def create_supply_async():
            result = await poster_client.create_supply(**supply_data)
            await poster_client.close()
            return result

        result = asyncio.run(create_supply_async())

        if not result.get('success'):
            return jsonify({'error': 'Failed to create supply in Poster'}), 500

        # Save price history to database
        db = get_database()
        price_records = []

        for item in data['items']:
            price_records.append({
                'ingredient_id': item['id'],
                'ingredient_name': item.get('name', ''),
                'supplier_id': data['supplier_id'],
                'supplier_name': data['supplier_name'],
                'price': float(item['price']),
                'quantity': float(item['quantity']),
                'unit': item.get('unit', 'шт'),
                'supply_id': result.get('supply_id'),
                'date': data.get('date', datetime.now().strftime('%Y-%m-%d'))
            })

        db.bulk_add_price_history(g.user_id, price_records)

        return jsonify({
            'success': True,
            'supply_id': result.get('supply_id')
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


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Starting Poster Helper Web Interface")
    print("=" * 60)
    print(f"📂 Data directory: {config.DATA_DIR}")
    print(f"👤 User ID: {TELEGRAM_USER_ID}")
    print(f"🌐 Access at: http://localhost:5000/aliases")
    print("=" * 60)

    app.run(debug=True, port=5000, host='0.0.0.0')
