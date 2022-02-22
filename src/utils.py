from datetime import datetime


def format_date(date: datetime) -> str:
    return date.strftime("%a, %b %d %Y at %I:%M %p %Z")
