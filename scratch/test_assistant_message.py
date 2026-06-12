import sys
import os
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_app import app
from database import get_database

def test_message():
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
    message_text = (
        "Так смотри что касается каспи Pay по выписке у меня будет сегодня 13 750 лаваши Астана 36 900 "
        "идея 276 000 донер мяса Караганда 310 единовременной расход Старый инк распечатка 90 090 "
        "Ирин 24 722 Coca-Cola в отдел Питтсбург кафе будет и 224 тыщи 580 Coca-Cola в отдел Питтсбург "
        "55 600 это Ирин пицца соусы будут 1700 маркетинговые расход он кажется уже у нас есть "
        "и 73 902 кускус филе и крыло будет"
    )
    
    print(f"Testing api_assistant_message with user_id: {user_id}")
    
    # Run request context
    with app.test_request_context(
        '/api/assistant/message',
        method='POST',
        data={
            'message': message_text,
            'date': '2026-06-05'
        }
    ):
        from flask import g, session
        session['telegram_user_id'] = user_id
        g.user_id = user_id
        
        from web_app import api_assistant_message
        try:
            response = api_assistant_message()
            print("Successfully processed api_assistant_message!")
            if hasattr(response, 'get_data'):
                data = response.get_data(as_text=True)
                print("Response data:")
                # Pretty print JSON if possible
                try:
                    js = json.loads(data)
                    print(json.dumps(js, indent=2, ensure_ascii=False)[:1000])
                except Exception:
                    print(data[:1000])
            else:
                print("Response is not a standard Response object:", type(response))
        except Exception as e:
            import traceback
            print("❌ Error caught during api_assistant_message execution:")
            traceback.print_exc()

if __name__ == '__main__':
    test_message()
