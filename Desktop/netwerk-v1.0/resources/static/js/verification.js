const verificationForm = document.getElementById("verificationForm");
const verificationCode = document.getElementById("verificationCode");
const resendButton = document.getElementById("resendButton");
const verificationMessage = document.getElementById("verificationMessage");
const verificationEmail = document.getElementById("verificationEmail");

function setVerificationMessage(message, isError = false) {
    verificationMessage.textContent = message;
    verificationMessage.classList.toggle("error", isError);
    verificationMessage.hidden = !message;
}

verificationCode.addEventListener("input", () => {
    verificationCode.value = verificationCode.value
        .replace(/\D/g, "")
        .slice(0, 6);
});

verificationCode.addEventListener("keydown", (event) => {
    if (
        event.key.length === 1 &&
        !/[0-9]/.test(event.key)
    ) {
        event.preventDefault();
    }
});

verificationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setVerificationMessage("");

    const code = verificationCode.value.trim();
    if (!/^\d{6}$/.test(code)) {
        setVerificationMessage("Enter a valid 6-digit code.", true);
        verificationCode.focus();
        return;
    }

    const button = verificationForm.querySelector(".verify-button");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Verifying...";

    try {
        const result = await verifyCode(code);

        if (result.data?.banned) {
            window.location.href = "/banned";
            return;
        }

        if (result.success) {
            button.textContent = "Verified!";
            window.location.href = "/";
            return;
        }

        setVerificationMessage(
            result.data?.message || result.message || "Invalid verification code.",
            true
        );
        verificationCode.focus();
        verificationCode.select();
    } finally {
        if (!button.textContent.startsWith("Verified")) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
});

resendButton.addEventListener("click", async () => {
    if (resendButton.disabled) {
        return;
    }

    const originalText = resendButton.textContent;
    resendButton.disabled = true;
    resendButton.textContent = "Sending...";
    setVerificationMessage("");

    try {
        const result = await resendVerification();

        if (result.data?.banned) {
            window.location.href = "/banned";
            return;
        }

        if (result.success) {
            setVerificationMessage("A new code was sent to your email.");
            return;
        }

        setVerificationMessage(
            result.data?.message ||
            result.message ||
            "Unable to send a new verification code.",
            true
        );
    } finally {
        resendButton.textContent = originalText;
        resendButton.disabled = false;
    }
});

getSession().then((result) => {
    if (result.data?.banned) {
        window.location.href = "/banned";
        return;
    }

    if (result.success && result.data.authenticated) {
        window.location.href = "/";
        return;
    }

    if (result.success && result.data.verification_required) {
        verificationEmail.textContent = result.data.verification_email || "";
        return;
    }

    if (!result.success || !result.data.verification_required) {
        window.location.href = "/";
    }
});

window.addEventListener("load", () => {
    verificationCode.focus();
});
