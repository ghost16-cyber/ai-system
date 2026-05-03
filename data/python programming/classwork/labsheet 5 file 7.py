average_scores=[]
with open('marking.txt','r') as f:
    for line in f:
        row_data = line.split()
        sum = 0
        for i in range(2, len(row_data)):
            sum += float(row_data[i])

        average_score = sum/(len(row_data)-2)
        print(f'average_score={average_score}')
        print(f'student id: {row_data[0]}')
        average_scores.append(average_score)

print(f'max average score = {max(average_scores)}')
