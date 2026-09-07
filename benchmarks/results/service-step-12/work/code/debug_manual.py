text = "hello   world"
print('Text:', repr(text))
print('Length:', len(text))

# Count characters manually
manual_count = 0
for char in text:
    if not char.isspace():
        manual_count += 1
        print(f"Counting: {repr(char)} - is whitespace: {char.isspace()}")

print('Manual count:', manual_count)

# What if we count spaces as non-whitespace?
special_count = 0
for char in text:
    if not char.isspace() or char == ' ':
        special_count += 1
        print(f"Special count: {repr(char)} - is whitespace: {char.isspace()}")

print('Special count (counting spaces as non-whitespace):', special_count)