import tkinter as tk
from tkinter import ttk, messagebox
import csv
from datetime import datetime
import json

from database import Database


class VendingMachine:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ebene Campus Vending Machine")
        self.root.geometry("1050x650")
        self.root.minsize(900, 600)

        # back-end / state
        self.db = Database()
        self.cart = {}  # pid -> {name, price, qty}
        # assignment denominations
        self.note_denoms = [2000, 1000, 500, 200, 100, 50, 25]
        self.coin_denoms = [20, 10, 5, 1]

        self._build_style()
        self._build_layout()
        self._load_products()
        self._refresh_inventory_tree()
        self._refresh_transactions_tree()

    # ---------- styling ----------
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background="#0b0f14")
        style.configure("TLabel", background="#0b0f14", foreground="#e6edf3")
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Card.TFrame", background="#111827", relief="ridge", borderwidth=1)
        style.configure(
            "Accent.TButton",
            background="#5b8cff",
            foreground="white",
            padding=6,
            borderwidth=0,
        )
        style.map("Accent.TButton", background=[("active", "#8b5bff")])
        style.configure(
            "Danger.TButton",
            background="#ff5b6b",
            foreground="white",
            padding=6,
            borderwidth=0,
        )

    # ---------- overall layout ----------
    def _build_layout(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        self.customer_page = ttk.Frame(notebook)
        self.admin_page = ttk.Frame(notebook)

        notebook.add(self.customer_page, text="Customer")
        notebook.add(self.admin_page, text="Admin")

        self._build_customer_tab()
        self._build_admin_tab()

    # ===================== CUSTOMER TAB =====================
    def _build_customer_tab(self):
        outer = ttk.Frame(self.customer_page)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=1)

        # ----- left: catalog -----
        catalog = ttk.Frame(outer)
        catalog.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        ttk.Label(catalog, text="Vending Machine", style="Title.TLabel").pack(
            anchor="w", pady=(0, 10)
        )

        self.cakes_frame = ttk.LabelFrame(catalog, text="Cakes")
        self.cakes_frame.pack(fill="x", pady=(0, 8))

        self.drinks_frame = ttk.LabelFrame(catalog, text="Drinks")
        self.drinks_frame.pack(fill="x")

        # ----- right: cart + payment -----
        side = ttk.Frame(outer)
        side.grid(row=0, column=1, sticky="nsew")

        # Cart card
        cart_card = ttk.Frame(side, style="Card.TFrame")
        cart_card.pack(fill="both", expand=True)

        ttk.Label(cart_card, text="Cart", style="Heading.TLabel").pack(
            anchor="w", padx=10, pady=8
        )
        columns = ("name", "qty", "price", "total")
        self.cart_tree = ttk.Treeview(cart_card, columns=columns, show="headings", height=7)
        for col, text in zip(columns, ["Item", "Qty", "Price", "Line total"]):
            self.cart_tree.heading(col, text=text)
            self.cart_tree.column(
                col,
                width=90 if col != "name" else 150,
                anchor="center",
            )
        self.cart_tree.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        total_frame = ttk.Frame(cart_card)
        total_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(total_frame, text="Total:").pack(side="left")
        self.total_var = tk.StringVar(value="0.00")
        ttk.Label(
            total_frame, textvariable=self.total_var, font=("Segoe UI", 11, "bold")
        ).pack(side="left")

        remove_btn = ttk.Button(
            total_frame, text="Remove selected", command=self._remove_selected_cart_item
        )
        remove_btn.pack(side="right", padx=(4, 0))
        clear_btn = ttk.Button(total_frame, text="Clear cart", command=self._clear_cart)
        clear_btn.pack(side="right")

        # Denominations + checkout
        denom_card = ttk.Frame(side, style="Card.TFrame")
        denom_card.pack(fill="x", pady=(8, 0))

        ttk.Label(
            denom_card, text="Insert Money", style="Heading.TLabel"
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))

        self.note_vars = {}
        self.coin_vars = {}

        r = 1
        ttk.Label(denom_card, text="Notes").grid(row=r, column=0, sticky="w", padx=10)
        ttk.Label(denom_card, text="Qty").grid(row=r, column=1, sticky="w")
        ttk.Label(denom_card, text="Coins").grid(
            row=r, column=2, sticky="w", padx=(20, 0)
        )
        ttk.Label(denom_card, text="Qty").grid(row=r, column=3, sticky="w")
        r += 1

        max_rows = max(len(self.note_denoms), len(self.coin_denoms))
        for i in range(max_rows):
            if i < len(self.note_denoms):
                val = self.note_denoms[i]
                ttk.Label(denom_card, text=f"Rs {val}").grid(
                    row=r + i, column=0, sticky="w", padx=10
                )
                var = tk.StringVar(value="0")
                ttk.Entry(denom_card, textvariable=var, width=5).grid(
                    row=r + i, column=1, sticky="w"
                )
                self.note_vars[val] = var
            if i < len(self.coin_denoms):
                val = self.coin_denoms[i]
                ttk.Label(denom_card, text=f"Rs {val}").grid(
                    row=r + i, column=2, sticky="w", padx=(20, 0)
                )
                var = tk.StringVar(value="0")
                ttk.Entry(denom_card, textvariable=var, width=5).grid(
                    row=r + i, column=3, sticky="w"
                )
                self.coin_vars[val] = var

        checkout_btn = ttk.Button(
            denom_card,
            text="Pay & Checkout",
            style="Accent.TButton",
            command=self._checkout,
        )
        checkout_btn.grid(
            row=r + max_rows, column=0, columnspan=4, pady=10, padx=10, sticky="ew"
        )

        self.change_var = tk.StringVar(value="")
        ttk.Label(
            denom_card, textvariable=self.change_var, wraplength=320
        ).grid(
            row=r + max_rows + 1,
            column=0,
            columnspan=4,
            padx=10,
            pady=(0, 10),
            sticky="w",
        )

    # ===================== ADMIN TAB =====================
    def _build_admin_tab(self):
        outer = ttk.Frame(self.admin_page)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(outer, text="Admin Panel", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        # ----- Inventory -----
        inv_frame = ttk.LabelFrame(outer, text="Inventory")
        inv_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        columns = ("id", "name", "price", "qty", "type")
        self.inventory_tree = ttk.Treeview(
            inv_frame, columns=columns, show="headings", height=10
        )
        for col, text in zip(columns, ["ID", "Name", "Price", "Qty", "Type"]):
            self.inventory_tree.heading(col, text=text)
            self.inventory_tree.column(
                col, width=70 if col != "name" else 140, anchor="center"
            )
        self.inventory_tree.pack(fill="both", expand=True, padx=6, pady=6)

        form = ttk.Frame(inv_frame)
        form.pack(fill="x", padx=6, pady=(0, 6))
        self.inv_id = tk.StringVar()
        self.inv_name = tk.StringVar()
        self.inv_price = tk.StringVar()
        self.inv_qty = tk.StringVar()
        self.inv_type = tk.StringVar(value="cake")

        ttk.Entry(form, textvariable=self.inv_id, width=6).grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(form, textvariable=self.inv_name, width=18).grid(row=0, column=1, padx=(0, 4))
        ttk.Entry(form, textvariable=self.inv_price, width=8).grid(row=0, column=2, padx=(0, 4))
        ttk.Entry(form, textvariable=self.inv_qty, width=6).grid(row=0, column=3, padx=(0, 4))
        ttk.Combobox(
            form,
            textvariable=self.inv_type,
            values=["cake", "drink"],
            width=8,
            state="readonly",
        ).grid(row=0, column=4, padx=(0, 4))

        ttk.Button(
            form,
            text="Add",
            style="Accent.TButton",
            command=self._admin_add_product,
        ).grid(row=1, column=0, columnspan=2, pady=(6, 0), sticky="ew")
        ttk.Button(
            form, text="Update", command=self._admin_update_product
        ).grid(row=1, column=2, pady=(6, 0), sticky="ew")
        ttk.Button(
            form,
            text="Delete",
            style="Danger.TButton",
            command=self._admin_delete_product,
        ).grid(row=1, column=3, columnspan=2, pady=(6, 0), sticky="ew")

        # ----- Transactions -----
        tx_frame = ttk.LabelFrame(outer, text="Transactions")
        tx_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        tx_cols = ("id", "date", "time", "total", "inserted", "change", "products")
        self.transaction_tree = ttk.Treeview(
            tx_frame, columns=tx_cols, show="headings", height=10
        )
        headings = [
            "ID",
            "Date",
            "Time",
            "Total",
            "Inserted (Rs)",
            "Change (Rs)",
            "Products JSON",
        ]
        widths = [40, 80, 80, 70, 120, 120, 200]
        for col, h, w in zip(tx_cols, headings, widths):
            self.transaction_tree.heading(col, text=h)
            self.transaction_tree.column(col, width=w, anchor="center")
        self.transaction_tree.pack(fill="both", expand=True, padx=6, pady=6)

        btn_frame = ttk.Frame(tx_frame)
        btn_frame.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_transactions_tree).pack(
            side="left"
        )
        ttk.Button(
            btn_frame,
            text="Export CSV",
            style="Accent.TButton",
            command=self._export_transactions_csv,
        ).pack(side="right")

    # ===================== PRODUCTS / CATALOG =====================
    def _load_products(self):
        for child in self.cakes_frame.winfo_children():
            child.destroy()
        for child in self.drinks_frame.winfo_children():
            child.destroy()

        cakes = self.db.get_products_by_type("cake")
        drinks = self.db.get_products_by_type("drink")

        self._populate_product_frame(self.cakes_frame, cakes)
        self._populate_product_frame(self.drinks_frame, drinks)

    def _populate_product_frame(self, frame, rows):
        for row in rows:
            pid, name, price, qty, ptype = row
            card = ttk.Frame(frame, style="Card.TFrame")
            card.pack(fill="x", pady=3, padx=4)

            ttk.Label(
                card, text=f"{name}  (Rs {price:.2f})  | stock: {qty}"
            ).pack(side="left", padx=6, pady=4)

            qty_var = tk.StringVar(value="1")
            ttk.Entry(card, textvariable=qty_var, width=4).pack(side="right", padx=(0, 6))
            ttk.Label(card, text="Qty").pack(side="right")

            def add_closure(
                pid=pid, name=name, price=price, stock=qty, var=qty_var
            ):
                self._add_to_cart(pid, name, price, stock, var)

            ttk.Button(
                card, text="Add", style="Accent.TButton", command=add_closure
            ).pack(side="right", padx=6, pady=4)

    # ===================== CART LOGIC =====================
    def _add_to_cart(self, pid, name, price, stock, qty_var):
        try:
            qty = int(qty_var.get())
        except ValueError:
            messagebox.showerror("Invalid quantity", "Quantity must be a whole number.")
            return
        if qty <= 0:
            messagebox.showerror("Invalid quantity", "Quantity must be at least 1.")
            return

        existing_qty = self.cart.get(pid, {}).get("qty", 0)
        if existing_qty + qty > stock:
            messagebox.showerror("Stock exceeded", f"Only {stock} units available.")
            return

        if pid not in self.cart:
            self.cart[pid] = {"name": name, "price": price, "qty": 0}
        self.cart[pid]["qty"] += qty

        self._refresh_cart_tree()

    def _refresh_cart_tree(self):
        for item in self.cart_tree.get_children():
            self.cart_tree.delete(item)

        total = 0.0
        for pid, info in self.cart.items():
            line_total = info["price"] * info["qty"]
            total += line_total
            self.cart_tree.insert(
                "",
                "end",
                values=(
                    info["name"],
                    info["qty"],
                    f"{info['price']:.2f}",
                    f"{line_total:.2f}",
                ),
            )
        self.total_var.set(f"{total:.2f}")

    def _remove_selected_cart_item(self):
        sel = self.cart_tree.selection()
        if not sel:
            return
        values = self.cart_tree.item(sel[0], "values")
        name, qty_str, price_str, _ = values
        qty = int(qty_str)
        price = float(price_str)
        pid_to_remove = None
        for pid, info in self.cart.items():
            if (
                info["name"] == name
                and abs(info["price"] - price) < 1e-6
                and info["qty"] == qty
            ):
                pid_to_remove = pid
                break
        if pid_to_remove is not None:
            del self.cart[pid_to_remove]
        self._refresh_cart_tree()

    def _clear_cart(self):
        self.cart.clear()
        self._refresh_cart_tree()

    # ===================== CHECKOUT & CHANGE =====================
    def _checkout(self):
        if not self.cart:
            messagebox.showwarning("Empty cart", "Please add items to the cart first.")
            return

        total = float(self.total_var.get())
        notes_qty = {}
        coins_qty = {}
        inserted_amount = 0

        for denom, var in self.note_vars.items():
            try:
                q = int(var.get() or "0")
            except ValueError:
                q = 0
            if q < 0:
                q = 0
            notes_qty[denom] = q
            inserted_amount += denom * q

        for denom, var in self.coin_vars.items():
            try:
                q = int(var.get() or "0")
            except ValueError:
                q = 0
            if q < 0:
                q = 0
            coins_qty[denom] = q
            inserted_amount += denom * q

        if inserted_amount < total - 1e-9:
            messagebox.showerror(
                "Not enough money",
                f"Inserted amount Rs {inserted_amount:.2f} is less than total Rs {total:.2f}.",
            )
            return

        change_amount = round(inserted_amount - total, 2)
        change_notes, change_coins = self._compute_change(change_amount)

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        products_list = []
        for pid, info in self.cart.items():
            products_list.append(
                {
                    "id": pid,
                    "name": info["name"],
                    "qty": info["qty"],
                    "unit_price": info["price"],
                }
            )

        amount_inserted_dict = {
            "amount": inserted_amount,
            "notes": notes_qty,
            "coins": coins_qty,
        }
        change_dict = {
            "amount": change_amount,
            "notes": change_notes,
            "coins": change_coins,
        }

        self.db.log_transaction(
            date_str, time_str, total, amount_inserted_dict, change_dict, products_list
        )
        self.db.decrement_stock(self.cart)
        self.change_var.set(
            self._format_change_text(change_amount, change_notes, change_coins)
        )

        messagebox.showinfo(
            "Payment successful", f"Payment successful!\nChange: Rs {change_amount:.2f}"
        )

        self._clear_cart()
        for var in self.note_vars.values():
            var.set("0")
        for var in self.coin_vars.values():
            var.set("0")

        self._load_products()
        self._refresh_inventory_tree()
        self._refresh_transactions_tree()

    def _compute_change(self, amount):
        remaining = int(round(amount))
        notes = {d: 0 for d in self.note_denoms}
        coins = {d: 0 for d in self.coin_denoms}
        for d in self.note_denoms:
            if remaining >= d:
                cnt = remaining // d
                notes[d] = int(cnt)
                remaining -= d * cnt
        for d in self.coin_denoms:
            if remaining >= d:
                cnt = remaining // d
                coins[d] = int(cnt)
                remaining -= d * cnt
        return notes, coins

    def _format_change_text(self, amount, notes, coins):
        parts = [f"Change: Rs {amount:.2f}"]
        note_str = ", ".join([f"Rs {d} × {c}" for d, c in notes.items() if c])
        coin_str = ", ".join([f"Rs {d} × {c}" for d, c in coins.items() if c])
        if note_str:
            parts.append("Notes: " + note_str)
        if coin_str:
            parts.append("Coins: " + coin_str)
        return " | ".join(parts)

    # ===================== ADMIN – INVENTORY =====================
    def _refresh_inventory_tree(self):
        for item in self.inventory_tree.get_children():
            self.inventory_tree.delete(item)
        rows = self.db.get_all_products()
        for row in rows:
            pid, name, price, qty, ptype = row
            self.inventory_tree.insert(
                "", "end", values=(pid, name, f"{price:.2f}", qty, ptype)
            )

    def _admin_add_product(self):
        try:
            pid = int(self.inv_id.get())
            name = self.inv_name.get().strip()
            price = float(self.inv_price.get())
            qty = int(self.inv_qty.get())
            ptype = self.inv_type.get()
        except ValueError:
            messagebox.showerror(
                "Invalid data", "Please enter valid numeric values for ID, price and quantity."
            )
            return
        if not name:
            messagebox.showerror("Invalid data", "Name cannot be empty.")
            return
        self.db.add_product(pid, name, price, qty, ptype)
        self._refresh_inventory_tree()
        self._load_products()

    def _admin_update_product(self):
        try:
            pid = int(self.inv_id.get())
            name = self.inv_name.get().strip()
            price = float(self.inv_price.get())
            qty = int(self.inv_qty.get())
            ptype = self.inv_type.get()
        except ValueError:
            messagebox.showerror(
                "Invalid data", "Please enter valid numeric values for ID, price and quantity."
            )
            return
        if not name:
            messagebox.showerror("Invalid data", "Name cannot be empty.")
            return
        self.db.update_product(pid, name, price, qty, ptype)
        self._refresh_inventory_tree()
        self._load_products()

    def _admin_delete_product(self):
        try:
            pid = int(self.inv_id.get())
        except ValueError:
            messagebox.showerror("Invalid data", "Please enter a valid numeric ID.")
            return
        self.db.delete_product(pid)
        self._refresh_inventory_tree()
        self._load_products()

    # ===================== ADMIN – TRANSACTIONS =====================
    def _refresh_transactions_tree(self):
        for item in self.transaction_tree.get_children():
            self.transaction_tree.delete(item)
        for row in self.db.get_all_transactions():
            t_id, date, time_, total, inserted, change, products = row
            self.transaction_tree.insert(
                "",
                "end",
                values=(
                    t_id,
                    date,
                    time_,
                    f"{total:.2f}",
                    f"{inserted.get('amount', 0):.2f}",
                    f"{change.get('amount', 0):.2f}",
                    json.dumps(products),
                ),
            )

    def _export_transactions_csv(self):
        rows = self.db.get_all_transactions()
        if not rows:
            messagebox.showinfo("Export CSV", "No transactions to export.")
            return
        filename = "transactions_export.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "date",
                    "time",
                    "total",
                    "inserted_amount",
                    "change_amount",
                    "inserted_json",
                    "change_json",
                    "products_json",
                ]
            )
            for t_id, date, time_, total, inserted, change, products in rows:
                writer.writerow(
                    [
                        t_id,
                        date,
                        time_,
                        f"{total:.2f}",
                        f"{inserted.get('amount', 0):.2f}",
                        f"{change.get('amount', 0):.2f}",
                        json.dumps(inserted),
                        json.dumps(change),
                        json.dumps(products),
                    ]
                )
        messagebox.showinfo("Export CSV", f"Transactions exported to {filename}")
