test_text = """Hello world
world
with multiple lines"""

print('Test text:')
print(repr(test_text))
print('Length:', len(test_text))

non_whitespace = ''.join(test_text.split())
print('Non-whitespace length:', len(non_whitespace))

for char in test_text:
    print('Char:', repr(char), 'is whitespace:', char.isspace())