#!/usr/bin/env python3

import sys
import os

# Add the current directory to the path so we can import text_stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_stats import word_count, line_count, char_count, top_words

def test_word_count():
    """Test word_count function."""
    # Test empty string
    assert word_count("") == 0, f"Expected 0, got {word_count('')}"
    
    # Test single word
    assert word_count("hello") == 1, f"Expected 1, got {word_count('hello')}"
    
    # Test multiple words
    assert word_count("hello world") == 2, f"Expected 2, got {word_count('hello world')}"
    
    # Test multiple spaces
    assert word_count("hello   world") == 2, f"Expected 2, got {word_count('hello   world')}"
    
    # Test text with newlines
    assert word_count("hello\nworld") == 2, f"Expected 2, got {word_count('hello\nworld')}"
    
    print("✓ word_count tests passed")

def test_line_count():
    """Test line_count function."""
    # Test empty string
    assert line_count("") == 0, f"Expected 0, got {line_count('')}"
    
    # Test single line
    assert line_count("hello") == 1, f"Expected 1, got {line_count('hello')}"
    
    # Test multiple lines
    assert line_count("hello\nworld") == 2, f"Expected 2, got {line_count('hello\nworld')}"
    
    # Test trailing newline
    assert line_count("hello\nworld\n") == 3, f"Expected 3, got {line_count('hello\nworld\n')}"
    
    # Test multiple newlines
    assert line_count("hello\n\n\nworld") == 4, f"Expected 4, got {line_count('hello\\n\\n\\nworld')}"
    
    print("✓ line_count tests passed")

def test_char_count():
    """Test char_count function."""
    # Test empty string
    assert char_count("") == 0, f"Expected 0, got {char_count('')}"
    
    # Test single character
    assert char_count("a") == 1, f"Expected 1, got {char_count('a')}"
    
    # Test multiple characters
    assert char_count("hello") == 5, f"Expected 5, got {char_count('hello')}"
    
    # Test spaces (should not count as characters)
    assert char_count("hello world") == 10, f"Expected 10, got {char_count('hello world')}"
    
    # Test newlines (should not count as characters)
    assert char_count("hello\nworld") == 10, f"Expected 10, got {char_count('hello\nworld')}"
    
    # Test multiple spaces
    assert char_count("hello   world") == 10, f"Expected 10, got {char_count('hello   world')}"
    
    print("✓ char_count tests passed")

def test_top_words():
    """Test top_words function."""
    # Test empty string
    assert top_words("", 2) == [], f"Expected [], got {top_words('', 2)}"
    
    # Test single word
    result = top_words("hello", 2)
    assert result == [("hello", 1)], f"Expected [(\"hello\", 1)], got {result}"
    
    # Test multiple words
    result = top_words("hello world", 2)
    # Should be sorted by frequency, then alphabetically
    expected = [("hello", 1), ("world", 1)]
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test case insensitivity
    result = top_words("Hello World", 2)
    expected = [("hello", 1), ("world", 1)]
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test punctuation handling
    result = top_words("hello, world! hello?", 2)
    expected = [("hello", 2), ("world", 1)]
    assert result == expected, f"Expected {expected}, got {result}"
    
    # Test multiple occurrences
    result = top_words("the cat and the dog", 2)
    expected = [("the", 2), ("and", 1)]  # "the" appears twice, then alphabetically "and" comes before "cat"
    assert result == expected, f"Expected {expected}, got {result}"
    
    print("✓ top_words tests passed")

def main():
    """Run all tests."""
    try:
        test_word_count()
        test_line_count()
        test_char_count()
        test_top_words()
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n💥 UNEXPECTED ERROR: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())