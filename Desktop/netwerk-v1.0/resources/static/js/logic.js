const API_BASE = "";

localStorage.removeItem("netwerk_verification");

async function apiRequest(endpoint, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {})
            }
        });

        const data = await response.json();

        return {
            success: response.ok && data.success !== false,
            status: response.status,
            data
        };
    } catch (error) {
        return {
            success: false,
            status: 0,
            data: {
                message: "Unable to connect to the server."
            }
        };
    }
}

async function login(username, password, rememberMe = false) {
    if (!username || !password) {
        return {
            success: false,
            message: "Username and password are required."
        };
    }

    return await apiRequest("/login", {
        method: "POST",
        body: JSON.stringify({
            username,
            password,
            remember_me: rememberMe
        })
    });
}

async function signup(username, email, password, confirmPassword) {
    if (!username || !email || !password || !confirmPassword) {
        return {
            success: false,
            message: "All fields are required."
        };
    }

    if (password !== confirmPassword) {
        return {
            success: false,
            message: "Passwords do not match."
        };
    }

    return await apiRequest("/signup", {
        method: "POST",
        body: JSON.stringify({
            username,
            email,
            password,
            confirm_password: confirmPassword
        })
    });
}

async function getSession() {
    return await apiRequest("/session");
}

async function verifyCode(code) {
    if (!code || !/^\d{6}$/.test(code)) {
        return {
            success: false,
            message: "Enter a valid 6-digit verification code."
        };
    }

    return await apiRequest("/verify", {
        method: "POST",
        body: JSON.stringify({
            code
        })
    });
}

async function resendVerification() {
    return await apiRequest("/resend-verification", {
        method: "POST"
    });
}

async function logout() {
    return await apiRequest("/logout", {
        method: "POST"
    });
}
