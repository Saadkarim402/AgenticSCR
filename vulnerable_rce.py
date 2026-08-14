def calculate_expression(expression: str):
    # Intentional Remote Code Execution (RCE) vulnerability for AgenticSCR testing
    
    # DANGER: Using eval() on raw user input allows arbitrary code execution
    result = eval(expression)
    
    return result

if __name__ == "__main__":
    # Example usage that could be exploited (e.g. "__import__('os').system('dir')")
    user_input = "2 + 2"
    print(calculate_expression(user_input))
