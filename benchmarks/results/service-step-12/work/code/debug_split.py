text = "hello   world"
print('Original text:', repr(text))
print('Length:', len(text))

split_result = text.split()
print('After split():', repr(split_result))
print('Split length:', len(split_result))

# Join the split result
joined = ''.join(split_result)
print('Joined:', repr(joined))
print('Joined length:', len(joined))

# Count manually
count = 0
for char in text:
    if not char.isspace():
        count += 1
print('Manual count:', count)