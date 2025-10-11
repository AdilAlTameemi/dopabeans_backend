@app.post("/api/create-payment-session")
def create_payment_session(order: OrderRequest):
    order_number = f"DB-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    description = f"{order.product} x{order.quantity} ({order.milk_type}, {order.order_type})"

    # Build hash according to TotalPay spec
    hash_string = f"{MERCHANT_KEY}{order_number}{order.amount:.2f}AED{description}{MERCHANT_PASS}"
    hashed = hashlib.sha1(hashlib.md5(hash_string.encode()).hexdigest().upper().encode()).hexdigest()

    # Build full payload (include required fields: methods, customer, billing_address, recurring_init)
    payload = {
        "merchant_key": MERCHANT_KEY,
        "operation": "purchase",
        "methods": ["card"],
        "order": {
            "number": order_number,
            "amount": f"{order.amount:.2f}",
            "currency": "AED",
            "description": description
        },
        "success_url": "https://www.dopabeansuae.com/payment-success",
        "cancel_url": "https://www.dopabeansuae.com/payment-cancel",
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

    # Send request to TotalPay with timeout and error handling
    try:
        response = requests.post(PAYMENT_URL, json=payload, timeout=15)
    except requests.RequestException as e:
        # network-level failure
        print("TotalPay request failed:", str(e))
        raise HTTPException(status_code=502, detail="Failed to connect to payment gateway")

    # Accept successful 200/201 responses; otherwise surface helpful logs
    if response.status_code not in (200, 201):
        print("TotalPay returned non-200 status", response.status_code)
        print("Response body:", response.text[:2000])  # log first part of body for debugging
        raise HTTPException(status_code=502, detail="Payment provider returned error")

    # Robust JSON parsing
    try:
        body = response.json()
    except ValueError:
        print("TotalPay returned non-JSON response:", response.text[:2000])
        raise HTTPException(status_code=502, detail="Invalid response from payment provider")

    redirect_url = body.get("redirect_url")
    if not redirect_url:
        print("Missing redirect_url in TotalPay response:", body)
        raise HTTPException(status_code=500, detail="Missing redirect URL")

    # Persist order in SQLite
    cursor.execute("""
        INSERT INTO orders (order_number, product, milk_type, order_type, quantity, amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_number, order.product, order.milk_type, order.order_type,
        order.quantity, order.amount, "pending", datetime.utcnow().isoformat()
    ))
    conn.commit()

    return {"redirect_url": redirect_url, "order_number": order_number}
