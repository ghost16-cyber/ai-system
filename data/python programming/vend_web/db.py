import os, json
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///vending_web.db")
engine = create_engine(DATABASE_URL, future=True)

def init_db():
    here = os.path.dirname(__file__)
    schema_path = os.path.join(here, "schema.sql")
    with engine.begin() as conn:
        with open(schema_path, "r", encoding="utf-8") as f:
            raw = f.read()
        for stmt in raw.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))

def get_products_by_type(ptype):
    with engine.begin() as conn:
        res = conn.execute(text("SELECT id,name,price,quantity,type FROM products WHERE type = :t ORDER BY id"),
                           {"t": ptype}).all()
        return [dict(r._mapping) for r in res]

def get_all_products():
    with engine.begin() as conn:
        res = conn.execute(text("SELECT id,name,price,quantity,type FROM products ORDER BY id")).all()
        return [dict(r._mapping) for r in res]

def add_product(pid, name, price, qty, ptype):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO products(id,name,price,quantity,type) VALUES (:i,:n,:p,:q,:t)"),
                     {"i": pid, "n": name, "p": float(price), "q": int(qty), "t": ptype})

def update_product(pid, name, price, qty, ptype):
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET name=:n, price=:p, quantity=:q, type=:t WHERE id=:i"),
                     {"i": pid, "n": name, "p": float(price), "q": int(qty), "t": ptype})

def delete_product(pid):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM products WHERE id=:i"), {"i": pid})

def verify_admin(u, pw):
    with engine.begin() as conn:
        res = conn.execute(text("SELECT 1 FROM admins WHERE username=:u AND password=:p"),
                           {"u": u, "p": pw}).first()
        return res is not None

def log_transaction(date, time, total_amount, amount_inserted, change_returned, products_purchased):
    if not isinstance(amount_inserted, str): amount_inserted = json.dumps(amount_inserted)
    if not isinstance(change_returned, str): change_returned = json.dumps(change_returned)
    if not isinstance(products_purchased, str): products_purchased = json.dumps(products_purchased)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO transactions(date,time,total_amount,amount_inserted,change_returned,products_purchased)
            VALUES (:d,:t,:tot,:ai,:cr,:pp)
        """), {"d": date, "t": time, "tot": float(total_amount),
               "ai": amount_inserted, "cr": change_returned, "pp": products_purchased})

def all_transactions():
    with engine.begin() as conn:
        res = conn.execute(text("""
            SELECT id,date,time,total_amount,amount_inserted,change_returned,products_purchased
            FROM transactions ORDER BY id DESC
        """)).all()
        rows = []
        for r in res:
            m = dict(r._mapping)
            for k in ("amount_inserted","change_returned","products_purchased"):
                v = m[k]
                if isinstance(v, (bytes, bytearray)): v = v.decode("utf-8")
                try:
                    m[k] = json.loads(v)
                except Exception:
                    m[k] = v
            rows.append(m)
        return rows

def decrement_stock(cart):
    with engine.begin() as conn:
        for pid, item in cart.items():
            conn.execute(text("UPDATE products SET quantity = quantity - :q WHERE id = :i"),
                         {"q": int(item["qty"]), "i": int(pid)})
