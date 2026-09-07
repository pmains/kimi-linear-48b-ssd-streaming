#!/usr/bin/env python3

import sys
import os

# Add the current directory to the path so we can import text_stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_stats import word_count, line_count, char_count

def test_char_count():
    """Test char_count function."""
    print("Testing char_count function...")
    
    # Test empty string
    result = char_count("")
    expected = 0
    print(f"Empty string: char_count('') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test single character
    result = char_count("a")
    expected = 1
    print(f"Single char: char_count('a') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test multiple characters
    result = char_count("hello")
    expected = 5
    print(f"Multiple chars: char_count('hello') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test spaces (should not count as characters)
    result = char_count("hello world")
    expected = 10
    print(f"With spaces: char_count('hello world') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test newlines (should not count as characters)
    result = char_count("hello\nworld")
    expected = 10
    print(f"With newlines: char_count('hello\\nworld') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test multiple spaces
    result = char_count("hello   world")
    expected = 11
    print(f"Multiple spaces: char_count('hello   world') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    
    print("All char_count tests passed!")

if __name__ == "__main__":
    test_char_count()