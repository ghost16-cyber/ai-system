import pymysql
import json
from datetime import datetime

# ---------------- AWS RDS CONFIG ----------------
DB_HOST = "vending-db.cn4sicq46640.eu-north-1.rds.amazonaws.com"    
DB_PORT = 3306
DB_NAME = "vending_db"                 
DB_USER = "vm_user"              
DB_PASSWORD = "palla16moon02"          
# ------------------------------------------------


class Database:
    def __init__(self):
        # Connect to AWS RDS MySQL
        self.conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.init_database()

    # ---------- schema & seed ----------
    def init_database(self):
        cur = self.conn.cursor()

        # Create products table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                quantity INT NOT NULL,
                type VARCHAR(20) NOT NULL
            )
        """)

        # Create transactions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                date VARCHAR(20) NOT NULL,
                time VARCHAR(20) NOT NULL,
                total_amount DECIMAL(10,2) NOT NULL,
                amount_inserted TEXT NOT NULL,
                change_returned TEXT NOT NULL,
                products_purchased TEXT NOT NULL
            )
        """)

        # Seed products if table is empty
        cur.execute("SELECT COUNT(*) AS c FROM products")
        count = cur.fetchone()["c"]

        if count == 0:
            products = [
                (1, "Chocolate Cake", 35, 10, "cake"),
                (2, "Vanilla Cake", 35, 8, "cake"),
                (3, "Red Velvet", 60, 5, "cake"),
                (4, "Coca Cola", 55, 20, "drink"),
                (5, "Sprite", 55, 15, "drink"),
                (6, "Orange Juice", 45, 12, "drink"),
            ]

            cur.executemany(
                """
                INSERT INTO products (id, name, price, quantity, type)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    price = VALUES(price),
                    quantity = VALUES(quantity),
                    type = VALUES(type)
                """,
                products
            )

    # ---------- product helpers ----------
    def get_products_by_type(self, product_type: str):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, name, price, quantity, type FROM products WHERE type=%s ORDER BY id",
            (product_type,)
        )
        rows = cur.fetchall()
        return [(r["id"], r["name"], float(r["price"]), r["quantity"], r["type"]) for r in rows]

    def get_all_products(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, price, quantity, type FROM products ORDER BY id")
        rows = cur.fetchall()
        return [(r["id"], r["name"], float(r["price"]), r["quantity"], r["type"]) for r in rows]

    def add_product(self, pid: int, name: str, price: float, qty: int, ptype: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO products (id, name, price, quantity, type)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                price=VALUES(price),
                quantity=VALUES(quantity),
                type=VALUES(type)
            """,
            (pid, name, price, qty, ptype),
        )

    def update_product(self, pid: int, name: str, price: float, qty: int, ptype: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE products SET name=%s, price=%s, quantity=%s, type=%s WHERE id=%s",
            (name, price, qty, ptype, pid),
        )

    def delete_product(self, pid: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM products WHERE id=%s", (pid,))

    def decrement_stock(self, cart: dict):
        cur = self.conn.cursor()
        for pid, info in cart.items():
            cur.execute(
                "UPDATE products SET quantity = quantity - %s WHERE id=%s",
                (int(info["qty"]), int(pid))
            )

    # ---------- transactions ----------
    def log_transaction(self, date, time, total_amount, amount_inserted, change_returned, products_purchased):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO transactions(
                date, time, total_amount,
                amount_inserted, change_returned, products_purchased
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                date,
                time,
                total_amount,
                json.dumps(amount_inserted),
                json.dumps(change_returned),
                json.dumps(products_purchased),
            )
        )

    def get_all_transactions(self):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM transactions ORDER BY id DESC")

        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append(
                (
                    r["id"],
                    r["date"],
                    r["time"],
                    float(r["total_amount"]),
                    json.loads(r["amount_inserted"]),
                    json.loads(r["change_returned"]),
                    json.loads(r["products_purchased"]),
                )
            )
        return results
