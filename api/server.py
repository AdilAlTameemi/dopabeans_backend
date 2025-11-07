from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import hashlib
import json
import copy
import requests
from datetime import datetime
from decimal import Decimal
import re
import os
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

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
raw_database_url = os.getenv("DATABASE_URL", "")
DATABASE_URL = raw_database_url.strip()
if DATABASE_URL.lower().startswith("database_url="):
    _, _, remainder = DATABASE_URL.partition("=")
    DATABASE_URL = remainder.strip()

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. Configure it with your Supabase Postgres connection string."
    )
parsed_url = urlparse(DATABASE_URL)
parsed_scheme = (parsed_url.scheme or "").lower()
if "postgres" not in parsed_scheme:
    raise RuntimeError(
        "DATABASE_URL must use a Postgres connection string (postgres:// or postgresql://)."
    )

pool_mode = parse_qs(parsed_url.query or "").get("pool_mode", ["unspecified"])[0]
print(
    f"[startup] Database target host={parsed_url.hostname} port={parsed_url.port or 'default'} "
    f"scheme={parsed_url.scheme} pool_mode={pool_mode}"
)

USE_POSTGRES = True

FOODICS_API_BASE_URL = os.getenv("FOODICS_API_BASE_URL", "https://api.foodics.com/v5")
FOODICS_API_TOKEN = os.getenv("FOODICS_API_TOKEN")
FOODICS_ORDER_BRANCH_ID = (
    os.getenv("FOODICS_ORDER_BRANCH_ID")
    or os.getenv("FOODICS_BRANCH_ID")
    or "a01238c6-deaf-423f-b2e5-f65a7239400c"
)
try:
    DEFAULT_FOODICS_ORDER_SOURCE = int(os.getenv("FOODICS_ORDER_SOURCE", "2"))
except ValueError:
    DEFAULT_FOODICS_ORDER_SOURCE = 2
FOODICS_DEVICE_ID = os.getenv("FOODICS_DEVICE_ID") or "a01238c6-eccb-4463-9566-da418839c0ab"
FOODICS_CREATOR_ID = os.getenv("FOODICS_CREATOR_ID") or "a0451f9f-b2f6-4d92-a5d9-6e99785f7e24"
FOODICS_CLOSER_ID = os.getenv("FOODICS_CLOSER_ID") or "a0451f9f-b2f6-4d92-a5d9-6e99785f7e24"

def get_bool_env(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

RUN_SCHEMA_MIGRATIONS = get_bool_env("RUN_SCHEMA_MIGRATIONS")

import psycopg2

PLACEHOLDER = "%s"

ColumnAccessor = Union[str, Callable[[Dict[str, Any]], Any]]
ColumnConfig = List[Tuple[str, ColumnAccessor]]


FALLBACK_MILK_OPTIONS = [
    {"value": "normal", "label": "Normal Milk"},
    {"value": "oat", "label": "Oat Milk"},
    {"value": "coconut", "label": "Coconut Milk"},
]

FALLBACK_BEAN_OPTIONS = [
    {"value": "brazilian", "label": "Brazilian"},
    {"value": "colombian", "label": "Colombian"},
]

BEST_SELLER_SLUGS = {"dopabeans_matcha", "cream_espresso"}

FOODICS_STATUS_MAP: Dict[str, str] = {
    "1": "draft",
    "2": "created",
    "3": "accepted",
    "4": "closed",
    "5": "void",
}

CATEGORY_OVERRIDES_BY_SLUG: Dict[str, Dict[str, Optional[str]]] = {
    "milkshake": {
        "id": "a0228670-4de6-4f15-bc78-09952ff384f8",
        "reference": "ctgry09",
        "name": "Milkshake",
    },
    "sweets": {
        "id": "a022869b-579b-40e6-acbf-69395bf24331",
        "reference": "ctgry11",
        "name": "Sweets",
    },
    "water": {
        "id": "a02289ed-35d5-4d6a-b5cb-a31af179768e",
        "reference": "ctgry12",
        "name": "Water",
    },
}

PRODUCT_CATEGORY_OVERRIDES: Dict[str, str] = {
    "oreo_milkshake": "milkshake",
    "pistachio_milkshake": "milkshake",
    "caramel_milkshake": "milkshake",
    "mango_milkshake": "milkshake",
    "banana_frappe": "frappe",
    "caramel_frappe": "frappe",
    "oreo_frappe": "frappe",
    "pistachio_frappe": "frappe",
    "banana_pudding": "sweets",
    "mango_cake": "sweets",
    "tiramisu_cake": "sweets",
    "sebastian_cheese_cake": "sweets",
    "choclate_cake": "sweets",
    "molten_aseeda": "sweets",
    "coockie": "sweets",
    "regular_cold_matcha": "matcha",
    "sparkling_water": "water",
    "normal_water": "water",
}

REMOVED_PRODUCT_SLUGS: Set[str] = {
    "coconut_blue_mojito",
    "tiramisu",
    "tiramisu_cake",
    "pdct600",
    "sebastian_cheese_cake",
    "sebastian_cheesecake",
    "pdct610",
}


def get_nested(data: Dict[str, Any], path: str) -> Any:
    keys = path.split(".")
    current: Any = data
    for key in keys:
        if current is None or not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "t"}:
            return True
        if lowered in {"false", "0", "no", "n", "f"}:
            return False
    return None


def to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (float, Decimal)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(float(stripped))
        except ValueError:
            return None
    return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None



def normalize_value_for_db(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if not USE_POSTGRES and isinstance(value, bool):
        return int(value)
    return value


def build_inventory_level_id(record: Dict[str, Any]) -> Optional[str]:
    explicit_id = record.get("id")
    if explicit_id:
        return str(explicit_id)
    branch_id = record.get("branch_id") or get_nested(record, "branch.id")
    item_id = record.get("inventory_item_id") or get_nested(record, "inventory_item.id")
    warehouse_id = record.get("warehouse_id") or get_nested(record, "warehouse.id")
    if branch_id and item_id and warehouse_id:
        return f"{branch_id}:{warehouse_id}:{item_id}"
    if branch_id and item_id:
        return f"{branch_id}:{item_id}"
    return None


def record_to_payload(record: Dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), default=str)


def slugify(value: Optional[str]) -> str:
    if not value:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    slug = re.sub(r"_+", "_", slug)
    return slug.strip("_")


def normalize_identifier(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def extract_branch_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("branch_id") or get_nested(record, "branch.id")
    return str(value) if value else None


def extract_modifier_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("modifier_id") or get_nested(record, "modifier.id")
    return str(value) if value else None


def extract_inventory_item_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("inventory_item_id") or get_nested(record, "inventory_item.id")
    return str(value) if value else None


def extract_warehouse_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("warehouse_id") or get_nested(record, "warehouse.id")
    return str(value) if value else None


def extract_payment_method_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("payment_method_id") or get_nested(record, "payment_method.id")
    return str(value) if value else None


def extract_customer_id(record: Dict[str, Any]) -> Optional[str]:
    value = record.get("customer_id") or get_nested(record, "customer.id")
    return str(value) if value else None


def extract_user_id(record: Dict[str, Any], key: str) -> Optional[str]:
    value = record.get(key)
    if value:
        return str(value)
    nested = get_nested(record, f"{key}.id")
    return str(nested) if nested else None


def extract_primary_branch_association(record: Dict[str, Any]) -> Optional[str]:
    branch_id = extract_branch_id(record)
    if branch_id:
        return branch_id
    branches = record.get("branches")
    if isinstance(branches, list) and branches:
        first = branches[0]
        if isinstance(first, dict):
            candidate = first.get("id")
            if candidate:
                return str(candidate)
    return None


ADDITIONAL_FOODICS_TABLES: Dict[str, Dict[str, List[Tuple[str, str]]]] = {
    "branches": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("reference", "TEXT"),
            ("code", "TEXT"),
            ("status", "TEXT"),
            ("type", "INTEGER"),
            ("timezone", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"),
            ("latitude", "REAL"),
            ("longitude", "REAL"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("reference", "TEXT"),
            ("code", "TEXT"),
            ("status", "TEXT"),
            ("type", "INTEGER"),
            ("timezone", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"),
            ("latitude", "DOUBLE PRECISION"),
            ("longitude", "DOUBLE PRECISION"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "devices": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("reference", "TEXT"),
            ("branch_id", "TEXT"),
            ("code", "TEXT"),
            ("type", "INTEGER"),
            ("status", "TEXT"),
            ("is_active", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("reference", "TEXT"),
            ("branch_id", "TEXT"),
            ("code", "TEXT"),
            ("type", "INTEGER"),
            ("status", "TEXT"),
            ("is_active", "BOOLEAN"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "inventory_items": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("sku", "TEXT"),
            ("barcode", "TEXT"),
            ("item_type", "TEXT"),
            ("category_id", "TEXT"),
            ("cost", "REAL"),
            ("price", "REAL"),
            ("is_active", "INTEGER"),
            ("unit", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("sku", "TEXT"),
            ("barcode", "TEXT"),
            ("item_type", "TEXT"),
            ("category_id", "TEXT"),
            ("cost", "NUMERIC"),
            ("price", "NUMERIC"),
            ("is_active", "BOOLEAN"),
            ("unit", "TEXT"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "inventory_levels": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("branch_id", "TEXT"),
            ("warehouse_id", "TEXT"),
            ("inventory_item_id", "TEXT"),
            ("available", "REAL"),
            ("in_stock", "REAL"),
            ("on_order", "REAL"),
            ("wastage", "REAL"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("branch_id", "TEXT"),
            ("warehouse_id", "TEXT"),
            ("inventory_item_id", "TEXT"),
            ("available", "NUMERIC"),
            ("in_stock", "NUMERIC"),
            ("on_order", "NUMERIC"),
            ("wastage", "NUMERIC"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "inventory_transactions": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("transaction_type", "TEXT"),
            ("inventory_item_id", "TEXT"),
            ("warehouse_id", "TEXT"),
            ("branch_id", "TEXT"),
            ("quantity", "REAL"),
            ("unit_cost", "REAL"),
            ("source_type", "TEXT"),
            ("source_id", "TEXT"),
            ("reference", "TEXT"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("transaction_type", "TEXT"),
            ("inventory_item_id", "TEXT"),
            ("warehouse_id", "TEXT"),
            ("branch_id", "TEXT"),
            ("quantity", "NUMERIC"),
            ("unit_cost", "NUMERIC"),
            ("source_type", "TEXT"),
            ("source_id", "TEXT"),
            ("reference", "TEXT"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "modifiers": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("reference", "TEXT"),
            ("is_active", "INTEGER"),
            ("is_required", "INTEGER"),
            ("min_options", "INTEGER"),
            ("max_options", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("reference", "TEXT"),
            ("is_active", "BOOLEAN"),
            ("is_required", "BOOLEAN"),
            ("min_options", "INTEGER"),
            ("max_options", "INTEGER"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "modifier_options": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("modifier_id", "TEXT"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("price", "REAL"),
            ("cost", "REAL"),
            ("calories", "REAL"),
            ("is_default", "INTEGER"),
            ("sort_order", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("modifier_id", "TEXT"),
            ("name", "TEXT"),
            ("name_localized", "TEXT"),
            ("price", "NUMERIC"),
            ("cost", "NUMERIC"),
            ("calories", "NUMERIC"),
            ("is_default", "BOOLEAN"),
            ("sort_order", "INTEGER"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "payment_methods": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("code", "TEXT"),
            ("method_type", "TEXT"),
            ("is_active", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("code", "TEXT"),
            ("method_type", "TEXT"),
            ("is_active", "BOOLEAN"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "users": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("email", "TEXT"),
            ("phone", "TEXT"),
            ("role", "TEXT"),
            ("status", "TEXT"),
            ("branch_id", "TEXT"),
            ("is_active", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("name", "TEXT"),
            ("email", "TEXT"),
            ("phone", "TEXT"),
            ("role", "TEXT"),
            ("status", "TEXT"),
            ("branch_id", "TEXT"),
            ("is_active", "BOOLEAN"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "foodics_orders": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("code", "TEXT"),
            ("number", "TEXT"),
            ("status", "TEXT"),
            ("order_type", "TEXT"),
            ("branch_id", "TEXT"),
            ("customer_id", "TEXT"),
            ("cashier_id", "TEXT"),
            ("device_id", "TEXT"),
            ("payment_method_id", "TEXT"),
            ("source", "TEXT"),
            ("total", "REAL"),
            ("subtotal", "REAL"),
            ("discount", "REAL"),
            ("tax", "REAL"),
            ("service_charge", "REAL"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("closed_at", "TEXT"),
            ("deleted_at", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("code", "TEXT"),
            ("number", "TEXT"),
            ("status", "TEXT"),
            ("order_type", "TEXT"),
            ("branch_id", "TEXT"),
            ("customer_id", "TEXT"),
            ("cashier_id", "TEXT"),
            ("device_id", "TEXT"),
            ("payment_method_id", "TEXT"),
            ("source", "TEXT"),
            ("total", "NUMERIC"),
            ("subtotal", "NUMERIC"),
            ("discount", "NUMERIC"),
            ("tax", "NUMERIC"),
            ("service_charge", "NUMERIC"),
            ("created_at", "TIMESTAMP"),
            ("updated_at", "TIMESTAMP"),
            ("closed_at", "TIMESTAMP"),
            ("deleted_at", "TIMESTAMP"),
            ("raw_payload", "TEXT"),
        ],
    },
    "product_modifiers": {
        "sqlite": [
            ("id", "TEXT PRIMARY KEY"),
            ("modifier_id", "TEXT"),
            ("product_id", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
        "postgres": [
            ("id", "TEXT PRIMARY KEY"),
            ("modifier_id", "TEXT"),
            ("product_id", "TEXT"),
            ("raw_payload", "TEXT"),
        ],
    },
}


def create_table_if_not_exists(cursor, table_name: str, columns: List[Tuple[str, str]]):
    definitions = ", ".join(f"{name} {col_type}" for name, col_type in columns)
    cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({definitions})")


def ensure_foodics_table(cursor, table_name: str):
    schema = ADDITIONAL_FOODICS_TABLES[table_name]
    if USE_POSTGRES:
        columns = schema["postgres"]
        create_table_if_not_exists(cursor, table_name, columns)
        for name, col_type in columns[1:]:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {name} {col_type}")
            if "TEXT" in col_type.upper():
                cursor.execute(
                    f"ALTER TABLE {table_name} ALTER COLUMN {name} DROP NOT NULL"
                )

        text_columns = [name for name, col_type in columns if "TEXT" in col_type.upper()]
        for name in text_columns:
            if name == "id":
                cursor.execute(
                    f"""
                    DO $$
                    DECLARE
                        rec record;
                    BEGIN
                        FOR rec IN (
                            SELECT tc.table_name, tc.constraint_name
                            FROM information_schema.table_constraints tc
                            JOIN information_schema.constraint_column_usage ccu
                                ON tc.constraint_name = ccu.constraint_name
                                AND tc.constraint_schema = ccu.constraint_schema
                            WHERE tc.constraint_type = 'FOREIGN KEY'
                                AND ccu.table_name = '{table_name}'
                                AND ccu.column_name = '{name}'
                                AND tc.constraint_schema = current_schema()
                        ) LOOP
                            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', rec.table_name, rec.constraint_name);
                        END LOOP;
                    END
                    $$;
                    """
                )

            cursor.execute(
                f"""
                DO $$
                BEGIN
                    BEGIN
                        ALTER TABLE {table_name} ALTER COLUMN {name} TYPE TEXT USING {name}::text;
                    EXCEPTION WHEN undefined_column THEN
                        NULL;
                    WHEN others THEN
                        NULL;
                    END;
                END
                $$;
                """
            )
    else:
        columns = schema["sqlite"]
        create_table_if_not_exists(cursor, table_name, columns)
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        for name, col_type in columns[1:]:
            if name not in existing_columns:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {col_type}")

def get_connection():
    parsed = urlparse(DATABASE_URL)
    query = parse_qs(parsed.query or "")
    sslmode = query.get("sslmode", ["require"])[0] or "require"

    connect_kwargs = {
        "dbname": parsed.path.lstrip("/") or "postgres",
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "sslmode": sslmode,
        "connect_timeout": int(query.get("connect_timeout", [10])[0] or 10)
    }

    # Forward additional query params except for sslmode/connect_timeout.
    for key, value in query.items():
        if key in {"sslmode", "connect_timeout"}:
            continue
        connect_kwargs[key] = value[-1]

    return psycopg2.connect(**connect_kwargs)


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

    create_categories_sql = """
        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            name TEXT,
            name_localized TEXT,
            reference TEXT,
            image TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """

    if USE_POSTGRES:
        create_categories_sql = """
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT,
                name_localized TEXT,
                reference TEXT,
                image TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                deleted_at TIMESTAMP
            )
        """

    create_products_sql = """
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT,
            name_localized TEXT,
            reference TEXT,
            sku TEXT,
            barcode TEXT,
            description TEXT,
            image TEXT,
            is_active INTEGER,
            price REAL,
            cost REAL,
            sort_order INTEGER,
            category_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT,
            raw_payload TEXT
        )
    """

    if USE_POSTGRES:
        create_products_sql = """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT,
                name_localized TEXT,
                reference TEXT,
                sku TEXT,
                barcode TEXT,
                description TEXT,
                image TEXT,
                is_active BOOLEAN,
                price NUMERIC,
                cost NUMERIC,
                sort_order INTEGER,
                category_id TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                deleted_at TIMESTAMP,
                raw_payload TEXT
            )
        """

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(create_table_sql)
        cursor.execute(create_categories_sql)
        cursor.execute(create_products_sql)

        if USE_POSTGRES and RUN_SCHEMA_MIGRATIONS:
            cursor.execute(
                """
                DO $$
                DECLARE
                    constraint_name text;
                BEGIN
                    SELECT tc.constraint_name INTO constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.constraint_schema = kcu.constraint_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_name = 'products'
                        AND kcu.column_name = 'category_id'
                        AND tc.constraint_schema = current_schema();

                    IF constraint_name IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE products DROP CONSTRAINT %I', constraint_name);
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                DECLARE
                    constraint_name text;
                BEGIN
                    SELECT tc.constraint_name INTO constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.constraint_schema = kcu.constraint_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_name = 'order_items'
                        AND kcu.column_name = 'product_id'
                        AND tc.constraint_schema = current_schema();

                    IF constraint_name IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE order_items DROP CONSTRAINT %I', constraint_name);
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                DECLARE
                    constraint_name text;
                BEGIN
                    SELECT tc.constraint_name INTO constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.constraint_schema = kcu.constraint_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_name = 'product_ingredients'
                        AND kcu.column_name = 'product_id'
                        AND tc.constraint_schema = current_schema();

                    IF constraint_name IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE product_ingredients DROP CONSTRAINT %I', constraint_name);
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'products'
                        AND column_name = 'category_id'
                        AND data_type <> 'text'
                        AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE products ALTER COLUMN category_id TYPE TEXT USING category_id::text';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'order_items'
                        AND column_name = 'product_id'
                        AND data_type <> 'text'
                        AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE order_items ALTER COLUMN product_id TYPE TEXT USING product_id::text';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'product_ingredients'
                        AND column_name = 'product_id'
                        AND data_type <> 'text'
                        AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE product_ingredients ALTER COLUMN product_id TYPE TEXT USING product_id::text';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    BEGIN
                        ALTER TABLE products ALTER COLUMN id TYPE TEXT USING id::text;
                    EXCEPTION WHEN undefined_column THEN
                        NULL;
                    END;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    BEGIN
                        ALTER TABLE categories DROP CONSTRAINT categories_name_key;
                    EXCEPTION WHEN undefined_object THEN
                        NULL;
                    END;

                    BEGIN
                        ALTER TABLE categories ALTER COLUMN id TYPE TEXT USING id::text;
                    EXCEPTION WHEN undefined_column THEN
                        -- column missing; ignore
                        NULL;
                    END;
                END
                $$;
                """
            )
            cursor.execute(
                """
                ALTER TABLE categories
                ADD COLUMN IF NOT EXISTS name TEXT,
                ADD COLUMN IF NOT EXISTS name_localized TEXT,
                ADD COLUMN IF NOT EXISTS reference TEXT,
                ADD COLUMN IF NOT EXISTS image TEXT,
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP
                """
            )
            cursor.execute(
                """
                ALTER TABLE products
                ADD COLUMN IF NOT EXISTS name TEXT,
                ADD COLUMN IF NOT EXISTS name_localized TEXT,
                ADD COLUMN IF NOT EXISTS reference TEXT,
                ADD COLUMN IF NOT EXISTS sku TEXT,
                ADD COLUMN IF NOT EXISTS barcode TEXT,
                ADD COLUMN IF NOT EXISTS description TEXT,
                ADD COLUMN IF NOT EXISTS image TEXT,
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN,
                ADD COLUMN IF NOT EXISTS price NUMERIC,
                ADD COLUMN IF NOT EXISTS cost NUMERIC,
                ADD COLUMN IF NOT EXISTS sort_order INTEGER,
                ADD COLUMN IF NOT EXISTS category_id TEXT,
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP,
                ADD COLUMN IF NOT EXISTS raw_payload TEXT
                """
            )
            cursor.execute(
                """
                DO $$
                DECLARE
                    fk_exists BOOLEAN;
                BEGIN
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                            AND tc.constraint_schema = kcu.constraint_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                            AND tc.table_name = 'products'
                            AND kcu.column_name = 'category_id'
                            AND tc.constraint_schema = current_schema()
                    ) INTO fk_exists;

                    IF NOT fk_exists THEN
                        EXECUTE 'ALTER TABLE products
                                 ADD CONSTRAINT products_category_id_fkey
                                 FOREIGN KEY (category_id)
                        REFERENCES categories(id)
                                 ON DELETE SET NULL';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                DECLARE
                    fk_exists BOOLEAN;
                BEGIN
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                            AND tc.constraint_schema = kcu.constraint_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                            AND tc.table_name = 'order_items'
                            AND kcu.column_name = 'product_id'
                            AND tc.constraint_schema = current_schema()
                    ) INTO fk_exists;

                    IF NOT fk_exists THEN
                        EXECUTE 'ALTER TABLE order_items
                                 ADD CONSTRAINT order_items_product_id_fkey
                                 FOREIGN KEY (product_id)
                                 REFERENCES products(id)
                                 ON DELETE CASCADE';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                DECLARE
                    fk_exists BOOLEAN;
                BEGIN
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                            AND tc.constraint_schema = kcu.constraint_schema
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                            AND tc.table_name = 'product_ingredients'
                            AND kcu.column_name = 'product_id'
                            AND tc.constraint_schema = current_schema()
                    ) INTO fk_exists;

                    IF NOT fk_exists THEN
                        EXECUTE 'ALTER TABLE product_ingredients
                                 ADD CONSTRAINT product_ingredients_product_id_fkey
                                 FOREIGN KEY (product_id)
                                 REFERENCES products(id)
                                 ON DELETE CASCADE';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_number TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS product TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS milk_type TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_type TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity INTEGER")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS amount NUMERIC")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_id TEXT")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_reference TEXT")
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE schemaname = current_schema()
                            AND indexname = 'orders_external_id_idx'
                    ) THEN
                        EXECUTE 'CREATE UNIQUE INDEX orders_external_id_idx ON orders(external_id) WHERE external_id IS NOT NULL';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'orders_source_check'
                            AND table_name = 'orders'
                            AND constraint_type = 'CHECK'
                            AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE orders DROP CONSTRAINT orders_source_check';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'orders_source_check'
                            AND table_name = 'orders'
                            AND constraint_type = 'CHECK'
                            AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source = ANY (ARRAY[''website''::text, ''foodics''::text, ''whatsapp''::text, ''qr''::text]))';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'orders_status_check'
                            AND table_name = 'orders'
                            AND constraint_type = 'CHECK'
                            AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE orders DROP CONSTRAINT orders_status_check';
                    END IF;
                END
                $$;
                """
            )
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'orders_status_check'
                            AND table_name = 'orders'
                            AND constraint_type = 'CHECK'
                            AND table_schema = current_schema()
                    ) THEN
                        EXECUTE 'ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IS NULL OR char_length(status) > 0)';
                    END IF;
                END
                $$;
                """
            )
            print("[startup] Postgres schema migration checks completed (RUN_SCHEMA_MIGRATIONS=true).")
        elif USE_POSTGRES:
            print("[startup] RUN_SCHEMA_MIGRATIONS is disabled; skipping Postgres schema migration checks.")
        else:
            cursor.execute("PRAGMA table_info(categories)")
            category_columns = {row[1] for row in cursor.fetchall()}
            sqlite_category_columns = {
                "name": "TEXT",
                "name_localized": "TEXT",
                "reference": "TEXT",
                "image": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
                "deleted_at": "TEXT",
            }
            for column_name, column_type in sqlite_category_columns.items():
                if column_name not in category_columns:
                    cursor.execute(f"ALTER TABLE categories ADD COLUMN {column_name} {column_type}")

            cursor.execute("PRAGMA table_info(products)")
            product_columns = {row[1] for row in cursor.fetchall()}
            sqlite_product_columns = {
                "name": "TEXT",
                "name_localized": "TEXT",
                "reference": "TEXT",
                "sku": "TEXT",
                "barcode": "TEXT",
                "description": "TEXT",
                "image": "TEXT",
                "is_active": "INTEGER",
                "price": "REAL",
                "cost": "REAL",
                "sort_order": "INTEGER",
                "category_id": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
                "deleted_at": "TEXT",
                "raw_payload": "TEXT",
            }
            for column_name, column_type in sqlite_product_columns.items():
                if column_name not in product_columns:
                    cursor.execute(f"ALTER TABLE products ADD COLUMN {column_name} {column_type}")

        for table_name in ADDITIONAL_FOODICS_TABLES:
            ensure_foodics_table(cursor, table_name)

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


def fetch_all(query, params=()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.commit()
        return rows
    finally:
        cursor.close()
        conn.close()


def fetch_all_dict(query, params=()):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        conn.commit()
        result = []
        for row in rows:
            result.append({column: row[index] for index, column in enumerate(columns)})
        return result
    finally:
        cursor.close()
        conn.close()


def upsert_generic(records: List[Dict[str, Any]], table_name: str, columns: ColumnConfig, context: Optional[Dict[str, Any]] = None):
    if not records:
        return {"inserted": 0, "updated": 0}

    processed_rows: List[Tuple[str, Tuple[Any, ...]]] = []
    column_names = [name for name, _ in columns]

    for record in records:
        row_values: List[Any] = []
        row_id: Optional[str] = None
        skip = False

        for column_name, accessor in columns:
            if callable(accessor):
                value = accessor(record)
            else:
                value = get_nested(record, accessor)

            if column_name == "id":
                if value is None or value == "":
                    skip = True
                    break
                row_id = str(value)
                value = row_id

            row_values.append(value)

        if skip or row_id is None:
            continue

        normalized_values = tuple(normalize_value_for_db(value) for value in row_values)
        processed_rows.append((row_id, normalized_values))

    if not processed_rows:
        return {"inserted": 0, "updated": 0}

    ids = [row_id for row_id, _ in processed_rows]
    if USE_POSTGRES:
        lookup_query = f"SELECT id::text FROM {table_name} WHERE id::text = ANY(%s)"
        rows = fetch_all(lookup_query, (ids,))
    else:
        placeholders = ",".join([PLACEHOLDER] * len(ids))
        lookup_query = f"SELECT id FROM {table_name} WHERE id IN ({placeholders})"
        rows = fetch_all(lookup_query, ids)

    existing_ids = {row[0] for row in rows}

    placeholders_sql = ", ".join([PLACEHOLDER] * len(column_names))
    insert_columns_sql = ", ".join(column_names)
    set_clauses = ", ".join(f"{col} = EXCLUDED.{col}" for col in column_names if col != "id")

    if set_clauses:
        upsert_sql = (
            f"INSERT INTO {table_name} ({insert_columns_sql}) "
            f"VALUES ({placeholders_sql}) ON CONFLICT(id) DO UPDATE SET {set_clauses}"
        )
    else:
        upsert_sql = (
            f"INSERT INTO {table_name} ({insert_columns_sql}) "
            f"VALUES ({placeholders_sql}) ON CONFLICT(id) DO NOTHING"
        )

    inserted = 0
    updated = 0

    conn = get_connection()
    try:
        cursor = conn.cursor()
        for row_id, values in processed_rows:
            cursor.execute(upsert_sql, values)
            if row_id in existing_ids:
                updated += 1
            else:
                inserted += 1
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"inserted": inserted, "updated": updated}


def fetch_foodics_collection(resource: str):
    if not FOODICS_API_TOKEN:
        raise HTTPException(status_code=500, detail="Foodics API token is not configured")

    url = f"{FOODICS_API_BASE_URL.rstrip('/')}/{resource.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {FOODICS_API_TOKEN}",
        "Accept": "application/json",
    }

    records_accumulator = []

    while url:
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach Foodics API: {exc}") from exc

        if response.status_code != 200:
            detail = response.text
            raise HTTPException(status_code=response.status_code, detail=f"Foodics API error: {detail}")

        payload = response.json()
        records = payload.get("data", [])
        records_accumulator.extend(records)
        links = payload.get("links", {})
        url = links.get("next")

    return records_accumulator


def post_foodics_resource(resource: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    if not FOODICS_API_TOKEN:
        raise HTTPException(status_code=500, detail="Foodics API token is not configured")

    url = f"{FOODICS_API_BASE_URL.rstrip('/')}/{resource.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {FOODICS_API_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Foodics API: {exc}") from exc

    if response.status_code >= 400:
        try:
            error_body = response.json()
            detail_message = (
                error_body.get("detail")
                or error_body.get("message")
                or error_body.get("error")
                or error_body
            )
        except ValueError:
            detail_message = response.text
        raise HTTPException(status_code=response.status_code, detail=f"Foodics API error: {detail_message}")

    try:
        return response.json()
    except ValueError:
        return {"status": "success"}


def fetch_foodics_categories():
    return fetch_foodics_collection("categories")


def fetch_foodics_products():
    records = fetch_foodics_collection("products?include=category")
    categories_cache: Dict[str, Dict[str, Any]] = {}

    def extract_category_details(record: Dict[str, Any]) -> Dict[str, Optional[str]]:
        raw_category = record.get("category")
        relationships = record.get("relationships")

        if isinstance(raw_category, dict):
            category_id = normalize_identifier(raw_category.get("id"))
            category_reference = normalize_identifier(raw_category.get("reference"))
            category_name = raw_category.get("name") or raw_category.get("name_localized")
            return {
                "id": category_id,
                "reference": category_reference,
                "name": category_name,
            }

        if isinstance(relationships, dict):
            category_rel = relationships.get("category") or relationships.get("categories")
            if isinstance(category_rel, dict):
                rel_data = category_rel.get("data")
                if isinstance(rel_data, dict):
                    category_id = normalize_identifier(rel_data.get("id"))
                    category_reference = normalize_identifier(rel_data.get("reference"))
                    category_name = rel_data.get("name") or rel_data.get("name_localized")
                    return {
                        "id": category_id,
                        "reference": category_reference,
                        "name": category_name,
                    }
                if isinstance(rel_data, list) and rel_data:
                    rel_entry = rel_data[0]
                    category_id = normalize_identifier(rel_entry.get("id"))
                    category_reference = normalize_identifier(rel_entry.get("reference"))
                    category_name = rel_entry.get("name") or rel_entry.get("name_localized")
                    return {
                        "id": category_id,
                        "reference": category_reference,
                        "name": category_name,
                    }

        category_id = normalize_identifier(record.get("category_id"))
        category_reference = normalize_identifier(record.get("category_reference"))
        category_name = record.get("category_name") or record.get("category_name_localized")

        if category_id and category_id in categories_cache:
            cached = categories_cache[category_id]
            return {
                "id": category_id,
                "reference": category_reference or cached.get("reference"),
                "name": category_name or cached.get("name"),
            }

        if category_reference and category_reference in categories_cache:
            cached = categories_cache[category_reference]
            return {
                "id": category_id or cached.get("id"),
                "reference": category_reference,
                "name": category_name or cached.get("name"),
            }

        if category_name:
            normalized = category_name.strip().lower()
            if normalized and normalized in categories_cache:
                cached = categories_cache[normalized]
                return {
                    "id": category_id or cached.get("id"),
                    "reference": category_reference or cached.get("reference"),
                    "name": category_name,
                }

        return {
            "id": category_id,
            "reference": category_reference,
            "name": category_name,
        }

    for record in records:
        category_details = extract_category_details(record)
        category_id = category_details.get("id")
        category_reference = category_details.get("reference")
        category_name = category_details.get("name")

        if category_id:
            categories_cache[category_id] = category_details
        if category_reference:
            categories_cache[category_reference] = category_details
        if isinstance(category_name, str) and category_name.strip():
            categories_cache[category_name.strip().lower()] = category_details

        record["category_id"] = category_id
        record["category_reference"] = category_reference
        record["category_name"] = category_name

        if category_id and isinstance(record.get("category"), dict):
            record["category"]["id"] = category_id
        if isinstance(record.get("category"), dict) and category_name:
            record["category"]["name"] = category_name

        if "relationships" in record:
            rel_category = record["relationships"].get("category") or record["relationships"].get("categories")
            if isinstance(rel_category, dict):
                rel_data = rel_category.get("data")
                if isinstance(rel_data, dict):
                    rel_data["id"] = category_id or rel_data.get("id")
                    if category_reference:
                        rel_data["reference"] = category_reference
                    if category_name:
                        rel_data["name"] = category_name
                elif isinstance(rel_data, list) and rel_data:
                    rel_entry = rel_data[0]
                    rel_entry["id"] = category_id or rel_entry.get("id")
                    if category_reference:
                        rel_entry["reference"] = category_reference
                    if category_name:
                        rel_entry["name"] = category_name

    return records


def fetch_foodics_branches():
    return fetch_foodics_collection("branches")


def fetch_foodics_devices():
    return fetch_foodics_collection("devices")


def fetch_foodics_inventory_items():
    return fetch_foodics_collection("inventory_items")


def fetch_foodics_inventory_transactions():
    return fetch_foodics_collection("inventory_transactions")


def fetch_foodics_modifiers():
    return fetch_foodics_collection("modifiers")


def fetch_foodics_modifier_options():
    return fetch_foodics_collection("modifier_options?include=modifier")


def fetch_foodics_payment_methods():
    return fetch_foodics_collection("payment_methods")


def fetch_foodics_users():
    return fetch_foodics_collection("users")


def fetch_foodics_orders():
    return fetch_foodics_collection("orders")


def fetch_foodics_inventory_levels(branches: Optional[List[Dict[str, Any]]] = None):
    branch_records = branches or fetch_foodics_branches()
    levels: List[Dict[str, Any]] = []

    for branch in branch_records:
        branch_id = branch.get("id")
        if not branch_id:
            continue
        try:
            branch_levels = fetch_foodics_collection(f"inventory_levels/{branch_id}")
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        levels.extend(branch_levels)

    return levels


BRANCH_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("name_localized", "name_localized"),
    ("reference", "reference"),
    ("code", "code"),
    ("status", "status"),
    ("type", lambda record: to_int(record.get("type"))),
    ("timezone", "timezone"),
    ("phone", "phone"),
    ("address", "address"),
    ("latitude", lambda record: to_float(record.get("latitude"))),
    ("longitude", lambda record: to_float(record.get("longitude"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


DEVICE_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("reference", "reference"),
    ("branch_id", extract_branch_id),
    ("code", "code"),
    ("type", lambda record: to_int(record.get("type"))),
    ("status", "status"),
    ("is_active", lambda record: to_bool(record.get("is_active"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


INVENTORY_ITEM_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("name_localized", "name_localized"),
    ("sku", "sku"),
    ("barcode", "barcode"),
    ("item_type", "type"),
    ("category_id", lambda record: record.get("category_id") or get_nested(record, "category.id")),
    ("cost", lambda record: to_float(record.get("cost"))),
    ("price", lambda record: to_float(record.get("price"))),
    ("is_active", lambda record: to_bool(record.get("is_active"))),
    ("unit", "unit"),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


INVENTORY_LEVEL_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", build_inventory_level_id),
    ("branch_id", extract_branch_id),
    ("warehouse_id", extract_warehouse_id),
    ("inventory_item_id", extract_inventory_item_id),
    ("available", lambda record: to_float(record.get("available"))),
    ("in_stock", lambda record: to_float(record.get("in_stock"))),
    ("on_order", lambda record: to_float(record.get("on_order"))),
    ("wastage", lambda record: to_float(record.get("wastage"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("raw_payload", record_to_payload),
]


INVENTORY_TRANSACTION_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("transaction_type", "type"),
    ("inventory_item_id", extract_inventory_item_id),
    ("warehouse_id", extract_warehouse_id),
    ("branch_id", extract_branch_id),
    ("quantity", lambda record: to_float(record.get("quantity"))),
    ("unit_cost", lambda record: to_float(record.get("unit_cost"))),
    ("source_type", "source_type"),
    ("source_id", lambda record: record.get("source_id") or get_nested(record, "source.id")),
    ("reference", "reference"),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("raw_payload", record_to_payload),
]


MODIFIER_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("name_localized", "name_localized"),
    ("reference", "reference"),
    ("is_active", lambda record: to_bool(record.get("is_active"))),
    ("is_required", lambda record: to_bool(record.get("is_required"))),
    ("min_options", lambda record: to_int(record.get("min"))),
    ("max_options", lambda record: to_int(record.get("max"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


MODIFIER_OPTION_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("modifier_id", extract_modifier_id),
    ("name", "name"),
    ("name_localized", "name_localized"),
    ("price", lambda record: to_float(record.get("price"))),
    ("cost", lambda record: to_float(record.get("cost"))),
    ("calories", lambda record: to_float(record.get("calories"))),
    ("is_default", lambda record: to_bool(record.get("is_default"))),
    ("sort_order", lambda record: to_int(record.get("sort_order"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


PRODUCT_MODIFIER_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda link: link.get("id")),
    ("modifier_id", "modifier_id"),
    ("product_id", "product_id"),
    ("raw_payload", record_to_payload),
]


PRODUCT_MODIFIER_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda link: link.get("id")),
    ("modifier_id", lambda link: link.get("modifier_id")),
    ("product_id", lambda link: link.get("product_id")),
    ("raw_payload", lambda link: record_to_payload(link.get("link_data") or link)),
]


PAYMENT_METHOD_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("code", "code"),
    ("method_type", "type"),
    ("is_active", lambda record: to_bool(record.get("is_active"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


USER_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("name", "name"),
    ("email", "email"),
    ("phone", "phone"),
    ("role", "role"),
    ("status", "status"),
    ("branch_id", extract_primary_branch_association),
    ("is_active", lambda record: to_bool(record.get("is_active"))),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
    ("deleted_at", "deleted_at"),
    ("raw_payload", record_to_payload),
]


FOODICS_ORDER_COLUMN_EXTRACTORS: ColumnConfig = [
    ("id", lambda record: record.get("id")),
    ("code", "code"),
    ("number", "number"),
    ("status", "status"),
    ("order_type", "type"),
    ("branch_id", extract_branch_id),
    ("customer_id", extract_customer_id),
    ("cashier_id", lambda record: extract_user_id(record, "cashier_id")),
    ("device_id", lambda record: record.get("device_id") or get_nested(record, "device.id")),
    ("payment_method_id", extract_payment_method_id),
    ("source", lambda record: normalize_identifier(record.get("source"))),
    (
        "total",
        lambda record: to_float(
            record.get("total")
            or record.get("total_price")
            or get_nested(record, "totals.total")
            or get_nested(record, "totals.total_price")
        ),
    ),
    (
        "subtotal",
        lambda record: to_float(
            record.get("subtotal")
            or record.get("subtotal_price")
            or get_nested(record, "totals.subtotal")
            or get_nested(record, "totals.subtotal_price")
        ),
    ),
    (
        "discount",
        lambda record: to_float(
            record.get("discount")
            or record.get("discount_amount")
            or get_nested(record, "totals.discount")
            or get_nested(record, "totals.discount_amount")
        ),
    ),
    (
        "tax",
        lambda record: to_float(
            record.get("tax")
            or record.get("tax_amount")
            or get_nested(record, "totals.tax")
            or get_nested(record, "totals.tax_amount")
        ),
    ),
    (
        "service_charge",
        lambda record: to_float(
            record.get("service_charge")
            or record.get("service_charge_amount")
            or get_nested(record, "totals.service_charge")
            or get_nested(record, "totals.service_charge_amount")
        ),
    ),
    ("created_at", lambda record: record.get("created_at") or record.get("opened_at")),
    ("updated_at", lambda record: record.get("updated_at")),
    ("closed_at", lambda record: record.get("closed_at")),
    ("deleted_at", lambda record: record.get("deleted_at")),
    ("raw_payload", record_to_payload),
]

def upsert_branches(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "branches", BRANCH_COLUMN_EXTRACTORS, context)


def upsert_devices(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "devices", DEVICE_COLUMN_EXTRACTORS, context)


def upsert_inventory_items(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "inventory_items", INVENTORY_ITEM_COLUMN_EXTRACTORS, context)


def upsert_inventory_levels(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "inventory_levels", INVENTORY_LEVEL_COLUMN_EXTRACTORS, context)


def upsert_inventory_transactions(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "inventory_transactions", INVENTORY_TRANSACTION_COLUMN_EXTRACTORS, context)


def upsert_modifiers(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "modifiers", MODIFIER_COLUMN_EXTRACTORS, context)


def upsert_modifier_options(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "modifier_options", MODIFIER_OPTION_COLUMN_EXTRACTORS, context)


def upsert_payment_methods(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "payment_methods", PAYMENT_METHOD_COLUMN_EXTRACTORS, context)


def upsert_users(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "users", USER_COLUMN_EXTRACTORS, context)


def project_foodics_orders_into_orders_table(
    records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None
):
    if not records:
        return {"inserted": 0, "updated": 0, "deleted": 0}

    payment_method_lookup: Dict[str, str] = {}

    if context:
        context_methods = context.get("payment_methods")
        if isinstance(context_methods, list):
            for method in context_methods:
                if not isinstance(method, dict):
                    continue
                method_id = normalize_identifier(method.get("id"))
                if not method_id:
                    continue
                display_name = (
                    method.get("name")
                    or method.get("code")
                    or method.get("reference")
                    or method_id
                )
                payment_method_lookup[method_id] = display_name

    if not payment_method_lookup:
        try:
            existing_methods = fetch_all_dict("SELECT id, name, code FROM payment_methods")
        except Exception:
            existing_methods = []
        for method in existing_methods:
            method_id = normalize_identifier(method.get("id"))
            if not method_id:
                continue
            display_name = (
                method.get("name")
                or method.get("code")
                or method_id
            )
            payment_method_lookup.setdefault(method_id, display_name)

    order_rows: List[Tuple[Any, ...]] = []
    external_ids: set[str] = set()

    for record in records:
        if not isinstance(record, dict):
            continue

        external_id = normalize_identifier(record.get("id"))
        if not external_id:
            continue

        external_ids.add(external_id)

        order_number = normalize_identifier(record.get("number"))
        status_raw = record.get("status")
        status = normalize_identifier(status_raw)
        if not status:
            status_key = None
            if isinstance(status_raw, (int, float)):
                status_key = str(int(status_raw))
            elif status_raw is not None:
                status_key = str(status_raw).strip().lower()
            if status_key:
                status = FOODICS_STATUS_MAP.get(status_key, None)
        if not status:
            status = "unknown"
        source_raw = record.get("source")
        source_normalized = normalize_identifier(source_raw)
        if source_normalized in {"website", "foodics", "whatsapp", "qr"}:
            source_value = source_normalized
        else:
            source_value = "foodics"

        payment_method_name: Optional[str] = None
        payment_method_id = extract_payment_method_id(record)
        payment_method_data = record.get("payment_method")

        if isinstance(payment_method_data, dict):
            payment_method_name = (
                payment_method_data.get("name")
                or payment_method_data.get("code")
                or payment_method_data.get("reference")
            )
            if not payment_method_id:
                payment_method_id = normalize_identifier(payment_method_data.get("id"))

        if payment_method_id:
            lookup_name = payment_method_lookup.get(payment_method_id)
            if lookup_name:
                payment_method_name = payment_method_name or lookup_name
            elif not payment_method_name:
                payment_method_name = payment_method_id

        total_price = to_float(
            record.get("total")
            or record.get("total_price")
            or get_nested(record, "totals.total")
            or get_nested(record, "totals.total_price")
        )

        created_at = (
            record.get("created_at")
            or record.get("opened_at")
            or record.get("closed_at")
            or record.get("updated_at")
        )

        customer_reference = extract_customer_id(record)
        customer_numeric: Optional[int] = None
        if customer_reference:
            try:
                customer_numeric = int(customer_reference)
            except ValueError:
                customer_numeric = None

        if total_price is None:
            total_price = 0.0

        order_rows.append(
            (
                external_id,
                source_value,
                status,
                payment_method_name,
                total_price,
                created_at,
                customer_numeric,
                customer_reference,
                order_number,
            )
        )

    if not order_rows:
        return {"inserted": 0, "updated": 0, "deleted": 0}

    conn = get_connection()
    inserted = 0
    updated = 0
    deleted = 0

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT external_id FROM orders WHERE source = %s AND external_id IS NOT NULL",
            ("foodics",),
        )
        existing_ids = {row[0] for row in cursor.fetchall() if row and row[0]}

        for row in order_rows:
            external_id = row[0]
            if external_id in existing_ids:
                cursor.execute(
                    """
                    UPDATE orders
                    SET
                        source = %s,
                        status = %s,
                        payment_method = %s,
                        total_price = %s,
                        created_at = COALESCE(%s, created_at),
                        customer_id = %s,
                        customer_reference = %s,
                        order_number = COALESCE(%s, order_number)
                    WHERE external_id = %s
                    """,
                    (
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        external_id,
                    ),
                )
                updated += 1
            else:
                cursor.execute(UPSERT_FOODICS_ORDER_PROJECTION_SQL, row)
                inserted += 1

        obsolete_ids = existing_ids - external_ids
        for external_id in obsolete_ids:
            cursor.execute("DELETE FROM orders WHERE external_id = %s", (external_id,))
            deleted += 1

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"inserted": inserted, "updated": updated, "deleted": deleted}


def upsert_foodics_orders(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    result = upsert_generic(records, "foodics_orders", FOODICS_ORDER_COLUMN_EXTRACTORS, context)
    projection_result = project_foodics_orders_into_orders_table(records, context)
    result["orders_table_inserted"] = projection_result.get("inserted", 0)
    result["orders_table_updated"] = projection_result.get("updated", 0)
    result["orders_table_deleted"] = projection_result.get("deleted", 0)
    return result


def upsert_product_modifiers(records: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None):
    return upsert_generic(records, "product_modifiers", PRODUCT_MODIFIER_COLUMN_EXTRACTORS, context)


UPSERT_CATEGORY_SQL = f"""
    INSERT INTO categories (id, name, name_localized, reference, image, created_at, updated_at, deleted_at)
    VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
    ON CONFLICT(id) DO UPDATE SET
        name = EXCLUDED.name,
        name_localized = EXCLUDED.name_localized,
        reference = EXCLUDED.reference,
        image = EXCLUDED.image,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        deleted_at = EXCLUDED.deleted_at
"""


def upsert_categories(categories, context: Optional[Dict[str, Any]] = None):
    if not categories:
        return {"inserted": 0, "updated": 0}

    category_ids = [category.get("id") for category in categories if category.get("id")]
    existing_ids = set()

    if category_ids:
        if USE_POSTGRES:
            lookup_query = "SELECT id::text FROM categories WHERE id::text = ANY(%s)"
            rows = fetch_all(lookup_query, (category_ids,))
        else:
            placeholders = ",".join([PLACEHOLDER] * len(category_ids))
            lookup_query = f"SELECT id FROM categories WHERE id IN ({placeholders})"
            rows = fetch_all(lookup_query, category_ids)
        existing_ids = {row[0] for row in rows}

    inserted = 0
    updated = 0

    conn = get_connection()
    try:
        cursor = conn.cursor()
        for category in categories:
            category_id = category.get("id")
            if not category_id:
                continue

            params = (
                category_id,
                category.get("name"),
                category.get("name_localized"),
                category.get("reference"),
                category.get("image"),
                category.get("created_at"),
                category.get("updated_at"),
                category.get("deleted_at"),
            )

            cursor.execute(UPSERT_CATEGORY_SQL, params)

            if category_id in existing_ids:
                updated += 1
            else:
                inserted += 1

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"inserted": inserted, "updated": updated}


def fetch_categories_for_sync(context: Dict[str, Any]):
    return fetch_foodics_categories()


def fetch_products_for_sync(context: Dict[str, Any]):
    return fetch_foodics_products()


def fetch_branches_for_sync(context: Dict[str, Any]):
    return fetch_foodics_branches()


def fetch_devices_for_sync(context: Dict[str, Any]):
    return fetch_foodics_devices()


def fetch_inventory_items_for_sync(context: Dict[str, Any]):
    return fetch_foodics_inventory_items()


def fetch_inventory_levels_for_sync(context: Dict[str, Any]):
    branches = context.get("branches")
    if branches is None:
        branches = fetch_foodics_branches()
        context["branches"] = branches
    return fetch_foodics_inventory_levels(branches)


def fetch_inventory_transactions_for_sync(context: Dict[str, Any]):
    return fetch_foodics_inventory_transactions()


def fetch_modifiers_for_sync(context: Dict[str, Any]):
    modifiers = fetch_foodics_modifiers()
    context["modifiers"] = modifiers
    return modifiers


def build_product_modifier_links(modifiers: List[Dict[str, Any]]):
    links: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for modifier in modifiers:
        if not isinstance(modifier, dict):
            continue

        modifier_id = modifier.get("id")
        if not modifier_id:
            continue

        relationship_products: List[Any] = []

        relationships = modifier.get("relationships")
        if isinstance(relationships, dict):
            rel_products = relationships.get("products")
            if isinstance(rel_products, dict):
                rel_data = rel_products.get("data")
                if isinstance(rel_data, list):
                    relationship_products.extend(rel_data)

        if not relationship_products and isinstance(modifier.get("products"), list):
            relationship_products.extend(modifier["products"])

        if not relationship_products:
            continue

        for product_ref in relationship_products:
            product_id = None
            pivot: Dict[str, Any] = {}
            if isinstance(product_ref, dict):
                product_id = product_ref.get("id") or product_ref.get("product_id")
                pivot = product_ref.get("pivot") if isinstance(product_ref.get("pivot"), dict) else {}
            elif isinstance(product_ref, str):
                product_id = product_ref

            if not product_id:
                continue

            link_id = f"{modifier_id}:{product_id}"
            if link_id in seen:
                continue
            seen.add(link_id)

            link_data = {
                "modifier": {
                    "id": str(modifier_id),
                    "name": modifier.get("name"),
                    "reference": modifier.get("reference"),
                },
                "product": {
                    "id": str(product_id),
                },
                "pivot": pivot,
            }

            links.append(
                {
                    "id": link_id,
                    "modifier_id": str(modifier_id),
                    "product_id": str(product_id),
                    "link_data": link_data,
                }
            )

    return links


def fetch_modifier_options_for_sync(context: Dict[str, Any]):
    return fetch_foodics_modifier_options()


def fetch_payment_methods_for_sync(context: Dict[str, Any]):
    return fetch_foodics_payment_methods()


def fetch_users_for_sync(context: Dict[str, Any]):
    return fetch_foodics_users()


def fetch_orders_for_sync(context: Dict[str, Any]):
    return fetch_foodics_orders()


def fetch_product_modifiers_for_sync(context: Dict[str, Any]):
    modifiers_with_products = context.get("modifiers_with_products")

    if modifiers_with_products is None:
        base_modifiers = context.get("modifiers")
        has_relationships = False

        if isinstance(base_modifiers, list):
            for modifier in base_modifiers:
                if not isinstance(modifier, dict):
                    continue
                relationships = modifier.get("relationships")
                if isinstance(relationships, dict) and isinstance(relationships.get("products"), dict):
                    has_relationships = True
                    break

        if has_relationships:
            modifiers_with_products = base_modifiers
        else:
            modifiers_with_products = fetch_foodics_collection("modifiers?include=products")

        context["modifiers_with_products"] = modifiers_with_products

    return build_product_modifier_links(modifiers_with_products or [])


def sanitize_modifier_option(option: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(option.get("id")) if option.get("id") is not None else None,
        "modifier_id": str(option.get("modifier_id")) if option.get("modifier_id") is not None else None,
        "name": option.get("name"),
        "name_localized": option.get("name_localized"),
        "price": to_float(option.get("price")),
        "cost": to_float(option.get("cost")),
        "is_default": to_bool(option.get("is_default")),
        "sort_order": to_int(option.get("sort_order")),
    }


def build_product_entry(
    product: Dict[str, Any],
    category_definition: Dict[str, Any],
    product_modifiers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    price_value = to_float(product.get("price"))
    raw_price = f"{price_value:.2f}" if price_value is not None else ""
    image_url = product.get("image") or ""
    is_active_value = to_bool(product.get("is_active"))
    is_available = True if is_active_value is None else bool(is_active_value)
    availability_label = "In Stock" if is_available else "Sold Out"

    duplicate_modifiers = [copy.deepcopy(modifier) for modifier in product_modifiers]

    milk_customizable = any(
        isinstance(modifier.get("name"), str) and "milk" in modifier["name"].lower()
        for modifier in duplicate_modifiers
    )
    bean_customizable = any(
        isinstance(modifier.get("name"), str)
        and any(token in modifier["name"].lower() for token in ("bean", "coffee"))
        for modifier in duplicate_modifiers
    )

    modifier_count = len(duplicate_modifiers)
    slug_source = product.get("slug") or product.get("reference") or product.get("name") or product.get("id")
    product_slug = slugify(slug_source) or str(product.get("id") or "")

    return {
        "id": product.get("id"),
        "productId": product.get("id"),
        "name": product.get("name") or product.get("name_localized") or "Untitled Item",
        "price": price_value,
        "rawPrice": raw_price,
        "imageUrl": image_url,
        "description": product.get("description") or "",
        "link": product.get("link") or "",
        "availability": availability_label,
        "isAvailable": is_available,
        "isCustomizable": modifier_count > 0,
        "milkCustomizable": milk_customizable,
        "beanCustomizable": bean_customizable,
        "modifiers": duplicate_modifiers,
        "hasModifiers": modifier_count > 0,
        "modifierCount": modifier_count,
        "slug": product_slug,
        "categorySlug": category_definition.get("slug"),
        "categoryId": category_definition.get("id"),
    }


@app.get("/api/menu")
def get_menu():
    categories = fetch_all_dict(
        "SELECT id, name, name_localized, reference FROM categories ORDER BY name NULLS LAST"
    )
    category_lookup: Dict[str, Dict[str, Any]] = {}
    category_lookup_by_name: Dict[str, Dict[str, Any]] = {}

    def register_category_definition(
        definition: Dict[str, Any],
        *,
        category_id: Optional[Any] = None,
        reference: Optional[Any] = None,
        slug: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        for key in (category_id, reference, slug):
            norm = normalize_identifier(key)
            if not norm:
                continue
            existing = category_lookup.get(norm)
            if existing is not None and existing is not definition:
                continue
            category_lookup[norm] = definition

        if isinstance(name, str):
            normalized_name = name.strip().lower()
            if normalized_name:
                existing = category_lookup_by_name.get(normalized_name)
                if existing is None:
                    category_lookup_by_name[normalized_name] = definition

    ordered_category_definitions: List[Dict[str, Any]] = []
    for index, category in enumerate(categories):
        name = category.get("name") or category.get("name_localized") or "Other"
        slug = slugify(name) or f"category_{index + 1}"
        category_id = normalize_identifier(category.get("id"))
        reference = normalize_identifier(category.get("reference"))
        definition = {
            "id": category_id,
            "name": name,
            "slug": slug,
            "index": index,
            "reference": reference,
        }
        register_category_definition(
            definition,
            category_id=category_id,
            reference=reference,
            slug=slug,
            name=name,
        )
        ordered_category_definitions.append(
            {
                "id": definition["id"],
                "name": definition["name"],
                "slug": definition["slug"],
                "position": definition["index"],
                "product_count": 0,
            }
        )

    products = fetch_all_dict(
        """
        SELECT
            p.id,
            p.name,
            p.name_localized,
            p.reference,
            p.description,
            p.image,
            p.price,
            p.is_active,
            p.category_id,
            c.name AS category_name,
            c.name_localized AS category_name_localized,
            c.reference AS category_reference,
            p.raw_payload
        FROM products AS p
        LEFT JOIN categories AS c ON c.id = p.category_id
        WHERE p.deleted_at IS NULL
        ORDER BY c.name NULLS LAST, p.name NULLS LAST
        """
    )

    product_modifier_links = fetch_all_dict(
        "SELECT product_id, modifier_id, raw_payload FROM product_modifiers"
    )
    modifiers = fetch_all_dict(
        """
        SELECT id, name, name_localized, reference, is_active, is_required, min_options, max_options
        FROM modifiers
        """
    )
    modifier_options = fetch_all_dict(
        """
        SELECT id, modifier_id, name, name_localized, price, cost, is_default, sort_order
        FROM modifier_options
        ORDER BY modifier_id, sort_order NULLS LAST, name
        """
    )

    modifier_options_map: Dict[str, List[Dict[str, Any]]] = {}
    for option in modifier_options:
        modifier_id = option.get("modifier_id")
        if modifier_id is None:
            continue
        modifier_key = str(modifier_id)
        modifier_options_map.setdefault(modifier_key, []).append(sanitize_modifier_option(option))

    modifier_lookup: Dict[str, Dict[str, Any]] = {}
    for modifier in modifiers:
        modifier_id = modifier.get("id")
        if modifier_id is None:
            continue
        modifier_key = str(modifier_id)
        modifier_lookup[modifier_key] = {
            "id": modifier_key,
            "name": modifier.get("name") or modifier.get("name_localized"),
            "name_localized": modifier.get("name_localized"),
            "reference": modifier.get("reference"),
            "is_active": to_bool(modifier.get("is_active")),
            "is_required": to_bool(modifier.get("is_required")),
            "min_options": to_int(modifier.get("min_options")),
            "max_options": to_int(modifier.get("max_options")),
            "options": copy.deepcopy(modifier_options_map.get(modifier_key, [])),
        }

    product_modifiers_map: Dict[str, List[Dict[str, Any]]] = {}
    for link in product_modifier_links:
        product_id = link.get("product_id")
        modifier_id = link.get("modifier_id")
        if product_id is None or modifier_id is None:
            continue
        modifier_key = str(modifier_id)
        modifier_details = modifier_lookup.get(modifier_key)
        if not modifier_details:
            continue
        raw_link_payload = link.get("raw_payload")
        payload_data: Dict[str, Any] = {}
        if isinstance(raw_link_payload, dict):
            payload_data = raw_link_payload
        elif isinstance(raw_link_payload, str):
            raw_text = raw_link_payload.strip()
            if raw_text:
                try:
                    payload_data = json.loads(raw_text)
                except json.JSONDecodeError:
                    payload_data = {}

        pivot_data = payload_data.get("pivot") if isinstance(payload_data.get("pivot"), dict) else {}

        def parse_id_list(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item) for item in value if item]
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return []
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed if item]
                except json.JSONDecodeError:
                    return []
            return []

        excluded_ids = set(parse_id_list(pivot_data.get("excluded_options_ids")))
        default_ids = set(parse_id_list(pivot_data.get("default_options_ids")))

        modifier_copy = copy.deepcopy(modifier_details)
        filtered_options: List[Dict[str, Any]] = []
        for option in modifier_copy.get("options", []):
            option_id = str(option.get("id")) if option.get("id") is not None else None
            if option_id and excluded_ids and option_id in excluded_ids:
                continue
            if option_id and option_id in default_ids:
                option["is_default"] = True
            filtered_options.append(option)

        modifier_copy["options"] = filtered_options

        product_key = str(product_id)
        product_modifiers_map.setdefault(product_key, []).append(modifier_copy)

    sections_map: Dict[str, Dict[str, Any]] = {}

    dynamic_category_definitions: Dict[str, Dict[str, Any]] = {}

    for product in products:
        product_id = product.get("id")
        if product_id is None:
            continue

        product_key = str(product_id)
        slug_source = (
            product.get("slug") or product.get("reference") or product.get("name") or product.get("id")
        )
        product_slug = slugify(slug_source) if isinstance(slug_source, str) else str(product.get("id") or "")
        raw_payload_data: Dict[str, Any] = {}
        raw_payload = product.get("raw_payload")
        if isinstance(raw_payload, dict):
            raw_payload_data = raw_payload
        elif isinstance(raw_payload, str) and raw_payload.strip():
            try:
                raw_payload_data = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload_data = {}

        category_obj = raw_payload_data.get("category") if isinstance(raw_payload_data.get("category"), dict) else None

        category_id = normalize_identifier(product.get("category_id"))
        if not category_id:
            category_id = normalize_identifier(raw_payload_data.get("category_id"))
        if not category_id:
            if category_obj is not None:
                category_id = normalize_identifier(category_obj.get("id") or category_obj.get("category_id"))
        if not category_id:
            relationships = raw_payload_data.get("relationships")
            if isinstance(relationships, dict):
                category_rel = relationships.get("category")
                if isinstance(category_rel, dict):
                    rel_data = category_rel.get("data")
                    if isinstance(rel_data, dict):
                        category_id = normalize_identifier(rel_data.get("id"))
                    elif isinstance(rel_data, list) and rel_data:
                        category_id = normalize_identifier(rel_data[0].get("id"))

        category_definition: Optional[Dict[str, Any]] = None
        if category_id:
            category_definition = category_lookup.get(category_id)

        if category_definition is None:
            override_slug = PRODUCT_CATEGORY_OVERRIDES.get(product_slug)
            if override_slug:
                override_meta = CATEGORY_OVERRIDES_BY_SLUG.get(override_slug, {})
                for key in (
                    normalize_identifier(override_meta.get("id")),
                    normalize_identifier(override_meta.get("reference")),
                    override_slug,
                ):
                    if key:
                        category_definition = category_lookup.get(key)
                    if category_definition:
                        break

                if category_definition is None:
                    category_definition = dynamic_category_definitions.get(override_slug)
                    if category_definition is None:
                        category_definition = {
                            "id": normalize_identifier(override_meta.get("id")),
                            "name": override_meta.get("name") or override_slug.replace("_", " ").title(),
                            "slug": override_slug,
                            "index": len(ordered_category_definitions) + len(dynamic_category_definitions),
                            "reference": normalize_identifier(override_meta.get("reference")),
                        }
                        dynamic_category_definitions[override_slug] = category_definition

                register_category_definition(
                    category_definition,
                    category_id=override_meta.get("id"),
                    reference=override_meta.get("reference"),
                    slug=override_slug,
                    name=override_meta.get("name"),
                )
                if not category_id and override_meta.get("id"):
                    category_id = normalize_identifier(override_meta.get("id"))

        category_reference = normalize_identifier(product.get("category_reference"))
        if not category_reference:
            category_reference = normalize_identifier(
                raw_payload_data.get("category_reference")
                or (category_obj.get("reference") if isinstance(category_obj, dict) else None)
            )
        if not category_reference:
            relationships = raw_payload_data.get("relationships")
            if isinstance(relationships, dict):
                category_rel = relationships.get("category")
                if isinstance(category_rel, dict):
                    rel_data = category_rel.get("data")
                    if isinstance(rel_data, dict):
                        category_reference = normalize_identifier(rel_data.get("reference"))
                    elif isinstance(rel_data, list) and rel_data:
                        category_reference = normalize_identifier(rel_data[0].get("reference"))

        if category_definition is None and category_reference:
            category_definition = category_lookup.get(category_reference)

        candidate_name = (
            (category_definition or {}).get("name")
            or product.get("category_name")
            or product.get("category_name_localized")
            or (category_obj.get("name") if isinstance(category_obj, dict) else None)
            or (category_obj.get("name_localized") if isinstance(category_obj, dict) else None)
        )

        lookup_slug = slugify(candidate_name) if candidate_name else None
        if category_definition is None and lookup_slug:
            category_definition = category_lookup.get(lookup_slug)

        if category_definition is None and candidate_name:
            normalized_name = candidate_name.strip().lower()
            if normalized_name:
                category_definition = category_lookup_by_name.get(normalized_name)

        if category_definition is None:
            fallback_name = candidate_name or "Uncategorized"
            base_slug = slugify(fallback_name) or "uncategorized"
            slug_candidate = base_slug
            suffix = 2
            while slug_candidate in dynamic_category_definitions:
                slug_candidate = f"{base_slug}_{suffix}"
                suffix += 1

            category_definition = dynamic_category_definitions.get(slug_candidate)
            if category_definition is None:
                category_definition = {
                    "id": category_id,
                    "name": fallback_name,
                    "slug": slug_candidate,
                    "index": len(ordered_category_definitions) + len(dynamic_category_definitions),
                    "reference": category_reference,
                }
                dynamic_category_definitions[slug_candidate] = category_definition
                register_category_definition(
                    category_definition,
                    category_id=category_id,
                    reference=category_reference,
                    slug=slug_candidate,
                    name=fallback_name,
                )
        else:
            register_category_definition(
                category_definition,
                category_id=category_id or category_definition.get("id"),
                reference=category_reference or category_definition.get("reference"),
                slug=(lookup_slug or category_definition.get("slug")),
                name=candidate_name or category_definition.get("name"),
            )

        category_name = category_definition.get("name") or candidate_name or "Other"
        category_slug = category_definition.get("slug") or slugify(category_name) or "other"
        category_definition.setdefault("name", category_name)
        category_definition.setdefault("slug", category_slug)
        if category_reference and not category_definition.get("reference"):
            category_definition["reference"] = category_reference
        if category_id and not category_definition.get("id"):
            category_definition["id"] = category_id

        if category_slug not in sections_map:
            sections_map[category_slug] = {
                "category": category_name,
                "slug": category_slug,
                "id": category_definition.get("id"),
                "products": [],
            }

        product_modifiers = product_modifiers_map.get(product_key, [])
        product_entry = build_product_entry(product, category_definition, product_modifiers)
        product_slug = slugify(product_entry.get("slug") or "")
        if product_slug in REMOVED_PRODUCT_SLUGS:
            continue
        sections_map[category_slug]["products"].append(product_entry)

    # Sort products within sections by price descending then name
    for section in sections_map.values():
        section["products"].sort(
            key=lambda prod: (
                prod.get("price") is None,
                -(prod.get("price") or 0),
                (prod.get("name") or "").lower(),
            )
        )

    ordered_sections: List[Dict[str, Any]] = []
    for category in categories:
        slug = slugify(category.get("name") or category.get("name_localized") or "Other") or "other"
        if slug in sections_map:
            ordered_sections.append(sections_map.pop(slug))

    # Append any remaining sections not tied to known categories
    for slug in sorted(sections_map.keys()):
        ordered_sections.append(sections_map[slug])

    best_seller_products: List[Dict[str, Any]] = []
    for section in ordered_sections:
        for product in section.get("products", []):
            if product.get("slug") in BEST_SELLER_SLUGS:
                best_seller_products.append(copy.deepcopy(product))

    if best_seller_products:
        ordered_sections = [
            {
                "category": "Best Seller",
                "slug": "best-seller",
                "id": None,
                "products": best_seller_products,
            }
        ] + ordered_sections

    normalized_sections: List[Dict[str, Any]] = []
    for section in ordered_sections:
        products = section.get("products") or []
        if not products:
            continue

        slug_value = str(section.get("slug") or "")
        if slug_value in {"other", "uncategorized"} or slug_value.startswith("uncategorized"):
            updated_section = dict(section)
            raw_category = (updated_section.get("category") or "").strip().lower()
            if not raw_category or raw_category in {"other", "uncategorized"}:
                updated_section["category"] = "More Items"
            normalized_sections.append(updated_section)
        else:
            normalized_sections.append(section)

    ordered_sections = normalized_sections

    categories_payload: List[Dict[str, Any]] = []
    for position, section in enumerate(ordered_sections):
        slug = section.get("slug")
        if not slug or slug == "best-seller" or slug.startswith("uncategorized"):
            continue
        categories_payload.append(
            {
                "id": section.get("id"),
                "name": section.get("category"),
                "slug": slug,
                "position": position,
                "product_count": len(section.get("products", [])),
            }
        )

    response_payload = {
        "status": "success",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sections": ordered_sections,
        "milk_options": copy.deepcopy(FALLBACK_MILK_OPTIONS),
        "bean_options": copy.deepcopy(FALLBACK_BEAN_OPTIONS),
        "categories": categories_payload,
    }

    return response_payload


UPSERT_PRODUCT_SQL = f"""
    INSERT INTO products (
        id,
        name,
        name_localized,
        reference,
        sku,
        barcode,
        description,
        image,
        is_active,
        price,
        cost,
        sort_order,
        category_id,
        created_at,
        updated_at,
        deleted_at,
        raw_payload
    ) VALUES (
        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER},
        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER},
        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER},
        {PLACEHOLDER}, {PLACEHOLDER}
    )
    ON CONFLICT(id) DO UPDATE SET
        name = EXCLUDED.name,
        name_localized = EXCLUDED.name_localized,
        reference = EXCLUDED.reference,
        sku = EXCLUDED.sku,
        barcode = EXCLUDED.barcode,
        description = EXCLUDED.description,
        image = EXCLUDED.image,
        is_active = EXCLUDED.is_active,
        price = EXCLUDED.price,
        cost = EXCLUDED.cost,
        sort_order = EXCLUDED.sort_order,
        category_id = EXCLUDED.category_id,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        deleted_at = EXCLUDED.deleted_at,
        raw_payload = EXCLUDED.raw_payload
"""

UPSERT_FOODICS_ORDER_PROJECTION_SQL = f"""
    INSERT INTO orders (
        external_id,
        source,
        status,
        payment_method,
        total_price,
        created_at,
        customer_id,
        customer_reference,
        order_number
    ) VALUES (
        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER},
        {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}
    )
"""


def upsert_products(products, context: Optional[Dict[str, Any]] = None):
    if not products:
        return {"inserted": 0, "updated": 0}

    product_ids = [product.get("id") for product in products if product.get("id")]
    existing_ids = set()

    if product_ids:
        if USE_POSTGRES:
            lookup_query = "SELECT id::text FROM products WHERE id::text = ANY(%s)"
            rows = fetch_all(lookup_query, (product_ids,))
        else:
            placeholders = ",".join([PLACEHOLDER] * len(product_ids))
            lookup_query = f"SELECT id FROM products WHERE id IN ({placeholders})"
            rows = fetch_all(lookup_query, product_ids)
        existing_ids = {row[0] for row in rows}

    inserted = 0
    updated = 0

    conn = get_connection()
    try:
        cursor = conn.cursor()
        for product in products:
            product_id = product.get("id")
            if not product_id:
                continue

            name = product.get("name")
            name_localized = product.get("name_localized")
            reference = product.get("reference")
            sku = product.get("sku")
            barcode = product.get("barcode")
            description = product.get("description")
            image = product.get("image")

            raw_category_id = product.get("category_id")
            if not raw_category_id and isinstance(product.get("category"), dict):
                raw_category_id = product["category"].get("id")
            category_id = str(raw_category_id) if raw_category_id is not None else None
            is_active_value = product.get("is_active")
            if is_active_value is not None:
                is_active_value = bool(is_active_value)
                if not USE_POSTGRES:
                    is_active_value = int(is_active_value)

            price = product.get("price")
            cost = product.get("cost")
            sort_order = product.get("sort_order")
            created_at = product.get("created_at")
            updated_at = product.get("updated_at")
            deleted_at = product.get("deleted_at")
            raw_payload = json.dumps(product)

            params = (
                product_id,
                name,
                name_localized,
                reference,
                sku,
                barcode,
                description,
                image,
                is_active_value,
                price,
                cost,
                sort_order,
                category_id,
                created_at,
                updated_at,
                deleted_at,
                raw_payload,
            )

            cursor.execute(UPSERT_PRODUCT_SQL, params)

            if product_id in existing_ids:
                updated += 1
            else:
                inserted += 1

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    return {"inserted": inserted, "updated": updated}


FOODICS_SYNC_CONFIG: Dict[str, Dict[str, Callable[..., Any]]] = {
    "branches": {"fetch": fetch_branches_for_sync, "upsert": upsert_branches},
    "categories": {"fetch": fetch_categories_for_sync, "upsert": upsert_categories},
    "devices": {"fetch": fetch_devices_for_sync, "upsert": upsert_devices},
    "inventory_items": {"fetch": fetch_inventory_items_for_sync, "upsert": upsert_inventory_items},
    "inventory_levels": {"fetch": fetch_inventory_levels_for_sync, "upsert": upsert_inventory_levels},
    "inventory_transactions": {"fetch": fetch_inventory_transactions_for_sync, "upsert": upsert_inventory_transactions},
    "modifiers": {"fetch": fetch_modifiers_for_sync, "upsert": upsert_modifiers},
    "modifier_options": {"fetch": fetch_modifier_options_for_sync, "upsert": upsert_modifier_options},
    "product_modifiers": {"fetch": fetch_product_modifiers_for_sync, "upsert": upsert_product_modifiers},
    "payment_methods": {"fetch": fetch_payment_methods_for_sync, "upsert": upsert_payment_methods},
    "products": {"fetch": fetch_products_for_sync, "upsert": upsert_products},
    "orders": {"fetch": fetch_orders_for_sync, "upsert": upsert_foodics_orders},
    "users": {"fetch": fetch_users_for_sync, "upsert": upsert_users},
}


FOODICS_SYNC_SEQUENCE: List[str] = [
    "branches",
    "categories",
    "devices",
    "inventory_items",
    "inventory_levels",
    "inventory_transactions",
    "modifiers",
    "modifier_options",
    "product_modifiers",
    "payment_methods",
    "products",
    "orders",
    "users",
]


def run_foodics_sync(resource: str, context: Optional[Dict[str, Any]] = None):
    resource_key = resource.lower()
    config = FOODICS_SYNC_CONFIG.get(resource_key)
    if not config:
        raise HTTPException(status_code=404, detail=f"Unsupported Foodics resource: {resource}")

    state = context if context is not None else {}
    fetcher = config["fetch"]
    upserter = config["upsert"]

    records = fetcher(state)
    state[resource_key] = records
    upsert_result = upserter(records, state)

    return {
        "status": "success",
        "resource": resource_key,
        "fetched": len(records),
        "inserted": upsert_result.get("inserted", 0),
        "updated": upsert_result.get("updated", 0),
    }


@app.get("/api/foodics/product-modifiers")
def get_foodics_product_modifiers():
    links = fetch_all_dict("SELECT product_id, modifier_id FROM product_modifiers")
    if not links:
        return {"status": "success", "count": 0, "product_modifiers": {}}

    modifiers = fetch_all_dict(
        "SELECT id, name, name_localized, reference, is_active, is_required, min_options, max_options FROM modifiers"
    )
    options = fetch_all_dict(
        "SELECT id, modifier_id, name, name_localized, price, cost, is_default, sort_order FROM modifier_options"
    )

    modifier_lookup: Dict[str, Dict[str, Any]] = {}
    for modifier in modifiers:
        modifier_id = modifier.get("id")
        if not modifier_id:
            continue

        sanitized = {
            "id": str(modifier_id),
            "name": modifier.get("name"),
            "name_localized": modifier.get("name_localized"),
            "reference": modifier.get("reference"),
            "is_active": to_bool(modifier.get("is_active")),
            "is_required": to_bool(modifier.get("is_required")),
            "min_options": to_int(modifier.get("min_options")),
            "max_options": to_int(modifier.get("max_options")),
            "options": [],
        }
        modifier_lookup[str(modifier_id)] = sanitized

    for option in options:
        modifier_id = option.get("modifier_id")
        if not modifier_id:
            continue
        modifier_ref = modifier_lookup.get(str(modifier_id))
        if not modifier_ref:
            continue

        sanitized_option = {
            "id": option.get("id"),
            "modifier_id": str(modifier_id),
            "name": option.get("name"),
            "name_localized": option.get("name_localized"),
            "price": to_float(option.get("price")),
            "cost": to_float(option.get("cost")),
            "is_default": to_bool(option.get("is_default")),
            "sort_order": to_int(option.get("sort_order")),
        }
        modifier_ref["options"].append(sanitized_option)

    for modifier in modifier_lookup.values():
        modifier["options"].sort(
            key=lambda opt: (
                opt.get("sort_order") is None,
                opt.get("sort_order") if opt.get("sort_order") is not None else 0,
                (opt.get("name") or "").lower(),
            )
        )

    product_modifiers_map: Dict[str, List[Dict[str, Any]]] = {}
    for link in links:
        product_id = link.get("product_id")
        modifier_id = link.get("modifier_id")
        if not product_id or not modifier_id:
            continue

        modifier_template = modifier_lookup.get(str(modifier_id))
        if not modifier_template:
            continue

        modifier_copy = copy.deepcopy(modifier_template)
        product_modifiers_map.setdefault(str(product_id), []).append(modifier_copy)

    for modifiers_list in product_modifiers_map.values():
        modifiers_list.sort(key=lambda mod: (mod.get("name") or "").lower())

    return {
        "status": "success",
        "count": len(product_modifiers_map),
        "product_modifiers": product_modifiers_map,
    }


init_db()

INSERT_ORDER_SQL = f"""
    INSERT INTO orders (order_number, product, milk_type, order_type, quantity, amount, status, created_at, source)
    VALUES ({PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER}, {PLACEHOLDER})
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


class SubMenuOrderItem(BaseModel):
    product_id: Optional[str] = None
    product_reference: Optional[str] = None
    name: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    milk_label: Optional[str] = None
    bean_label: Optional[str] = None
    milk_value: Optional[str] = None
    bean_value: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None

    class Config:
        extra = "allow"


class SubMenuOrderRequest(BaseModel):
    order: Dict[str, Any]
    items: List[SubMenuOrderItem]

    class Config:
        extra = "allow"

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
            datetime.utcnow().isoformat(),
            "website",
        )
    )

    return {"redirect_url": redirect_url, "order_number": order_number}


@app.post("/api/submenu-orders")
def submit_submenu_order(request: SubMenuOrderRequest):
    order_info = request.order or {}
    raw_type = order_info.get("order_type")
    if raw_type is None:
        raw_type = order_info.get("type")
    normalized_type = str(raw_type or "").strip().lower()
    foodics_type_map = {"inhouse": 1, "takeaway": 2, "delivery": 3}
    foodics_type = foodics_type_map.get(normalized_type, 1)

    item_payloads: List[Dict[str, Any]] = []
    for raw_item in request.items or []:
        if isinstance(raw_item, SubMenuOrderItem):
            item_payloads.append(raw_item.dict(exclude_none=False))
        elif isinstance(raw_item, dict):
            item_payloads.append(raw_item)
        else:
            item_payloads.append({})

    branch_id = FOODICS_ORDER_BRANCH_ID
    if not branch_id:
        raise HTTPException(status_code=500, detail="Foodics branch ID is not configured.")

    guests = to_int(order_info.get("guests"))
    if guests is None or guests <= 0:
        guests = to_int(order_info.get("quantity"))
    if guests is None or guests <= 0:
        guests = max(1, sum(to_int(item.get("quantity")) or 1 for item in item_payloads) if item_payloads else 1)

    table_number = order_info.get("table_number")
    table_label: Optional[str] = None
    table_id_value = normalize_identifier(order_info.get("table_id"))
    kitchen_note_candidates = [
        order_info.get("kitchen_notes"),
        order_info.get("product"),
        order_info.get("summary"),
    ]
    kitchen_note_parts: List[str] = []
    for candidate in kitchen_note_candidates:
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped:
                kitchen_note_parts.append(stripped)
    if table_number is not None:
        table_number_text = str(table_number).strip()
        if table_number_text:
            table_label = table_number_text
            kitchen_note_parts.append(f"Table: {table_number_text}")
    kitchen_notes = " | ".join(kitchen_note_parts) if kitchen_note_parts else None
    if not kitchen_notes:
        kitchen_notes = "Order received via sub-menu"

    products_payload: List[Dict[str, Any]] = []
    computed_subtotal = 0.0

    for item_data in item_payloads:
        if not isinstance(item_data, dict):
            continue
        product_id = item_data.get("product_id") or item_data.get("product_reference")
        if not product_id:
            continue

        quantity = to_int(item_data.get("quantity"))
        if quantity is None or quantity <= 0:
            quantity = 1

        unit_price = to_float(item_data.get("unit_price"))
        if unit_price is None:
            unit_price = 0.0

        base_total = round(unit_price * quantity, 2)

        options_payload: List[Dict[str, Any]] = []
        options_total = 0.0
        raw_options = item_data.get("options") or []
        if isinstance(raw_options, list):
            for option in raw_options:
                if not isinstance(option, dict):
                    continue
                modifier_option_id = (
                    option.get("modifier_option_id")
                    or option.get("modifierOptionId")
                    or option.get("option_id")
                    or option.get("optionId")
                    or option.get("id")
                )
                if not modifier_option_id:
                    continue
                option_quantity = to_int(option.get("quantity"))
                if option_quantity is None or option_quantity <= 0:
                    option_quantity = quantity

                option_unit_price = to_float(option.get("unit_price") or option.get("unitPrice") or option.get("price"))
                if option_unit_price is None:
                    option_unit_price = 0.0

                option_total = round(option_unit_price * option_quantity, 2)
                options_total += option_total

                partition = to_int(option.get("partition"))
                if partition is None or partition <= 0:
                    partition = 1

                option_entry: Dict[str, Any] = {
                    "modifier_option_id": str(modifier_option_id),
                    "quantity": option_quantity,
                    "unit_price": option_unit_price,
                    "total_price": option_total,
                    "partition": partition,
                }

                options_payload.append(option_entry)

        total_line_price = round(base_total + options_total, 2)
        computed_subtotal += total_line_price

        milk_label = item_data.get("milk_label")
        bean_label = item_data.get("bean_label")

        per_item_notes: List[str] = []
        extra_note = item_data.get("kitchen_notes")
        if isinstance(extra_note, str):
            stripped_note = extra_note.strip()
            if stripped_note:
                per_item_notes.append(stripped_note)
        if isinstance(milk_label, str) and milk_label.strip():
            per_item_notes.append(f"Milk: {milk_label.strip()}")
        if isinstance(bean_label, str) and bean_label.strip():
            per_item_notes.append(f"Bean: {bean_label.strip()}")

        product_entry: Dict[str, Any] = {
            "product_id": str(product_id),
            "quantity": quantity,
            "unit_price": unit_price,
            "total_price": total_line_price,
            "discount_amount": 0,
            "tax_exclusive_unit_price": unit_price,
            "tax_exclusive_total_price": total_line_price,
            "options": options_payload,
        }

        if per_item_notes:
            product_entry["kitchen_notes"] = "; ".join(per_item_notes)
        else:
            product_entry["kitchen_notes"] = ""

        products_payload.append(product_entry)

    if not products_payload:
        raise HTTPException(status_code=400, detail="No valid products were provided for the order.")

    computed_subtotal = round(computed_subtotal, 2)

    subtotal_price = to_float(order_info.get("subtotal_price"))
    if subtotal_price is None:
        subtotal_price = computed_subtotal

    total_price = to_float(order_info.get("total_price"))
    if total_price is None:
        total_price = subtotal_price

    tax_exclusive_total_price = to_float(order_info.get("tax_exclusive_total_price"))
    if tax_exclusive_total_price is None:
        tax_exclusive_total_price = total_price

    discount_amount = to_float(order_info.get("discount_amount")) or 0.0
    rounding_amount = to_float(order_info.get("rounding_amount")) or 0.0
    tax_exclusive_discount_amount = to_float(order_info.get("tax_exclusive_discount_amount")) or 0.0

    business_date_value = order_info.get("business_date")
    if isinstance(business_date_value, str):
        business_date = business_date_value.strip() or None
    else:
        business_date = None
    if business_date is None:
        business_date = datetime.utcnow().strftime("%Y-%m-%d")

    due_at_value = order_info.get("due_at")
    if isinstance(due_at_value, str):
        due_at = due_at_value.strip() or None
    else:
        due_at = due_at_value if due_at_value else None

    customer_notes_value = order_info.get("customer_notes") or order_info.get("notes")
    if isinstance(customer_notes_value, str):
        customer_notes = customer_notes_value.strip()
    else:
        customer_notes = ""

    device_id_value = FOODICS_DEVICE_ID
    if not device_id_value:
        raise HTTPException(status_code=500, detail="Foodics device ID is not configured.")
    device_id = str(device_id_value).strip()

    creator_id_value = FOODICS_CREATOR_ID
    if not creator_id_value:
        raise HTTPException(status_code=500, detail="Foodics creator ID is not configured.")
    creator_id = str(creator_id_value).strip()

    closer_id_value = FOODICS_CLOSER_ID
    if not closer_id_value:
        raise HTTPException(status_code=500, detail="Foodics closer ID is not configured.")
    closer_id = str(closer_id_value).strip()

    customer_id_value = order_info.get("customer_id")
    customer_id = str(customer_id_value).strip() if customer_id_value else None

    customer_address_id_value = order_info.get("customer_address_id")
    customer_address_id = str(customer_address_id_value).strip() if customer_address_id_value else None

    discount_id_value = order_info.get("discount_id")
    discount_id = str(discount_id_value).strip() if discount_id_value else None

    coupon_code_value = order_info.get("coupon_code")
    coupon_code = coupon_code_value.strip() if isinstance(coupon_code_value, str) else None

    discount_type_value = order_info.get("discount_type")
    discount_type = discount_type_value.strip() if isinstance(discount_type_value, str) else None

    promotion_id_value = order_info.get("promotion_id")
    promotion_id = str(promotion_id_value).strip() if promotion_id_value else None

    source_override = order_info.get("source")
    source_value = to_int(source_override)

    meta_payload: Dict[str, Any] = {"origin": "sub_menu"}
    if isinstance(order_info.get("meta"), dict):
        meta_payload.update({key: value for key, value in order_info["meta"].items() if value is not None})
    if table_label:
        meta_payload["table_number"] = table_label
    elif table_id_value:
        meta_payload["table_number"] = table_id_value
    if source_override is not None:
        meta_payload["requested_source"] = source_override
    if isinstance(source_override, str) and not source_override.isdigit():
        meta_payload["submitted_via"] = source_override
    meta_payload = {key: value for key, value in meta_payload.items() if value is not None}

    if source_value is None:
        source_value = DEFAULT_FOODICS_ORDER_SOURCE

    payments_value = order_info.get("payments")
    payments = [payment for payment in payments_value if isinstance(payment, dict)] if isinstance(payments_value, list) else []

    charges_value = order_info.get("charges")
    charges = [charge for charge in charges_value if isinstance(charge, dict)] if isinstance(charges_value, list) else []

    tags_value = order_info.get("tags")
    if isinstance(tags_value, list):
        tags = [
            str(tag).strip()
            for tag in tags_value
            if isinstance(tag, (str, int)) and str(tag).strip()
        ]
    else:
        tags = []

    promotion = order_info.get("promotion") if order_info.get("promotion") is not None else None

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    order_payload: Dict[str, Any] = {
        "type": foodics_type,
        "source": source_value,
        "status": 1,
        "branch_id": str(branch_id),
        "device_id": device_id,
        "creator_id": creator_id,
        "closer_id": closer_id,
        "customer_id": customer_id,
        "guests": guests,
        "kitchen_notes": kitchen_notes,
        "customer_notes": customer_notes,
        "business_date": business_date,
        "subtotal_price": subtotal_price,
        "discount_amount": discount_amount,
        "rounding_amount": rounding_amount,
        "total_price": total_price,
        "tax_exclusive_discount_amount": tax_exclusive_discount_amount,
        "tax_exclusive_total_price": tax_exclusive_total_price,
        "payments": payments,
        "charges": charges,
        "products": products_payload,
        "tags": tags,
        "promotion": promotion,
        "due_at": due_at,
    }

    if customer_address_id is not None:
        order_payload["customer_address_id"] = customer_address_id
    if discount_id is not None:
        order_payload["discount_id"] = discount_id
    if coupon_code is not None:
        order_payload["coupon_code"] = coupon_code
    if discount_type is not None:
        order_payload["discount_type"] = discount_type
    if promotion_id is not None:
        order_payload["promotion_id"] = promotion_id
    if meta_payload:
        order_payload["meta"] = meta_payload
    if table_id_value:
        order_payload["table_id"] = table_id_value
    order_payload["opened_at"] = timestamp_str
    order_payload["created_at"] = timestamp_str
    order_payload["updated_at"] = timestamp_str
    if customer_notes == "":
        order_payload["customer_notes"] = ""

    try:
        foodics_response = post_foodics_resource("orders", order_payload)
    except HTTPException as exc:
        try:
            print("[submenu] Foodics order payload:", json.dumps(order_payload))
        except Exception:
            print("[submenu] Foodics order payload: <unserializable>")
        print("[submenu] Foodics order error:", exc.detail)
        raise
    response_payload = foodics_response.get("data", foodics_response)

    return {"status": "success", "order": response_payload}

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


@app.post("/api/sync-foodics-categories")
def sync_foodics_categories():
    result = run_foodics_sync("categories")
    result.pop("resource", None)
    return result


@app.post("/api/sync-foodics-products")
def sync_foodics_products():
    result = run_foodics_sync("products")
    result.pop("resource", None)
    return result


@app.post("/api/sync-foodics/{resource}")
def sync_foodics_resource_endpoint(resource: str):
    result = run_foodics_sync(resource)
    return result


@app.post("/api/sync-foodics-all")
def sync_foodics_all():
    context: Dict[str, Any] = {}
    aggregated: Dict[str, Dict[str, Any]] = {}

    for resource in FOODICS_SYNC_SEQUENCE:
        result = run_foodics_sync(resource, context)
        result_copy = dict(result)
        result_copy.pop("resource", None)
        aggregated[resource] = result_copy

    return {"status": "success", "results": aggregated}

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
