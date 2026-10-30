import requests
from datetime import date, datetime


API_URL = "https://api.frankfurter.dev/v2/rates"
PROVIDER_NAME = "Frankfurter"


class CurrencyServiceError(Exception):
    """Raised when an exchange rate cannot be retrieved safely."""


def get_exchange_rate(base_currency, quote_currency):
    """
    Return a validated exchange rate.

    The rate means:
    1 unit of base_currency = rate units of quote_currency.

    Same-currency conversions return 1.0 without an API request.
    """

    base = base_currency.strip().upper()
    quote = quote_currency.strip().upper()

    if len(base) != 3 or len(quote) != 3:
        raise CurrencyServiceError(
            "Currency codes must contain three letters."
        )

    if not base.isalpha() or not quote.isalpha():
        raise CurrencyServiceError(
            "Currency codes must contain letters only."
        )

    if base == quote:
        return {
            "base": base,
            "quote": quote,
            "rate": 1.0,
            "date": date.today(),
            "provider": PROVIDER_NAME,
        }

    try:
        response = requests.get(
            API_URL,
            params={
                "base": base,
                "quotes": quote,
            },
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()

    except requests.RequestException as error:
        raise CurrencyServiceError(
            "Could not contact the exchange-rate provider. "
            "Please try again later."
        ) from error

    except ValueError as error:
        raise CurrencyServiceError(
            "The exchange-rate provider returned invalid data."
        ) from error

    if not isinstance(results, list) or not results:
        raise CurrencyServiceError(
            f"No exchange rate is available for {base} to {quote}."
        )

    # Use the latest dated rate returned by the provider.
    valid_results = []

    for item in results:
        if not isinstance(item, dict):
            continue

        if (
            item.get("base") != base
            or item.get("quote") != quote
        ):
            continue

        try:
            rate = float(item["rate"])
            rate_date = date.fromisoformat(item["date"])
        except (KeyError, TypeError, ValueError):
            continue

        if rate > 0 and rate < float("inf"):
            valid_results.append({
                "base": base,
                "quote": quote,
                "rate": rate,
                "date": rate_date,
                "provider": PROVIDER_NAME,
            })

    if not valid_results:
        raise CurrencyServiceError(
            f"No valid exchange rate was returned for "
            f"{base} to {quote}."
        )

    return max(valid_results, key=lambda item: item["date"])


def convert_amount(amount, base_currency, quote_currency):
    """Convert an amount and return both the result and rate details."""

    try:
        amount = float(amount)
    except (TypeError, ValueError) as error:
        raise CurrencyServiceError(
            "The amount must be a valid number."
        ) from error

    if amount < 0 or amount >= float("inf"):
        raise CurrencyServiceError(
            "The amount must be finite and non-negative."
        )

    rate_details = get_exchange_rate(
        base_currency,
        quote_currency,
    )

    converted_amount = amount * rate_details["rate"]

    return {
        **rate_details,
        "original_amount": amount,
        "converted_amount": round(converted_amount, 2),
    }