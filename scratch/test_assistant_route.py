import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_app import app
from database import get_database

def test_route():
    db = get_database()
    # Try to find a valid user in database
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_user_id FROM users LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        print("No users found in database to test with.")
        return
        
    user_id = row[0]
    print(f"Testing view_assistant route with user_id: {user_id}")
    
    # Run request context
    with app.test_request_context('/assistant?date=2026-06-05'):
        from flask import g, session
        session['telegram_user_id'] = user_id
        g.user_id = user_id
        
        # We also need to mock before_request handlers if needed, or call view_assistant directly
        from web_app import view_assistant
        try:
            response = view_assistant()
            print("Successfully rendered view_assistant!")
            # Print a snippet of response if it's a string
            if hasattr(response, 'get_data'):
                data = response.get_data(as_text=True)
                print(f"Response size: {len(data)} characters")
                print("HTML Snippet:")
                print(data[:500])
            else:
                print("Response is not a standard Response object:", type(response))
        except Exception as e:
            import traceback
            print("❌ Error caught during view_assistant execution:")
            traceback.print_exc()

if __name__ == '__main__':
    test_route()
