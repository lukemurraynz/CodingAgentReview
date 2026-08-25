class NoopMetricsClient:
    def increment(self, name): ...

def build_service(metrics=None):
    metrics = metrics or NoopMetricsClient()
    return Service(metrics)

class Service:
    def __init__(self, metrics): self.metrics = metrics
