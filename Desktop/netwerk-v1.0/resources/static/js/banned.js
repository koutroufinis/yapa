const banReason = document.getElementById("banReason");
const banAction = document.getElementById("banAction");

async function refreshBanStatus() {
    banAction.disabled = true;
    banAction.textContent = "Checking ban status...";

    try {
        const response = await fetch("/ban-status", {
            headers: {
                Accept: "application/json"
            }
        });
        const result = await response.json();

        if (!response.ok || result.success !== true) {
            throw new Error(result.message || "Unable to check ban status.");
        }

        if (result.banned) {
            banReason.textContent = result.reason ||
                "If you believe this is a mistake, please contact support.";
            banAction.textContent = "Check ban status again";
            banAction.disabled = false;
            return;
        }

        banReason.textContent =
            "This ban is no longer active. You can return to the login page.";
        banAction.textContent = "Return to the login page";
        banAction.disabled = false;
        banAction.dataset.cleared = "true";
    } catch (error) {
        banReason.textContent = error.message;
        banAction.textContent = "Retry";
        banAction.disabled = false;
        banAction.dataset.cleared = "";
    }
}

banAction.addEventListener("click", async () => {
    if (banAction.dataset.cleared === "true") {
        window.location.href = "/";
        return;
    }

    banAction.dataset.cleared = "";
    await refreshBanStatus();
});

refreshBanStatus();