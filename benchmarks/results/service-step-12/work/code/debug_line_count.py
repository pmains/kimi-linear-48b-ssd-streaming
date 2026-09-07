text = "Hello world\nThis is a test\nWith multiple lines"
print('Text:', repr(text))
print('Line count using split:', len(text.split('\n')))
print('Line count using our helper:', len(text.split()))

# Test what happens with different line endings
print("\nTesting different line endings:")
print("Single newline:", len("hello\nworld".split()))
print("Windows line ending:", len("hello\r\nworld".split()))
print("Unix line ending:", len("hello\nworld".split()))