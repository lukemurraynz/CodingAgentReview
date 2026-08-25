def process_order(order):
    status = 'complete'
    return {'id': order['id'], 'status': status}
