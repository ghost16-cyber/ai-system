import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('names.csv')

x_data = df['name']
y_data = df['age']
plt.plot(x_data, y_data)

