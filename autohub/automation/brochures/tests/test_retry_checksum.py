import time
import tempfile
from pathlib import Path
import requests

from autohub.automation.brochures.downloader.retry import with_retry, RetryError
from autohub.automation.brochures.checksum import (
    calculate_checksum,
    load_checksums,
    save_checksums,

)

#Test retry and Backoff logic

def test_with_retry_success():
    attempts = {"count": 0}

    def flaky_request():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.HTTPError("Temporary failure")
        # Simulate successful response
        response = requests.Response()
        response.status_code = 200
        response._content = b"PDF data"
        return response
    
    start = time.time()
    response = with_retry(flaky_request, max_retries=5, base_delay=0.1)
    elapsed = time.time() - start

    assert response.status_code == 200
    assert attempts["count"] == 3
    assert elapsed >= 0.1 + 0.2  # At least two delays

#Test retry exhaustion

def test_with_retry_exhaustion():
    def always_fail():
        raise requests.HTTPError("Permanent failure")
    
    try:
        with_retry(always_fail, max_retries=4, base_delay=0.1)
        assert False, "RetryError was not raised"
    except RetryError as exc:
        assert "Failed after 4 attempts" in str(exc)

#Test checksum calculation

def test_calculate_checksum():
    data = b"Sample PDF data"
    checksum1 = calculate_checksum(data)
    checksum2 = calculate_checksum(data)
    assert checksum1 == checksum2

#Skip Checksum duplicate
def test_checksum_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        checksum_file = tmp_path / "checksums.json"

        fake_checksum = {
            "test.pdf": calculate_checksum(b"PDF DATA")
        }

        save_checksums(fake_checksum)

        loaded = load_checksums()
        assert loaded["test.pdf"] == fake_checksum["test.pdf"]