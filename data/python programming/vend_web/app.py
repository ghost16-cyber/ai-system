import os, json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from db import init_db, get_products_by_type, add_product, update_product, delete_product, \
               get_all_products, verify_admin, log_transaction, all_transactions, decrement_stock

app = Flask(__name__)

def _init_once():
    init_db()
    if not get_all_products():
        try:
            add_product(1, "Chocolate Cake", 35, 10, "cake")
            add_product(2, "Vanilla Cake", 35, 8, "cake")
            add_product(3, "Red Velvet", 50, 5, "cake")
            add_product(4, "Coca Cola", 55, 20, "drink")
            add_product(5, "Sprite", 55, 15, "drink")
            add_product(6, "Orange Juice", 55, 12, "drink")
        except Exception:
            pass

# Initialize immediately for Flask 3.x
with app.app_context():
    _init_once()


app.secret_key = os.getenv("SECRET_KEY", "dev-secret")


def get_cart():
    return session.setdefault("cart", {})

def set_cart(cart):
    session["cart"] = cart
    session.modified = True

def compute_change(amount):
    remaining = int(round(amount))
    note_denoms = [2000, 1000, 500, 200, 100, 50, 25]
    coin_denoms = [20, 10, 5, 1]
    notes = {d: 0 for d in note_denoms}
    coins = {d: 0 for d in coin_denoms}
    for d in note_denoms:
        if remaining >= d:
            cnt = remaining // d; notes[d]=int(cnt); remaining -= d*cnt
    for d in coin_denoms:
        if remaining >= d:
            cnt = remaining // d; coins[d]=int(cnt); remaining -= d*cnt
    return notes, coins

@app.get("/")
def index():
    q = (request.args.get("q") or "").lower().strip()
    cakes = [p for p in get_products_by_type("cake") if q in p["name"].lower()] 
    drinks = [p for p in get_products_by_type("drink") if q in p["name"].lower()]
    cart = get_cart()
    total = sum(int(v["qty"])*float(v["price"]) for v in cart.values())
    return render_template("index.html", cakes=cakes, drinks=drinks, cart=cart, total=total, query=q)

@app.post("/add")
def add():
    pid = str(request.form.get("pid"))
    name = request.form.get("name")
    price = float(request.form.get("price"))
    qty = int(request.form.get("qty", "1"))
    cart = get_cart()
    if pid not in cart:
        cart[pid] = {"name": name, "price": price, "qty": 0}
    cart[pid]["qty"] += qty
    set_cart(cart)
    flash(f"Added {qty} × {name}", "ok")
    return redirect(url_for("index"))

@app.post("/remove")
def remove():
    pid = str(request.form.get("pid"))
    cart = get_cart()
    cart.pop(pid, None)
    set_cart(cart)
    return redirect(url_for("index"))

@app.post("/clear")
def clear():
    set_cart({})
    return redirect(url_for("index"))

@app.post("/checkout")
def checkout():
    cart = get_cart()
    if not cart:
        flash("Cart is empty", "warn")
        return redirect(url_for("index"))
    total_due = sum(int(v["qty"])*float(v["price"]) for v in cart.values())

    notes = {int(k.split("_")[1]): int(request.form.get(k, 0) or 0)
             for k in request.form if k.startswith("note_")}
    coins = {int(k.split("_")[1]): int(request.form.get(k, 0) or 0)
             for k in request.form if k.startswith("coin_")}
    inserted = sum(d*c for d,c in notes.items()) + sum(d*c for d,c in coins.items())
    if inserted < total_due - 1e-9:
        flash(f"Inserted Rs {inserted:.2f} < Due Rs {total_due:.2f}", "warn")
        return redirect(url_for("index"))

    change_amt = round(inserted - total_due, 2)
    change_notes, change_coins = compute_change(change_amt)

    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M:%S")
    products_purchased = [{"id": int(pid), "name": it["name"], "qty": int(it["qty"]), "unit_price": float(it["price"])}
                          for pid, it in cart.items()]
    amount_inserted = {"notes": notes, "coins": coins, "amount": float(inserted)}
    change_returned = {"notes": change_notes, "coins": change_coins, "amount": float(change_amt)}

    log_transaction(date_str, time_str, float(total_due), amount_inserted, change_returned, products_purchased)
    decrement_stock(cart)
    set_cart({})
    flash(f"Payment success. Change Rs {change_amt:.2f}", "ok")
    session["last_change"] = {"notes": change_notes, "coins": change_coins, "amount": change_amt}
    return redirect(url_for("index"))

@app.get("/admin/login")
def admin_login_form():
    return render_template("admin_login.html")

@app.post("/admin/login")
def admin_login():
    u = request.form.get("username","")
    p = request.form.get("password","")
    if verify_admin(u,p):
        session["admin"]=u
        return redirect(url_for("admin_inventory"))
    flash("Invalid credentials","danger")
    return redirect(url_for("admin_login_form"))

@app.get("/admin/inventory")
def admin_inventory():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    prods = get_all_products()
    return render_template("admin_inventory.html", prods=prods)

@app.post("/admin/inventory/add")
def admin_add_prod():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    add_product(int(request.form["id"]), request.form["name"],
                float(request.form["price"]), int(request.form["quantity"]), request.form["type"])
    return redirect(url_for("admin_inventory"))

@app.post("/admin/inventory/update")
def admin_update_prod():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    update_product(int(request.form["id"]), request.form["name"],
                   float(request.form["price"]), int(request.form["quantity"]), request.form["type"])
    return redirect(url_for("admin_inventory"))

@app.post("/admin/inventory/delete")
def admin_delete_prod():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    delete_product(int(request.form["id"]))
    return redirect(url_for("admin_inventory"))

@app.get("/admin/transactions")
def admin_tx():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    rows = all_transactions()
    return render_template("admin_transactions.html", rows=rows)

@app.get("/admin/transactions/export.csv")
def admin_tx_export():
    if not session.get("admin"): return redirect(url_for("admin_login_form"))
    rows = all_transactions()
    def gen():
        import csv, io, json
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id","date","time","total_amount","inserted_amount","change_amount",
                         "inserted_notes_json","inserted_coins_json","change_notes_json","change_coins_json","items_json"])
        yield output.getvalue(); output.seek(0); output.truncate(0)
        for m in rows:
            inserted = m["amount_inserted"]; change = m["change_returned"]; items = m["products_purchased"]
            writer.writerow([
                m["id"], m["date"], m["time"], f"{float(m['total_amount']):.2f}",
                f"{float(inserted.get('amount',0)):.2f}", f"{float(change.get('amount',0)):.2f}",
                json.dumps(inserted.get("notes", {}), ensure_ascii=False),
                json.dumps(inserted.get("coins", {}), ensure_ascii=False),
                json.dumps(change.get("notes", {}), ensure_ascii=False),
                json.dumps(change.get("coins", {}), ensure_ascii=False),
                json.dumps(items, ensure_ascii=False),
            ])
            yield output.getvalue(); output.seek(0); output.truncate(0)
    return Response(gen(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=transactions.csv"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
