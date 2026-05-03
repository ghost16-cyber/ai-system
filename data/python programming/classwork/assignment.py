import csv
from datetime import datetime


vending_cake = [
    {"id":41, "name" : "Snickers", "price": 35, "quantity": 5, "type": "cake"},
    {"id":42, "name" : "Slai o lai", "price": 25, "quantity": 3, "type": "cake"},
    {"id":43, "name" : "Sando", "price": 12, "quantity": 3, "type": "cake"},
    {"id":44, "name" : "Sando", "price": 12, "quantity": 4, "type": "cake"},
    {"id":45, "name" : "Snickers", "price": 35, "quantity": 4, "type": "cake"},
    {"id":46, "name" : "Yeah", "price": 15, "quantity": 4, "type":  "cake"},
    ]
vending_drinks =[
    {"id":31, "name":"Fuze Tea", "price": 50, "quantity": 4, "type": "Drink" },
    {"id":32, "name":"Coca Cola", "price":50, "quantity": 5, "type": "Drink"},
    {"id":33, "name":"Crystal", "price":25, "quantity": 6, "type": "Drink"},
    {"id":34, "name":"Mirinda White", "price":50, "quantity": 6, "type": "Drink"},
    {"id":35, "name":"Mirinda Red", "price":50, "quantity": 6, "type": "Drink"},
    {"id":36, "name":"Coca Cola", "price":50, "quantity": 6, "type": "Drink"},
    ]

denominations = {
    "notes": [100, 50, 25],
    "coins": [10, 5, 1]
}
def display_items():
    print("--------VENDING MACHINE--------\n")

    print("Cake:")
    for cake in vending_cake:
        if cake["type"] == "cake":
            print(f"ID: {cake["id"]} - {cake['name']} - Rs{cake['price']} - Qty: {cake['quantity']}")

    print("\nDrinks:")
    for drink in vending_drinks:
        if drink["type"] == "Drink":
            print(f"ID: {drink['id']} - {drink['name']} - Rs{drink['price']} - Qty: {drink['quantity']}")

    print("\n-------------")
display_items()


def is_integer(value):
    if value.isdigit():
        return True
    return False


def get_money_input():
    print("\nPlease insert money (enter denomination amounts one by one, enter 0 when done):")
    total = 0
    input_details = {}

    while True:
        amount_input = input("Enter denomination (Rs): ")
        if not is_integer(amount_input):
            print("Please enter a valid number.")
            continue

        amount = int(amount_input)
        if amount == 0:
            break
        if amount not in denominations["notes"] and amount not in denominations["coins"]:
            print("Invalid denomination. Please use valid Mauritian currency.")
            continue

        count_input = input(f"How many Rs {amount} notes/coins? ")
        if not is_integer(count_input):
            print("Please enter a valid number.")
            continue

        count = int(count_input)
        if count < 0:
            print("Please enter a positive number.")
            continue

        total += amount * count
        input_details[amount] = count

    return total, input_details


def calculate_change(amount_paid, total_cost):
    change = amount_paid - total_cost
    if change < 0:
        return None, None

    change_details = {}
    remaining = change

    # Sort denominations in descending order
    all_denominations = sorted(denominations["notes"] + denominations["coins"], reverse=True)

    for denom in all_denominations:
        if remaining >= denom:
            count = remaining // denom
            change_details[denom] = count
            remaining -= denom * count

    return change, change_details


def log_transaction(product_id, quantity, total_cost, input_details, change_details):
    timestamp = datetime.now()
    date_str = timestamp.strftime("%Y-%m-%d")
    time_str = timestamp.strftime("%H:%M:%S")

    # Format input details
    input_str = ", ".join([f"{v}xRs{k}" for k, v in input_details.items()])

    # Format change details if any
    change_str = "None"
    if change_details:
        change_str = ", ".join([f"{v}xRs{k}" for k, v in change_details.items()])

    # Write to CSV
    with open('vending_transactions.csv', 'a', newline='') as file:
        writer = csv.writer(file)
        # Write header if file is empty
        if file.tell() == 0:
            writer.writerow(
                ["Date", "Time", "Product ID", "Quantity", "Total Cost", "Amount Inserted", "Change Returned"])

        writer.writerow([
            date_str,
            time_str,
            product_id,
            quantity,
            f"Rs{total_cost}",
            input_str,
            change_str
        ])


def main():
    print("Welcome to Ebene Campus Vending Machine!")

    while True:
        display_products()

        product_id_input = input("\nEnter the product ID you want to purchase (0 to exit): ")
        if not is_integer(product_id_input):
            print("Please enter a valid number.")
            continue

        product_id = int(product_id_input)
        if product_id == 0:
            print("Thank you for using our vending machine!")
            break

        product = None
        for p in products:
            if p["id"] == product_id:
                product = p
                break

        if not product:
            print("Invalid product ID. Please try again.")
            continue

        if product["quantity"] <= 0:
            print("This product is out of stock. Please select another.")
            continue

        quantity_input = input(f"Enter quantity for {product['name']} (max {product['quantity']}): ")
        if not is_integer(quantity_input):
            print("Please enter a valid number.")
            continue

        quantity = int(quantity_input)
        if quantity <= 0:
            print("Quantity must be positive.")
            continue
        if quantity > product["quantity"]:
            print(f"Only {product['quantity']} available. Please enter a lower quantity.")
            continue

        total_cost = product["price"] * quantity
        print(f"\nTotal cost: Rs{total_cost}")

        amount_paid, input_details = get_money_input()
        if amount_paid < total_cost:
            print(f"Insufficient amount. Rs{total_cost - amount_paid} more needed. Transaction cancelled.")
            continue

        change, change_details = calculate_change(amount_paid, total_cost)

        if change is not None:
            product["quantity"] -= quantity
            print(f"\nDispensing {quantity} {product['name']}(s)...")
            print(f"Thank you! Your change is Rs{change}")
            if change > 0:
                print("Change breakdown:")
                for denom, count in change_details.items():
                    print(f"  Rs{denom}: {count}")

            # Log the transaction
            log_transaction(product_id, quantity, total_cost, input_details, change_details)
        else:
            print("Error calculating change. Transaction cancelled.")

        input("\nPress Enter to continue or Ctrl+C to exit...")


if __name__ == "__main__":
    main()