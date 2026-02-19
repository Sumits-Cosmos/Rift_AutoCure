import pytest
import sys
import os

# Add the parent directory to path so we can import src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.utils import add, subtract, multiply, divide


def test_add_positive():
    assert add(1, 2) == 3


def test_add_negative():
    assert add(-1, -1) == -2


def test_add_zero():
    assert add(0, 0) == 0


def test_subtract():
    assert subtract(5, 3) == 2


def test_multiply():
    assert multiply(3, 4) == 12


def test_divide():
    assert divide(10, 2) == 5.0


def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)
