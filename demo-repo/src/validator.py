# A simple email validator with an intentional SYNTAX bug.
# Line 8 is missing a colon at the end of the function definition.

import re


EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

def validate_email(email)
    """Validate an email address format."""
    if not isinstance(email, str):
        return False
    return bool(EMAIL_REGEX.match(email))


def validate_name(name):
    """Validate a name is non-empty and alphabetic."""
    if not isinstance(name, str):
        return False
    return len(name.strip()) > 0 and name.replace(" ", "").isalpha()
