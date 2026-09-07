#!/usr/bin/env python3

import sys
import os

# Add the current directory to the path so we can import text_stats
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_stats import top_words

def main():
    """Test top_words with the sentence 'the cat and the dog' for n=2."""
    result = top_words("the cat and the dog", 2)
    print(f"top_words('the cat and the dog', 2) = {result}")
    
    # Expected: [('the', 2), ('and', 1)] 
    # 'the' appears twice, 'and' appears once, and 'and' comes alphabetically before 'cat'

if __name__ == "__main__":
    main()