import sqlite3

def get_user_data(username: str):
    # Intentional SQL Injection vulnerability for testing AgenticSCR
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # DANGER: String formatting directly into SQL query allows SQL Injection
    query = f"SELECT * FROM users WHERE username = '{username}'"
    
    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()
    
    return results

if __name__ == "__main__":
    # Example usage that could be exploited (e.g. "admin' OR '1'='1")
    malicious_input = "admin' OR '1'='1"
    print(get_user_data(malicious_input))
