from datetime import datetime
def today_date() -> str:
    """ Function to fetch today's date
     Returns:
        A string of today's date
    """
    return datetime.today().strftime('%Y-%m-%d')