def fetch(client, url, attempts=3):
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return client.get(url)
        except ConnectionError as exc:
            last = exc
    raise RuntimeError(f'fetch failed after {attempts} attempts') from last
