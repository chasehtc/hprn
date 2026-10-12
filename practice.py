l1 = [2,4,3]  
l2 = [5,6,4]        

def add_two_numbers(l1, l2):
    ans = []
    carry = 0
    i, j = 0, 0
    
    while i < len(l1) or j < len(l2) or carry:
        val1 = l1[i] if i < len(l1) else 0
        val2 = l2[j] if j < len(l2) else 0
        
        total = val1 + val2 + carry
        
        carry = total // 10
        
        ans.append(total % 10)

        i += 1
        j += 1
        
    return ans

print(add_two_numbers(l1, l2))  # Output: [0, 0, 0, 1] (Represents 1000)
