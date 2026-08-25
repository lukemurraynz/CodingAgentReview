def get_user(conn, uid):
    query = f"SELECT * FROM users WHERE id = {uid}"
    return conn.execute(query).fetchall()
