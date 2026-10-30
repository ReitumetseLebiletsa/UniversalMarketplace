import requests

url = "https://api.frankfurter.dev/v2/rates"

try:
    response = requests.get(
        url,
        params={
            "base": "ZAR",
            "quotes": "LSL,USD"
        },
        timeout=15,
    )

    print("HTTP status:", response.status_code)
    response.raise_for_status()

    data = response.json()
    print("API response:", data)

except requests.RequestException as error:
    print("API request failed:", error)

except ValueError as error:
    print("Could not read the API response:", error)