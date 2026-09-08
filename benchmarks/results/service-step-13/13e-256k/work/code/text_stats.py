def word_count(text: str) -> int:
    """Count the number of words in the text.
    
    A word is defined as a sequence of non-whitespace characters separated by whitespace.
    """
    if not text or not text.strip():
        return 0
    return len(text.split())


def line_count(text: str) -> int:
    """Count the number of lines in the text.
    
    A line is defined as a sequence of characters ending with a newline character.
    """
    if not text:
        return 0
    return text.count('\n') + (1 if text.endswith('\n') or text != text.rstrip('\n') else 0)


def char_count(text: str) -> int:
    """Count the number of characters in the text, excluding whitespace.
    
    This counts all non-whitespace characters including letters, digits, punctuation, and special characters.
    """
    if not text:
        return 0
    return len(text) - len(text.split()) + 1