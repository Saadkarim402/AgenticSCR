import sqlite3

def get_user_data(username):
    # Intentional SQL Injection vulnerability for AgenticSCR testing
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # DANGER: Directly concatenating user input into the SQL query
    query = f"SELECT * FROM users WHERE username = '{username}'"
    
    cursor.execute(query)
    result = cursor.fetchall()
    
    conn.close()
    return result

if __name__ == "__main__":
    # Example usage
    user_input = "admin' OR '1'='1"
    print(get_user_data(user_input))
