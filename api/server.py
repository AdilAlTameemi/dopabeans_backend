# api/server.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import hashlib
import requests
import sqlite3
from datetime import datetime

app = FastAPI()

# SQLite setup
conn = sqlite3.connect("orders.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT,
    product TEXT,
    milk_type TEXT,
    order_type TEXT,
    quantity INTEGER,
    amount REAL,
    status TEXT,
    created_at TEXT
)
""")
conn.commit()

# Replace with your actual keys
MERCHANT_KEY = "YOUR_MERCHANT_KEY"
MERCHANT_PASS = "YOUR_MERCHANT_PASS"
PAYMENT_URL = "https://secure.totalpay.global/api/v1/session"
NOTIFICATION_SECRET = "SHARED_SECRET"  # optional if using callback validation

class OrderRequest(BaseModel):
    product: str
    milk_type: str
    order_type: str  # inhouse or takeaway
    quantity: int
    amount: float

@app.post("/api/create-payment-session")
def create_payment_session(order: OrderRequest):
    order_number = f"DB-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    description = f"{order.product} x{order.quantity} ({order.milk_type}, {order.order_type})"

    hash_string = f"{MERCHANT_KEY}{order_number}{order.amount:.2f}AED{description}{MERCHANT_PASS}"
    hashed = hashlib.sha1(hashlib.md5(hash_string.encode()).hexdigest().upper().encode()).hexdigest()

    payload = {
        "merchant_key": MERCHANT_KEY,
        "operation": "purchase",
        "order": {
            "number": order_number,
            "amount": f"{order.amount:.2f}",
            "currency": "AED",
            "description": description
        },
        "success_url": "https://www.dopabeansuae.com/payment-success",
        "cancel_url": "https://www.dopabeansuae.com/payment-cancel",
        "notification_url": "https://your-backend.vercel.app/api/payment-callback",
        "session_expiry": 60,
        "req_token": False,
        "hash": hashed
    }

    response = requests.post(PAYMENT_URL, json=payload)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to connect to payment gateway")

    redirect_url = response.json().get("redirect_url")
    if not redirect_url:
        raise HTTPException(status_code=500, detail="Missing redirect URL")

    cursor.execute("""
        INSERT INTO orders (order_number, product, milk_type, order_type, quantity, amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_number, order.product, order.milk_type, order.order_type,
        order.quantity, order.amount, "pending", datetime.utcnow().isoformat()
    ))
    conn.commit()

    return {"redirect_url": redirect_url, "order_number": order_number}

@app.post("/api/payment-callback")
async def payment_callback(request: Request):
    data = await request.json()
    order_number = data.get("order_number")
    order_status = data.get("order_status")

    if order_number and order_status:
        cursor.execute("UPDATE orders SET status = ? WHERE order_number = ?", (order_status, order_number))
        conn.commit()
        return JSONResponse({"message": "Callback processed"})
    raise HTTPException(status_code=400, detail="Invalid callback payload")

@app.get("/api/order-status/{order_number}")
def get_order_status(order_number: str):
    cursor.execute("SELECT status FROM orders WHERE order_number = ?", (order_number,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_number": order_number, "status": row[0]}