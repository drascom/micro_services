# LivAuto Scan

Hair Transplant Pre-Op Questionnaire Processing System

## Login

The application uses session-based authentication.

**Credentials:** Set in `.env` file
```
BASIC_AUTH_USERNAME=your_username
BASIC_AUTH_PASSWORD=your_password
```

**How to login:**
1. Open the web interface at `http://localhost:8000`
2. Enter your username and password in the login modal
3. Credentials are verified against `.env` file values
4. On success, you receive a session token (stored in browser)
5. Token is used for all subsequent requests

**Logout:** Click the logout button to invalidate your session token.

**Note:** Sessions are stored in memory and will reset when the server restarts.
