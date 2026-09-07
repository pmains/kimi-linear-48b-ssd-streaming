#!/usr/bin/env python3
"""
Simple text analysis module for counting words, lines, and characters.
"""

import re

def word_count(text: str) -> int:
    """Count the number of words in the text. Words are sequences of alphanumeric characters separated by whitespace."""
    if not text:
        return 0
    # Split on any whitespace and filter out empty strings
    words = re.split(r'\s+', text.strip())
    return len([word for word in words if word])

def line_count(text: str) -> int:
    """Count the number of lines in the text."""
    if not text:
        return 0
    # Split on newlines and filter out empty strings
    lines = text.split('\n')
    return len([line for line in lines if line.strip()])

def char_count(text: str) -> int:
    """Count the number of characters in the text, excluding whitespace."""
    if not text:
        return 0
    # Remove all whitespace characters and count the remaining characters
    return len(re.sub(r'\s+', '', text))

if __name__ == "__main__":
    # Simple test when run directly
    test_text = """Hello world!
This is a test.
Count words, lines, and characters.
"""
    print(f"Text: {repr(test_text)}")
    print(f"Word count: {word_count(test_text)}")
    print(f"Line count: {line_count(test_text)}")
    print(f"Char count (excl whitespace): {char_count(test_text)}")