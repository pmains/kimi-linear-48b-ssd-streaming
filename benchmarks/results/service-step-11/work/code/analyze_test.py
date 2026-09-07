# Analyze all the test cases

test_cases = [
    ("", 0),
    ("a", 1),
    ("hello", 5),
    ("hello world", 10),  # h-e-l-l-o- -w-o-r-l-d = 10 non-whitespace
    ("hello\\nworld", 10),  # h-e-l-l-o-\\n-w-o-r-l-d = 10 non-whitespace  
    ("hello   world", 11),  # This is the problematic one
]

for text, expected in test_cases:
    print(f"Text: {repr(text)}")
    print(f"Expected: {expected}")
    
    # Count manually
    manual_count = sum(1 for char in text if not char.isspace())
    print(f"Manual count: {manual_count}")
    
    # Count with split
    split_count = len(''.join(text.split()))
    print(f"Split method: {split_count}")
    
    print("---")