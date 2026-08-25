def price_for(items, key):
    try:
        return items[key]
    except KeyError:
        pass
    return 0
