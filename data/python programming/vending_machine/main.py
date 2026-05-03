import tkinter as tk
from vending_machine import VendingMachine

if __name__ == "__main__":
    root = tk.Tk()
    app = VendingMachine(root)
    root.mainloop()