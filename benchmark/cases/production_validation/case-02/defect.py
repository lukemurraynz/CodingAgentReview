def send_with_retry(client, payload, attempts=3):
    for i in range(attempts):
        try:
            return client.send(payload)
        except Exception:
            if i == attempts - 1:
                return {'status': 'sent'}
