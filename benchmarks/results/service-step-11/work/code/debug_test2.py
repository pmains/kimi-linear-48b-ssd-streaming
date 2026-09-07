text = "hello\nworld"
print('Text:', repr(text))
print('Total length:', len(text))
non_whitespace = ''.join(text.split())
print('Non-whitespace length:', len(non_whitespace))
count = 0
for char in text:
    if not char.isspace():
        count += 1
print('Manual count:', count)