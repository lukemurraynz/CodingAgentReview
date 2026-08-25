def lookup(users, email):
    return users.get(email, users.get('id'))
