def char_count_original(text):
    """Count the number of characters in the text (excluding whitespace)."""
    if not text:
        return 0
    # Remove all whitespace characters and count the remaining characters
    non_whitespace = ''.join(text.split())
    return len(non_whitespace)

text = "hello   world"
print('Text:', repr(text))
print('Original method:', char_count_original(text))

# Let's see what happens step by step
split_result = text.split()
print('After split():', repr(split_result))
joined = ''.join(split_result)
print('Joined:', repr(joined))
print('Length:', len(joined))