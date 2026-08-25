import logging

logger = logging.getLogger(__name__)

def merge(records):
    merged = {}
    try:
        for r in records:
            merged.update(r)
    except Exception as e:
        logger.warning('merge failed: %s', e)
    return merged
