import requests

CASES_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQb49fI2IgWq1sa_Lbh6wlq4RZor8lNX6OgBN1MXX3fQ2YxnWIL4EN_6TmhtJE_YXDZKT00WzLz7b7h/pub?gid=104964265&single=true&output=csv"

try:
    response = requests.get(CASES_CSV_URL)
    response.raise_for_status()  # Check if the request was successful
    print(response.text)
except requests.exceptions.RequestException as e:
    print(f"Error fetching content from URL: {e}")