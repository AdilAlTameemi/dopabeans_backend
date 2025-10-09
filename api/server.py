from fastapi import FastAPI
import sqlite3
import os

app = FastAPI()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "orders.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT,
            product_name TEXT,
            quantity INTEGER,
            milk_type TEXT,
            order_type TEXT,  -- inhouse / takeaway
            payment_status TEXT,
            total_amount REAL,
            payment_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

@app.get("/health")
def health_check():
    return {"status": "ok"}