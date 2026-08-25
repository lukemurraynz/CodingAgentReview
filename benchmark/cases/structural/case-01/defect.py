class _NoopEventSubscriber:
    def handle(self, event): ...

def build_router():
    subscriber = _NoopEventSubscriber()
    return {'handler': subscriber.handle}
