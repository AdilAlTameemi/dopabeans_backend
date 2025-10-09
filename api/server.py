from fastapi import FastAPI
import sqlite3
import os

MERCHANT_KEY = "d57181cc-9f60-11f0-a37e-563fa6bd0e58"
MERCHANT_PASS = "aafc6a570e8193c5525a7e0c207d05e5"
PAYMENT_URL = "https://checkout.totalpay.global/api/v1/session"

app = FastAPI()

@app.get("/api/health")
def health_check():
    # Debug: check if /tmp is writable
    try:
        with open("/tmp/test_write.txt", "w") as f:
            f.write("hello")
        return {"status": "ok", "writable": True}
    except Exception as e:
        return {"status": "fail", "error": str(e)}

@app.get("/api/init-db")
def init_db_route():
    try:
        db_path = "/tmp/orders.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT
            )
        """)
        conn.commit()
        conn.close()
        return {"status": "db_created", "path": db_path}
    except Exception as e:
        return {"status": "fail", "error": str(e)}
    
    
    # Test deploy trigger