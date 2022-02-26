from datetime import datetime
from pytz import timezone

est = timezone('America/New_York')


def format_date(date: datetime) -> str:
    date = est.localize(date)
    return date.strftime("%a, %b %d %Y at %I:%M %p %Z")
