from __future__ import annotations

import os
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    color TEXT,
    brand TEXT,
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
"""


SEED_SQL = """
INSERT OR IGNORE INTO categories (name) VALUES
  ('Smartphones'),
  ('Laptops'),
  ('Headphones'),
  ('Wearables'),
  ('Accessories');

-- Products: price_cents in cents (e.g., 99900 = 999.00)
INSERT OR IGNORE INTO products (name, category_id, price_cents, quantity, color, brand) VALUES
  ('iPhone 15 Pro 256GB', (SELECT id FROM categories WHERE name='Smartphones'), 129900, 7, 'Black', 'Apple'),
  ('iPhone 14 128GB',     (SELECT id FROM categories WHERE name='Smartphones'),  79900, 3, 'Blue',  'Apple'),
  ('Samsung Galaxy S24',  (SELECT id FROM categories WHERE name='Smartphones'),  99900, 5, 'Gray',  'Samsung'),
  ('Google Pixel 8',      (SELECT id FROM categories WHERE name='Smartphones'),  89900, 2, 'Black', 'Google'),

  ('MacBook Air M2 13"',  (SELECT id FROM categories WHERE name='Laptops'),     119900, 4, 'Silver','Apple'),
  ('Dell XPS 13',         (SELECT id FROM categories WHERE name='Laptops'),     139900, 1, 'Black', 'Dell'),
  ('Lenovo ThinkPad X1',  (SELECT id FROM categories WHERE name='Laptops'),     159900, 2, 'Black', 'Lenovo'),

  ('Sony WH-1000XM5',     (SELECT id FROM categories WHERE name='Headphones'),   34900, 6, 'Black', 'Sony'),
  ('AirPods Pro 2',       (SELECT id FROM categories WHERE name='Headphones'),   24900, 8, 'White', 'Apple'),

  ('Apple Watch Series 9',(SELECT id FROM categories WHERE name='Wearables'),    39900, 3, 'Red',   'Apple'),
  ('Garmin Forerunner 265',(SELECT id FROM categories WHERE name='Wearables'),   44900, 2, 'Black', 'Garmin'),

  ('USB-C Charger 65W',   (SELECT id FROM categories WHERE name='Accessories'),   3900, 20, 'White','Anker'),
  ('MagSafe Case iPhone', (SELECT id FROM categories WHERE name='Accessories'),   5900, 10, 'Red',  'Apple');
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    # Default: ./data/shop.db
    db_path = Path(os.getenv("DB_PATH", Path(__file__).with_name("data") / "shop.db"))
    init_db(db_path)
    print(f"OK: initialized DB at: {db_path}")


if __name__ == "__main__":
    main()