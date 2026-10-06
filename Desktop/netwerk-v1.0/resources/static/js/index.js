const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");
const showLogin = document.getElementById("showLogin");
const showSignup = document.getElementById("showSignup");
const subtitle = document.getElementById("subtitle");
const authBox = document.getElementById("authBox");
const dashboard = document.getElementById("dashboard");
const authMessage = document.getElementById("authMessage");
const dashboardMessage = document.getElementById("dashboardMessage");
const logoutButton = document.getElementById("logoutButton");

function setMessage(element, message, isError = false) {
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.hidden = !message;
}

function setBusy(form, labelText) {
    const button = form.querySelector(".auth-button");
    const label = button.querySelector("span");
    const originalText = label.textContent;

    button.disabled = true;
    label.textContent = labelText;

    return () => {
        button.disabled = false;
        label.textContent = originalText;
    };
}

function switchForm(form) {
    loginForm.classList.toggle("active", form === "login");
    signupForm.classList.toggle("active", form === "signup");
    subtitle.textContent = form === "login"
        ? "Welcome back"
        : "Create your account";
    setMessage(authMessage, "");
}

function showDashboard(user) {
    authBox.hidden = true;
    dashboard.hidden = false;
    subtitle.textContent = `Welcome back, ${user.username}`;
    document.getElementById("dashboardEmail").textContent = user.email;
}

function redirectIfBanned(result) {
    if (result.data?.banned) {
        window.location.href = "/banned";
        return true;
    }

    return false;
}

showSignup.addEventListener("click", () => switchForm("signup"));
showLogin.addEventListener("click", () => switchForm("login"));

loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage(authMessage, "");

    const username = document.getElementById("login_username").value.trim();
    const password = document.getElementById("login_password").value;
    const rememberMe = document.getElementById("remember_me").checked;
    const restoreButton = setBusy(loginForm, "Signing in...");

    try {
        const result = await login(username, password, rememberMe);

        if (redirectIfBanned(result)) {
            return;
        }

        if (result.success) {
            window.location.href = "/";
            return;
        }

        if (result.data?.verification_required) {
            window.location.href = "/verification";
            return;
        }

        setMessage(
            authMessage,
            result.data?.message || result.message || "Unable to sign in.",
            true
        );
    } finally {
        restoreButton();
    }
});

signupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setMessage(authMessage, "");

    const username = document.getElementById("signup_username").value.trim();
    const email = document.getElementById("signup_email").value.trim();
    const password = document.getElementById("signup_password").value;
    const confirmPassword = document.getElementById("confirm_password").value;

    if (password !== confirmPassword) {
        setMessage(authMessage, "Passwords do not match.", true);
        return;
    }

    const restoreButton = setBusy(signupForm, "Creating account...");

    try {
        const result = await signup(username, email, password, confirmPassword);

        if (redirectIfBanned(result)) {
            return;
        }

        if (!result.success) {
            setMessage(
                authMessage,
                result.data?.message || result.message || "Unable to create account.",
                true
            );
            return;
        }

        window.location.href = "/verification";
    } finally {
        restoreButton();
    }
});

logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    setMessage(dashboardMessage, "");

    const result = await logout();
    if (result.success) {
        window.location.href = "/";
        return;
    }

    logoutButton.disabled = false;
    setMessage(
        dashboardMessage,
        result.data?.message || result.message || "Unable to log out.",
        true
    );
});

getSession().then((result) => {
    if (redirectIfBanned(result)) {
        return;
    }

    if (!result.success) {
        setMessage(
            authMessage,
            result.data?.message || "Unable to connect to the server.",
            true
        );
        return;
    }

    if (result.data.authenticated) {
        showDashboard(result.data.user);
    } else if (result.data.verification_required) {
        window.location.href = "/verification";
    }
});
