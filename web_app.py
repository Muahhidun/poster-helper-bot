"""Flask web application for managing ingredient aliases"""
import os
import csv
import secrets
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Starting Poster Helper Web Interface")
    print("=" * 60)
    print(f"📂 Data directory: {config.DATA_DIR}")
    print(f"👤 User ID: {TELEGRAM_USER_ID}")
    print(f"🌐 Access at: http://localhost:5000/aliases")
    print("=" * 60)

    app.run(debug=True, port=5000, host='0.0.0.0')
