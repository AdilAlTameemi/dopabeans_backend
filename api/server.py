from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import hashlib
import requests
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# CORS configuration so the frontend can reach the API.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://dopabeansuae.com",
    "https://www.dopabeansuae.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

if USE_POSTGRES:
    # Ensure SSL is enforced for Supabase connections if not already specified.
    if "sslmode" not in DATABASE_URL:
        DATABASE_URL = f"{DATABASE_URL}?sslmode=require"

    import psycopg2

    PLACEHOLDER = "%s"
else:
    import sqlite3

    DB_PATH = os.path.join(os.path.dirname(__file__), "orders.db")
    PLACEHOLDER = "?"


def get_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DB_PATH)


def init_db():
    create_table_sql = """
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
    """

    if USE_POSTGRES:
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                order_number TEXT UNIQUE,
                product TEXT,
                milk_type TEXT,
                order_type TEXT,
                quantity INTEGER,
                amount NUMERIC,
                status TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(create_table_sql)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def execute_non_query(query, params=()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def fetch_one(query, params=()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.commit()
        return row
    finally:
        cursor.close()
        conn.close()


init_db()

INSERT_ORDER_SQL = f"""
    INSERT INTO orders (order_number, product, milk_type, order_type, quantity, amount, status, created_at)
    VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
"""

UPDATE_ORDER_SQL = f"UPDATE orders SET status = {PLACEHOLDER} WHERE order_number = {PLACEHOLDER}"
SELECT_ORDER_SQL = f"SELECT status FROM orders WHERE order_number = {PLACEHOLDER}"

# TotalPay sandbox credentials
MERCHANT_KEY = "d57181cc-9f60-11f0-a37e-563fa6bd0e58"
MERCHANT_PASS = "aafc6a570e8193c5525a7e0c207d05e5"
PAYMENT_URL = "https://checkout.totalpay.global/api/v1/session"

# Order schema
class OrderRequest(BaseModel):
    product: str
    milk_type: str
    order_type: str  # "inhouse" or "takeaway"
    quantity: int
    amount: float

@app.post("/api/create-payment-session")
def create_payment_session(order: OrderRequest):
    order_number = f"DB-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    description = f"{order.product} x{order.quantity} ({order.milk_type}, {order.order_type})"
    amount_str = f"{order.amount:.2f}"
    # Authentication signature: sha1(md5(strtoupper(order.number + order.amount + order.currency + order.description + merchant.pass)))
    hash_string = f"{order_number}{amount_str}AED{description}{MERCHANT_PASS}"
    md5_hex = hashlib.md5(hash_string.upper().encode("utf-8")).hexdigest()
    hashed = hashlib.sha1(md5_hex.encode("utf-8")).hexdigest()

    payload = {
        "merchant_key": MERCHANT_KEY,
        "operation": "purchase",
        "methods": ["card"],
        "order": {
            "number": order_number,
            "amount": amount_str,
            "currency": "AED",
            "description": description
        },
        "success_url": "https://dopabeansuae.com/payment-success",
        "cancel_url": "https://dopabeansuae.com/payment-cancel",
        "notification_url": "https://dopabeans-backend.onrender.com/api/payment-callback",
        "session_expiry": 60,
        "req_token": False,
        "recurring_init": "true",
        "customer": {
            "name": "Test Customer",
            "email": "test@example.com"
        },
        "billing_address": {
            "country": "AE",
            "state": "DU",
            "city": "Dubai",
            "address": "Sheikh Zayed Road, Tower 21",
            "zip": "00000",
            "phone": "0501234567"
        },
        "hash": hashed
    }

    try:
        response = requests.post(PAYMENT_URL, json=payload, timeout=15)
    except requests.RequestException as e:
        print("TotalPay request failed:", str(e))
        raise HTTPException(status_code=502, detail="Failed to connect to payment gateway")

    if response.status_code not in (200, 201):
        print("TotalPay returned non-200 status", response.status_code)
        print("Response body:", response.text[:2000])
        raise HTTPException(status_code=502, detail="Payment provider returned error")

    try:
        body = response.json()
    except ValueError:
        print("TotalPay returned non-JSON response:", response.text[:2000])
        raise HTTPException(status_code=502, detail="Invalid response from payment provider")

    redirect_url = body.get("redirect_url")
   if not redirect_url:
       print("Missing redirect_url in TotalPay response:", body)
       raise HTTPException(status_code=500, detail="Missing redirect URL")

    execute_non_query(
        INSERT_ORDER_SQL,
        (
            order_number,
            order.product,
            order.milk_type,
            order.order_type,
            order.quantity,
            order.amount,
            "pending",
            datetime.utcnow().isoformat()
        )
    )

    return {"redirect_url": redirect_url, "order_number": order_number}

@app.post("/api/payment-callback")
async def payment_callback(request: Request):
    data = await request.json()
    order_number = data.get("order_number")
    order_status = data.get("order_status")

    if order_number and order_status:
        execute_non_query(UPDATE_ORDER_SQL, (order_status, order_number))
        return JSONResponse({"message": "Callback processed"})
    raise HTTPException(status_code=400, detail="Invalid callback payload")

@app.get("/api/order-status/{order_number}")
def get_order_status(order_number: str):
    row = fetch_one(SELECT_ORDER_SQL, (order_number,))
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    status_value = row[0] if isinstance(row, (tuple, list)) else row
    return {"order_number": order_number, "status": status_value}

@app.get("/api/health")
def health_check():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        return {"status": "ok", "writable": True}
    except Exception:
        return {"status": "error", "writable": False}
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass
