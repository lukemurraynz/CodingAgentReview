import logging

logger = logging.getLogger(__name__)

def get_recommendations(user_id, store=None):
    logger.info('recommendations requested for %s', user_id)
    if store is None:
        return []
    return store.for_user(user_id)
