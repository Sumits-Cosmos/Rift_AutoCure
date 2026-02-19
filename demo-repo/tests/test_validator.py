import pytest
import sys
import os

# Add the parent directory to path so we can import src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.validator import validate_email, validate_name


def test_valid_email():
    assert validate_email("user@example.com") == True


def test_invalid_email():
    assert validate_email("notanemail") == False


def test_empty_email():
    assert validate_email("") == False


def test_valid_name():
    assert validate_name("John Doe") == True


def test_invalid_name():
    assert validate_name("") == False
