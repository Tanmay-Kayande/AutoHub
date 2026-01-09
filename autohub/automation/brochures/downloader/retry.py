import time
import random
import requests
from typing import Callable


class RetryError(Exception):
    """Raised when all retry attempts fail."""
    pass


def with_retry(
    func: Callable[[], requests.Response],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retry_on_status: tuple[int, ...] = (500, 502, 503, 504, 429),
) -> requests.Response:

    last_exception: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = func()

            if response.status_code in retry_on_status:
                raise requests.HTTPError(
                    f"Retryable status code: {response.status_code}"
                )

            return response

        except Exception as exc:
            last_exception = exc

            if attempt == max_retries:
                break

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += random.uniform(0, 0.3)
            time.sleep(delay)
        
    raise RetryError(
        f"Failed after {max_retries} attempts"
    ) from last_exception
