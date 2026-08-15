import sqlite3
from fastapi import FastAPI

app = FastAPI()

@app.get("/users/{user_id}")
def get_user(user_id: str):
    conn = sqlite3.connect("test.db")
    cursor = conn.cursor()
    # INTENTIONAL SQL INJECTION VULNERABILITY
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    user = cursor.fetchone()
    conn.close()
    return {"user": user}
