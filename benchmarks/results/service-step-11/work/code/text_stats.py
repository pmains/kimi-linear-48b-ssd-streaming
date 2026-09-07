def _count_tokens(text, split_func):
    """Helper function to count tokens using the given split function."""
    if not text:
        return 0
    # Split using the provided function and count non-empty results
    tokens = split_func(text)
    return len([token for token in tokens if token])


"""
Module for counting text statistics.

This module provides functions to count words, lines, and characters in text,
with utilities for cleaning punctuation and finding most common words.
"""

def word_count(text):
    """Count the number of words in the text."""
    return _count_tokens(text, str.split)

def line_count(text):
    """Count the number of lines in the text."""
    if not text:
        return 0
    # Split on newlines specifically, don't filter empty strings
    lines = text.split('\n')
    return len(lines)

def char_count(text):
    """Count the number of characters in the text (excluding whitespace)."""
    if not text:
        return 0
    # Count characters while ignoring whitespace
    count = 0
    for char in text:
        if not char.isspace():
            count += 1
    return count

def top_words(text, n):
    """Return the n most common words (case-insensitive, ignore punctuation) as a list of (word, count)."""
    if not text:
        return []
    
    # Remove punctuation and convert to lowercase
    import re
    # Replace punctuation with spaces, then split
    cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned_text.split()
    
    # Count word frequencies
    word_counts = {}
    for word in words:
        if word:  # Skip empty strings
            word_counts[word] = word_counts.get(word, 0) + 1
    
    # Sort by frequency (descending) and then alphabetically for consistency
    sorted_words = sorted(word_counts.items(), key=lambda x: (-x[1], x[0]))
    
    # Return only the top n words
    return sorted_words[:n]


def _count_tokens(text, split_func):
    """Helper function to count tokens using the given split function."""
    if not text:
        return 0
    # Split using the provided function and count non-empty results
    tokens = split_func(text)
    return len([token for token in tokens if token])


def word_count(text):
    """Count the number of words in the text."""
    return _count_tokens(text, str.split)


def line_count(text):
    """Count the number of lines in the text."""
    if not text:
        return 0
    # Split on newlines specifically, don't filter empty strings
    lines = text.split('\n')
    return len(lines)


def char_count(text):
    """Count the number of characters in the text (excluding whitespace)."""
    if not text:
        return 0
    # Count characters while ignoring whitespace
    count = 0
    for char in text:
        if not char.isspace():
            count += 1
    return count

if __name__ == "__main__":
    # Simple test cases
    test_text = """Hello world
This is a test
With multiple lines"""
    
    print(f"Word count: {word_count(test_text)}")
    print(f"Line count: {line_count(test_text)}")
    print(f"Character count (excl. whitespace): {char_count(test_text)}")


def __main__():
    """Main function for command-line usage."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 text_stats.py <file>")
        print("  file: Path to text file to analyze")
        return
    
    file_path = sys.argv[1]
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Count statistics
        words = word_count(content)
        lines = line_count(content)
        chars = char_count(content)
        
        print(f"File: {file_path}")
        print(f"Words: {words}")
        print(f"Lines: {lines}")
        print(f"Characters (excluding whitespace): {chars}")
        
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found")
    except Exception as e:
        print(f"Error reading file: {e}")


if __name__ == "__main__":
    __main__()