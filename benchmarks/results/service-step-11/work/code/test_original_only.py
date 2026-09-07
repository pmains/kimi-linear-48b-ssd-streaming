#!/usr/bin/env python3

def char_count_original(text):
    """Count the number of characters in the text (excluding whitespace)."""
    if not text:
        return 0
    # Remove all whitespace characters and count the remaining characters
    non_whitespace = ''.join(text.split())
    return len(non_whitespace)

def test_char_count():
    """Test char_count function."""
    # Test multiple spaces
    result = char_count_original("hello   world")
    expected = 11
    print(f"char_count('hello   world') = {result}, expected {expected}")
    assert result == expected, f"Expected {expected}, got {result}"
    print("✓ Multiple spaces test passed")

test_char_count()