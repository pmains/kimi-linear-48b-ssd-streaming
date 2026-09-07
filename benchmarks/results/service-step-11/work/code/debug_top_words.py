import re

text = "the cat and the dog"
cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
print('Original:', repr(text))
print('Cleaned:', repr(cleaned_text))

words = cleaned_text.split()
print('Words:', words)

# Count word frequencies
word_counts = {}
for word in words:
    if word:  # Skip empty strings
        word_counts[word] = word_counts.get(word, 0) + 1

print('Word counts:', word_counts)

# Sort by frequency (descending) and then alphabetically for consistency
sorted_words = sorted(word_counts.items(), key=lambda x: (-x[1], x[0]))
print('Sorted:', sorted_words)

# Return only the top 2 words
result = sorted_words[:2]
print('Top 2:', result)