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

        # If no init_data provided, use default user (for web interface)
        if not init_data:
            g.user_id = TELEGRAM_USER_ID
        # Validate init data if provided
        elif not validate_telegram_web_app_data(init_data, TELEGRAM_TOKEN):
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

        if accounts:
            from poster_client import PosterClient

            # Sort accounts: primary first (PizzBurg takes priority for deduplication)
            accounts_sorted = sorted(accounts, key=lambda a: (not a.get('is_primary', False), a['id']))

            seen_names = set()  # Track ingredient names for deduplication

            for acc in accounts_sorted:
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
                        for ing in ingredients:
                            name = ing.get('ingredient_name', '')
                            name_lower = name.lower()

                            # Skip if already seen (primary account takes priority)
                            if name_lower in seen_names:
                                continue
                            seen_names.add(name_lower)

                            items.append({
                                'id': int(ing.get('ingredient_id', 0)),
                                'name': name,
                                'type': 'ingredient',
                                'poster_account_id': acc['id'],
                                'poster_account_name': acc['account_name']
                            })

                    loop.close()
                except Exception as e:
                    print(f"Error loading from account {acc['account_name']}: {e}")
                    continue
    except Exception as e:
        print(f"Error loading from Poster API: {e}")
        # Fallback to CSV
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
    """
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
            from datetime import timezone, timedelta
            kz_tz = timezone(timedelta(hours=5))
            supply_date = datetime.now(kz_tz).strftime('%Y-%m-%d %H:%M:%S')

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
                    ingredients.append({
                        'id': item['id'],
                        'num': float(item['quantity']),
                        'price': float(item['price'])
                    })

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

                    supply_id = await poster_client.create_supply(
                        supplier_id=supplier_id,
                        storage_id=data.get('storage_id', 1),
                        date=supply_date,
                        ingredients=ingredients,
                        account_id=data['account_id'],
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
    """Show expense drafts for user"""
    from datetime import datetime, timedelta

    db = get_database()
    drafts = db.get_expense_drafts(TELEGRAM_USER_ID, status="pending")

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
                        # Load categories (same across accounts usually)
                        if not all_categories:
                            cats = await client.get_categories()
                            all_categories = cats

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

            categories, accounts, poster_transactions = loop.run_until_complete(load_data())
            loop.close()
    except Exception as e:
        print(f"Error loading categories/accounts: {e}")
        import traceback
        traceback.print_exc()

    return render_template('expenses.html',
                          drafts=drafts,
                          categories=categories,
                          accounts=accounts,
                          poster_accounts=poster_accounts_list,
                          poster_transactions=poster_transactions)


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

        categories = loop.run_until_complete(get_categories())
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

    draft_id = db.create_expense_draft(
        telegram_user_id=TELEGRAM_USER_ID,
        amount=data.get('amount', 0),
        description=data.get('description', ''),
        expense_type=data.get('expense_type', 'transaction'),
        category=data.get('category'),
        source=data.get('source', 'cash'),
        account_id=data.get('account_id'),
        poster_account_id=data.get('poster_account_id', default_poster_account_id)
    )

    if draft_id:
        return jsonify({'success': True, 'id': draft_id})
    return jsonify({'success': False, 'error': 'Failed to create draft'})


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

                    # Build category map
                    category_map = {cat.get('category_name', ''): int(cat.get('category_id', 1)) for cat in categories}
                    if "Прочее" not in category_map and category_map:
                        category_map["Прочее"] = list(category_map.values())[0]

                    for draft in account_transactions:
                        # Find finance account: prefer manually selected, then auto-detect by source
                        account_id = draft.get('account_id')

                        if not account_id:
                            # Auto-detect based on source
                            if draft['source'] == 'kaspi':
                                for acc in finance_accounts:
                                    if 'kaspi' in acc.get('name', '').lower():
                                        account_id = int(acc['account_id'])
                                        break
                            else:
                                for acc in finance_accounts:
                                    if 'закуп' in acc.get('name', '').lower() or 'оставил' in acc.get('name', '').lower():
                                        account_id = int(acc['account_id'])
                                        break

                        if not account_id and finance_accounts:
                            account_id = int(finance_accounts[0]['account_id'])

                        cat_id = category_map.get(draft.get('category'), category_map.get("Прочее", 1))

                        try:
                            await client.create_transaction(
                                transaction_type=0,
                                category_id=cat_id,
                                account_from_id=account_id,
                                amount=int(draft['amount']),
                                comment=draft['description']
                            )
                            total_success += 1
                            all_processed_ids.append(draft['id'])
                            print(f"✅ Created transaction in {account['account_name']}: {draft['description']} - {draft['amount']}₸")
                        except Exception as e:
                            print(f"Error creating transaction in {account['account_name']}: {e}")

                finally:
                    await client.close()

            return total_success, all_processed_ids

        success, processed_ids = loop.run_until_complete(create_all_transactions())
        loop.close()

        # Mark as processed
        if processed_ids:
            db.mark_drafts_processed(processed_ids)

        flash(f'Создано {success} транзакций в Poster', 'success')

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
    """Show supply drafts for user with ingredients for search"""
    db = get_database()
    drafts_raw = db.get_supply_drafts(TELEGRAM_USER_ID, status="pending")

    # Load items for each draft
    drafts = []
    for draft_raw in drafts_raw:
        draft = db.get_supply_draft_with_items(draft_raw['id'])
        if draft:
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

            # Load ingredients with deduplication
            accounts_sorted = sorted(accounts, key=lambda a: (not a.get('is_primary', False), a['id']))
            seen_names = set()

            for acc in accounts_sorted:
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
                        name = ing.get('ingredient_name', '')
                        # Don't deduplicate - show from all accounts with account tag
                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': 'ingredient',
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc.get('account_name', '')
                        })

                    loop.run_until_complete(poster_client.close())
                    loop.close()
                except Exception as e:
                    print(f"Error loading ingredients from account {acc.get('account_name', acc['id'])}: {e}")

    except Exception as e:
        print(f"Error loading ingredients: {e}")

    return render_template('supplies.html',
                          drafts=drafts,
                          pending_supplies=pending_supplies,
                          items=items,
                          poster_accounts=poster_accounts_list)


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
            drafts.append(draft)

    # Load ingredients from ALL Poster accounts
    items = []
    try:
        accounts = db.get_accounts(TELEGRAM_USER_ID)
        if accounts:
            from poster_client import PosterClient

            accounts_sorted = sorted(accounts, key=lambda a: (not a.get('is_primary', False), a['id']))
            seen_names = set()

            for acc in accounts_sorted:
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
                        name = ing.get('ingredient_name', '')
                        name_lower = name.lower()

                        if name_lower in seen_names:
                            continue
                        seen_names.add(name_lower)

                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': 'ingredient',
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc.get('name', '')
                        })

                    loop.run_until_complete(poster_client.close())
                    loop.close()
                except Exception as e:
                    print(f"Error loading ingredients from account {acc.get('name', acc['id'])}: {e}")

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
        return jsonify({'success': success})
    return jsonify({'success': False, 'error': 'No fields to update'})


@app.route('/supplies/add-item/<int:draft_id>', methods=['POST'])
def add_supply_item(draft_id):
    """Add item to supply draft"""
    db = get_database()
    data = request.get_json() or {}

    item_id = db.add_supply_draft_item(
        supply_draft_id=draft_id,
        item_name=data.get('item_name', data.get('name', '')),
        quantity=float(data.get('quantity', 0)),
        unit=data.get('unit', 'шт'),
        price_per_unit=float(data.get('price', data.get('price_per_unit', 0))),
        poster_ingredient_id=data.get('poster_ingredient_id') or data.get('id'),
        poster_ingredient_name=data.get('poster_ingredient_name') or data.get('name'),
        poster_account_id=data.get('poster_account_id')
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
                            if 'закуп' in acc.get('name', '').lower() or 'оставил' in acc.get('name', '').lower():
                                account_id_poster = int(acc['account_id'])
                                break

                        if not account_id_poster and poster_accounts:
                            account_id_poster = int(poster_accounts[0]['account_id'])

                        ingredients = []
                        for item in acc_items:
                            ingredients.append({
                                'id': item['poster_ingredient_id'],
                                'num': float(item['quantity']),
                                'price': float(item['price_per_unit'])
                            })

                        supply_date = draft.get('invoice_date') or datetime.now().strftime('%Y-%m-%d')

                        supply_id = await client.create_supply(
                            supplier_id=supplier_id,
                            storage_id=1,
                            date=f"{supply_date} 12:00:00",
                            ingredients=ingredients,
                            account_id=account_id_poster,
                            comment=f"Накладная от {draft.get('supplier_name', 'поставщика')}"
                        )

                        return supply_id
                    finally:
                        await client.close()

                supply_id = loop.run_until_complete(create_supply_in_poster())
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

            # Sort accounts: primary first (PizzBurg takes priority for deduplication)
            accounts_sorted = sorted(accounts, key=lambda a: (not a.get('is_primary', False), a['id']))

            seen_names = set()

            for acc in accounts_sorted:
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
                        name = ing.get('ingredient_name', '')
                        name_lower = name.lower()

                        # Skip if already seen (primary account takes priority)
                        if name_lower in seen_names:
                            continue
                        seen_names.add(name_lower)

                        items.append({
                            'id': int(ing.get('ingredient_id', 0)),
                            'name': name,
                            'type': 'ingredient',
                            'poster_account_id': acc['id'],
                            'poster_account_name': acc['account_name']
                        })

                    loop.close()
                except Exception as e:
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


@app.route('/supplies/delete-item/<int:item_id>', methods=['POST'])
def delete_supply_item(item_id):
    """Delete a supply draft item"""
    db = get_database()
    success = db.delete_supply_draft_item(item_id)
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
                                if 'kaspi' in acc.get('name', '').lower():
                                    account_id = int(acc['account_id'])
                                    break
                        else:
                            for acc in finance_accounts:
                                if 'закуп' in acc.get('name', '').lower() or 'оставил' in acc.get('name', '').lower():
                                    account_id = int(acc['account_id'])
                                    break

                    if not account_id and finance_accounts:
                        account_id = int(finance_accounts[0]['account_id'])

                    # Prepare ingredients for this account
                    ingredients = []
                    for item in account_items:
                        ingredients.append({
                            'id': item['poster_ingredient_id'],
                            'num': float(item['quantity']),
                            'price': float(item['price_per_unit'])
                        })

                    # Create supply
                    supply_date = draft.get('invoice_date') or datetime.now().strftime('%Y-%m-%d')

                    supply_id = await client.create_supply(
                        supplier_id=supplier_id,
                        storage_id=1,
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
                        print(f"✅ Created supply #{supply_id} in {account['account_name']}: {len(account_items)} items, {account_total}₸")

                finally:
                    await client.close()

            return created_supplies

        created_supplies = loop.run_until_complete(create_supplies_in_poster())
        loop.close()

        if created_supplies:
            # Mark draft as processed
            db.mark_supply_draft_processed(draft_id)

            # Also mark linked expense draft if exists
            if draft.get('linked_expense_draft_id'):
                db.mark_drafts_processed([draft['linked_expense_draft_id']])

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
    """Get Poster data for shift closing (sales, bonuses, cash, card payments)"""
    date = request.args.get('date')  # Format: YYYYMMDD

    try:
        from poster_client import PosterClient
        db = get_database()
        accounts = db.get_accounts(g.user_id)

        if not accounts:
            return jsonify({'error': 'No Poster accounts configured'}), 400

        # Use primary account
        account = next((acc for acc in accounts if acc.get('is_primary')), accounts[0])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def get_poster_data():
            client = PosterClient(
                telegram_user_id=g.user_id,
                poster_token=account['poster_token'],
                poster_user_id=account['poster_user_id'],
                poster_base_url=account['poster_base_url']
            )

            try:
                if date is None:
                    date_param = datetime.now().strftime("%Y%m%d")
                else:
                    date_param = date

                # Get transactions for the day (sales data)
                result = await client._request('GET', 'dash.getTransactions', params={
                    'dateFrom': date_param,
                    'dateTo': date_param
                })

                transactions = result.get('response', [])

                # Filter only closed orders (status='2')
                closed_transactions = [tx for tx in transactions if tx.get('status') == '2']

                # Calculate totals
                total_cash = 0      # Наличные
                total_card = 0      # Картой (безнал в Poster)
                total_sum = 0       # Общая сумма заказов
                bonus_total = 0     # Бонусы (онлайн-оплата)

                for tx in closed_transactions:
                    cash = int(tx.get('payed_cash', 0))
                    card = int(tx.get('payed_card', 0))
                    total = int(tx.get('payed_sum', 0))
                    bonus = int(tx.get('payed_bonus', 0))

                    total_cash += cash
                    total_card += card
                    total_sum += total
                    bonus_total += bonus

                # Trade total = cash + card (without bonuses)
                trade_total = total_cash + total_card

                # Try to get shift start balance from finance API
                shift_start = 0
                try:
                    # Get today's finance report for cash drawer opening balance
                    finance_result = await client._request('GET', 'finance.getReport', params={
                        'dateFrom': date_param,
                        'dateTo': date_param
                    })
                    # Parse shift start if available
                    report = finance_result.get('response', {})
                    if isinstance(report, dict):
                        shift_start = int(report.get('cash_shift_open', 0))
                except Exception as e:
                    print(f"Could not get shift start: {e}")
                    shift_start = 0

                return {
                    'success': True,
                    'date': date_param,
                    'total_sum': total_sum,              # Общая сумма (включая бонусы) - в тийинах
                    'trade_total': trade_total,          # Торговля за день (без бонусов) - в тийинах
                    'bonus': bonus_total,                # Бонусы (онлайн-оплата) - в тийинах
                    'poster_cash': total_cash,           # Наличка в Poster - в тийинах
                    'poster_card': total_card,           # Безнал в Poster (картой) - в тийинах
                    'shift_start': shift_start,          # Остаток на начало смены - в тийинах
                    'transactions_count': len(closed_transactions)
                }

            finally:
                await client.close()

        data = loop.run_until_complete(get_poster_data())
        loop.close()

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
        deposits = float(data.get('deposits', 0))
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

        # 3. Итого фактический = Фактический - Смена + Расходы - Внесения
        fact_adjusted = fact_total - shift_start - deposits + expenses

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
                'deposits': deposits,
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
