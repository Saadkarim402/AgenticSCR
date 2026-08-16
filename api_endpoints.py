import sqlite3
import os
from fastapi import FastAPI, Request

app = FastAPI()

# 1. SQL INJECTION VULNERABILITY
@app.get("/api/v1/user")
def get_user_profile(username: str):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    # Vulnerable: User input is directly concatenated into the SQL query
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    
    user_data = cursor.fetchone()
    conn.close()
    return {"user": user_data}

# 2. COMMAND INJECTION VULNERABILITY
@app.post("/api/v1/network/ping")
async def ping_server(request: Request):
    data = await request.json()
    target_ip = data.get("ip_address")
    
    # Vulnerable: User input is directly executed in the shell
    command = f"ping -c 4 {target_ip}"
    result = os.system(command)
    
    return {"status": "completed", "exit_code": result}

# 3. PATH TRAVERSAL VULNERABILITY
@app.get("/api/v1/download")
def download_file(filename: str):
    # Vulnerable: User can pass "../../etc/passwd" to read sensitive system files
    file_path = os.path.join("/var/www/html/downloads", filename)
    
    try:
        with open(file_path, "r") as f:
            content = f.read()
        return {"file_content": content}
    except Exception as e:
        return {"error": "File not found"}
