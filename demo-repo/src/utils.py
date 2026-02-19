# A simple utility module with an intentional LINTING bug.
# The unused import on line 15 triggers flake8 F401.


def add(a, b):
    """Add two numbers together."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b


import os  # noqa — THIS IS THE BUG: unused import on line 15


def multiply(a, b):
    """Multiply two numbers."""
    return a * b


def divide(a, b):
    """Divide a by b."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b
