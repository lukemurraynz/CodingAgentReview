from di import container
from workers.legacy import LegacyExporter


def configure(container=container):
    container.add_singleton(LegacyExporter, LegacyExporter)
