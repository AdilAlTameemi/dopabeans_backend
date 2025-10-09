from fastapi import FastAPI
import sqlite3
import os

app = FastAPI()

@app.get("/api/health")
def health_check():
    # Debug: check /tmp is writable
    try:
        test_path = "/tmp/test_write.txt"
        with open(test_path, "w") as f:
            f.write("hello")
        return {"status": "ok", "writable": True, "path": test_path}
    except Exception as e:
        return {"status": "fail", "error": str(e)}

# Now move DB init *into the endpoint* for testing
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