import matplotlib.pyplot as plt
import numpy as np

name = np.array(["palla", "ali", "greg", "uzair", "abdel"])
age   = np.array([21, 13, 25, 24, 21, ])

plt.scatter(name, age)
plt.show()