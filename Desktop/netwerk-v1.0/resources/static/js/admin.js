const userList = document.getElementById("userList");
const conversationList = document.getElementById("conversationList");
const conversationTitle = document.getElementById("conversationTitle");
const adminNotice = document.getElementById("adminNotice");
const userSearch = document.getElementById("userSearch");
const jsonSelect = document.getElementById("jsonSelect");
const jsonContents = document.getElementById("jsonContents");
const banForm = document.getElementById("banForm");
const banValue = document.getElementById("banValue");
const banKind = document.getElementById("banKind");
const banReason = document.getElementById("banReason");

let adminData = null;
let selectedUserId = null;

function showNotice(message, isError = false) {
    adminNotice.textContent = message;
    adminNotice.classList.toggle("error", isError);
    adminNotice.hidden = !message;
}

async function requestJson(url, options = {}) {
    try {
        const response = await fetch(url, {
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
    } catch {
        return {
            success: false,
            data: { message: "Unable to connect to the server." }
        };
    }
}

function createEmptyState(text) {
    const paragraph = document.createElement("p");
    paragraph.className = "muted";
    paragraph.textContent = text;
    return paragraph;
}

function renderSummary() {
    document.getElementById("userCount").textContent = adminData.users.length;
    document.getElementById("messageCount").textContent = adminData.messages.length;
    document.getElementById("loginCount").textContent = adminData.login_history.length;
    document.getElementById("banCount").textContent =
        adminData.banned.usernames.length + adminData.banned.ip_addresses.length;
    document.getElementById("lastUpdated").textContent =
        `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    jsonContents.textContent = JSON.stringify(adminData[jsonSelect.value], null, 2);
}

function renderUsers() {
    const query = userSearch.value.trim().toLocaleLowerCase();
    const users = adminData.users.filter((user) =>
        `${user.username} ${user.email}`.toLocaleLowerCase().includes(query)
    );
    if (users.length === 0) {
        userList.replaceChildren(createEmptyState("No users match your search."));
        return;
    }

    const banned = new Set(
        adminData.banned.usernames.map((entry) => entry.username.toLocaleLowerCase())
    );
    const fragment = document.createDocumentFragment();
    for (const user of users) {
        const row = document.createElement("div");
        row.className = "user-row";
        row.classList.toggle("selected", selectedUserId === user.id);

        const select = document.createElement("button");
        select.type = "button";
        select.className = "user-pick";
        const name = document.createElement("strong");
        name.textContent = user.username;
        const email = document.createElement("small");
        email.textContent = user.email;
        if (user.verified) {
            const verified = document.createElement("span");
            verified.className = "verified-label";
            verified.textContent = " · Verified";
            email.appendChild(verified);
        }
        select.append(name, email);
        select.addEventListener("click", () => loadConversation(user));

        const banButton = document.createElement("button");
        banButton.type = "button";
        banButton.className = "user-ban";
        banButton.textContent = banned.has(user.username.toLocaleLowerCase())
            ? "Banned"
            : "Ban";
        banButton.disabled = banned.has(user.username.toLocaleLowerCase()) ||
            user.username.toLocaleLowerCase() === "koutroufinis";
        banButton.setAttribute("aria-label", `Ban ${user.username}`);
        banButton.addEventListener("click", () => {
            banKind.value = "username";
            banValue.value = user.username;
            banReason.focus();
            document.querySelector(".bans-panel").scrollIntoView({
                behavior: "smooth",
                block: "center"
            });
        });

        row.append(select, banButton);
        fragment.appendChild(row);
    }
    userList.replaceChildren(fragment);
}

async function loadConversation(user) {
    selectedUserId = user.id;
    conversationTitle.textContent = user.username;
    conversationList.replaceChildren(createEmptyState("Loading conversation…"));
    renderUsers();

    const result = await requestJson(`/admin/conversations/${encodeURIComponent(user.id)}`);
    if (!result.success) {
        conversationList.replaceChildren(
            createEmptyState(result.data?.message || "Unable to load this conversation.")
        );
        return;
    }

    if (result.data.messages.length === 0) {
        conversationList.replaceChildren(createEmptyState("No direct messages with this user."));
        return;
    }

    const fragment = document.createDocumentFragment();
    for (const message of result.data.messages) {
        const article = document.createElement("article");
        article.className = "message-row";
        const metadata = document.createElement("div");
        metadata.className = "message-meta";
        const sender = document.createElement("strong");
        sender.textContent = message.sender;
        const direction = document.createElement("span");
        direction.textContent = "to";
        const recipient = document.createElement("strong");
        recipient.textContent = message.recipient;
        const timestamp = document.createElement("time");
        timestamp.dateTime = message.created_at;
        timestamp.textContent = new Date(message.created_at).toLocaleString();
        metadata.append(sender, direction, recipient, timestamp);
        const body = document.createElement("p");
        body.className = "message-body";
        body.textContent = message.text;
        article.append(metadata, body);
        fragment.appendChild(article);
    }
    conversationList.replaceChildren(fragment);
    conversationList.scrollTop = conversationList.scrollHeight;
}

function renderBans() {
    const entries = [
        ...adminData.banned.usernames.map((ban) => ({
            kind: "username",
            value: ban.username,
            reason: ban.reason || ""
        })),
        ...adminData.banned.ip_addresses.map((ban) => ({
            kind: "ip_address",
            value: ban.ip_address,
            reason: ban.reason || ""
        }))
    ];
    const banList = document.getElementById("banList");
    if (entries.length === 0) {
        banList.replaceChildren(createEmptyState("No active bans."));
        return;
    }

    const fragment = document.createDocumentFragment();
    for (const ban of entries) {
        const row = document.createElement("div");
        row.className = "ban-entry";
        const description = document.createElement("span");
        description.textContent =
            `${ban.kind === "username" ? "User" : "IP"}: ${ban.value}` +
            (ban.reason ? ` — ${ban.reason}` : "");
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "unban-button";
        remove.textContent = "Unban";
        remove.addEventListener("click", () => removeBan(ban));
        row.append(description, remove);
        fragment.appendChild(row);
    }
    banList.replaceChildren(fragment);
}

async function refreshData() {
    showNotice("");
    const result = await requestJson("/admin/data");
    if (!result.success) {
        showNotice(result.data?.message || "Unable to load control-panel data.", true);
        return;
    }

    adminData = result.data.data;
    renderSummary();
    renderUsers();
    renderBans();
    if (selectedUserId !== null) {
        const user = adminData.users.find((entry) => entry.id === selectedUserId);
        if (user) {
            await loadConversation(user);
        } else {
            selectedUserId = null;
            conversationTitle.textContent = "Select a user";
            conversationList.replaceChildren(createEmptyState("Choose a user to inspect their conversations."));
        }
    }
}

async function removeBan(ban) {
    const result = await requestJson("/admin/bans", {
        method: "DELETE",
        body: JSON.stringify({ kind: ban.kind, value: ban.value })
    });
    if (!result.success) {
        showNotice(result.data?.message || "Unable to remove this ban.", true);
        return;
    }
    adminData.banned = result.data.banned;
    renderSummary();
    renderUsers();
    renderBans();
    showNotice(`Ban removed for ${ban.value}.`);
}

banForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!banForm.reportValidity()) {
        return;
    }
    const submit = document.getElementById("banSubmit");
    submit.disabled = true;
    const result = await requestJson("/admin/bans", {
        method: "POST",
        body: JSON.stringify({
            kind: banKind.value,
            value: banValue.value,
            reason: banReason.value
        })
    });
    submit.disabled = false;
    if (!result.success) {
        showNotice(result.data?.message || "Unable to apply this ban.", true);
        return;
    }
    adminData.banned = result.data.banned;
    renderSummary();
    renderUsers();
    renderBans();
    showNotice(`Ban applied to ${banValue.value.trim()}.`);
    banForm.reset();
});

userSearch.addEventListener("input", renderUsers);
jsonSelect.addEventListener("change", () => {
    if (adminData) {
        jsonContents.textContent = JSON.stringify(adminData[jsonSelect.value], null, 2);
    }
});
document.getElementById("refreshButton").addEventListener("click", refreshData);
refreshData();
