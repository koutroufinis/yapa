const sidebar = document.getElementById("sidebar");
const overlay = document.getElementById("overlay");
const messageForm = document.getElementById("messageForm");
const messageInput = document.getElementById("messageInput");
const sendButton = messageForm.querySelector(".send-button");
const messages = document.getElementById("messages");
const emptyChat = document.getElementById("emptyChat");
const conversationList = document.getElementById("conversationList");
const conversationState = document.getElementById("conversationState");
const searchInput = document.getElementById("searchInput");
const chatNotice = document.getElementById("chatNotice");
const chatName = document.getElementById("chatName");
const chatAvatar = document.getElementById("chatAvatar");
const chatStatus = document.getElementById("chatStatus");
const chatPresenceLight = document.getElementById("chatPresenceLight");
const profileName = document.getElementById("profileName");
const profileAvatar = document.getElementById("profileAvatar");
const profilePresenceLight = document.getElementById("profilePresenceLight");
const profilePresenceText = document.getElementById("profilePresenceText");
const profileSettingsDialog = document.getElementById("profileSettingsDialog");
const profileSettingsForm = document.getElementById("profileSettingsForm");
const profileUsernameInput = document.getElementById("profileUsername");
const profilePhotoInput = document.getElementById("profilePhotoInput");
const profilePhotoPreview = document.getElementById("profilePhotoPreview");
const profileSettingsMessage = document.getElementById("profileSettingsMessage");
const saveProfileButton = document.getElementById("saveProfileButton");
const deleteAccountDialog = document.getElementById("deleteAccountDialog");
const deleteAccountForm = document.getElementById("deleteAccountForm");
const deleteAccountPassword = document.getElementById("deleteAccountPassword");
const deleteAccountMessage = document.getElementById("deleteAccountMessage");
const confirmDeleteAccount = document.getElementById("confirmDeleteAccount");
const mobileMenu = document.getElementById("mobileMenu");
const newChatButton = document.getElementById("newChatButton");
const logoutButton = document.getElementById("logoutButton");
const editProfileButton = document.getElementById("editProfileButton");
const adminPanelLink = document.getElementById("adminPanelLink");

let currentUser = null;
let selectedContact = null;
let lastMessageId = 0;
let lastMessageDate = null;
let contacts = [];
let pollInProgress = false;
let pollingContactId = null;
let conversationVersion = 0;
let redirected = false;
let typingActive = false;
let typingStopTimer = null;
let pendingProfilePhoto = null;
let removeProfilePhoto = false;

function showNotice(message, isError = true) {
    chatNotice.textContent = message;
    chatNotice.classList.toggle("error", isError);
    chatNotice.hidden = !message;
}

function handleAuthFailure(result) {
    if (redirected) {
        return true;
    }

    if (result.data?.banned) {
        redirected = true;
        window.location.href = "/banned";
        return true;
    }

    if (result.status === 401) {
        redirected = true;
        window.location.href = "/";
        return true;
    }

    return false;
}

function getInitial(name) {
    return name.trim().charAt(0).toUpperCase() || "?";
}

function formatTime(value) {
    return new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit"
    });
}

function formatDate(value) {
    const date = new Date(value);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    if (date.toDateString() === today.toDateString()) {
        return "Today";
    }
    if (date.toDateString() === yesterday.toDateString()) {
        return "Yesterday";
    }

    return date.toLocaleDateString([], {
        month: "long",
        day: "numeric",
        year: date.getFullYear() === today.getFullYear() ? undefined : "numeric"
    });
}

function dateKey(value) {
    const date = new Date(value);
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function closeMobileSidebar() {
    sidebar.classList.remove("open");
    overlay.classList.remove("active");
    mobileMenu.setAttribute("aria-expanded", "false");
}

function setPresenceLight(light, online) {
    light.classList.toggle("online", online);
    light.classList.toggle("offline", !online);
}

function applyAvatar(element, user) {
    const existingPhoto = element.querySelector(".avatar-photo");
    if (existingPhoto) {
        existingPhoto.remove();
    }
    element.textContent = getInitial(user.username || "");

    let photoUrl = user.photo_data_url || "";
    if (!photoUrl && user.has_profile_photo && user.id) {
        photoUrl = `/chat/profile-photo/${user.id}?v=${user.profile_photo_version || 0}`;
    }
    if (!photoUrl) {
        return;
    }

    const image = document.createElement("img");
    image.className = "avatar-photo";
    image.alt = "";
    image.src = photoUrl;
    image.addEventListener("error", () => {
        image.remove();
        element.textContent = getInitial(user.username || "");
    }, { once: true });
    element.appendChild(image);
}

function updateChatPresence(online, isTyping = false) {
    setPresenceLight(chatPresenceLight, online);
    chatAvatar.classList.toggle("online", online);
    chatAvatar.classList.toggle("offline", !online);
    chatStatus.classList.toggle("typing", isTyping);
    chatStatus.textContent = isTyping
        ? `${selectedContact.username} is typing`
        : online ? "Online" : "Offline";
}

function sendPresence(active, typing = false, keepalive = false) {
    const body = {
        active,
        typing: active && typing
    };
    if (body.typing && selectedContact) {
        body.recipient_id = selectedContact.id;
    }

    const options = {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(body),
        keepalive
    };
    return apiRequest("/chat/presence", options);
}

function stopTyping() {
    typingActive = false;
    if (typingStopTimer !== null) {
        window.clearTimeout(typingStopTimer);
        typingStopTimer = null;
    }
    if (!redirected) {
        void sendPresence(true, false);
    }
}

function handleMessageInput() {
    if (!selectedContact || !messageInput.value.trim()) {
        stopTyping();
        return;
    }

    if (!typingActive) {
        typingActive = true;
        void sendPresence(true, true);
    }
    if (typingStopTimer !== null) {
        window.clearTimeout(typingStopTimer);
    }
    typingStopTimer = window.setTimeout(stopTyping, 1500);
}

function renderContacts() {
    const filter = searchInput.value.trim().toLocaleLowerCase();
    const visibleContacts = contacts.filter((contact) =>
        contact.username.toLocaleLowerCase().includes(filter)
    );
    const fragment = document.createDocumentFragment();

    for (const contact of visibleContacts) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "conversation";
        button.classList.toggle(
            "active",
            selectedContact?.id === contact.id
        );
        button.setAttribute(
            "aria-label",
            `Open conversation with ${contact.username}, ${contact.online ? "online" : "offline"}`
        );

        const avatar = document.createElement("span");
        avatar.className = "avatar purple";
        avatar.classList.toggle("online", contact.online);
        avatar.classList.toggle("offline", !contact.online);
        applyAvatar(avatar, contact);

        const info = document.createElement("span");
        info.className = "conversation-info";

        const header = document.createElement("span");
        header.className = "conversation-header";

        const name = document.createElement("strong");
        name.textContent = contact.username;
        header.appendChild(name);

        if (contact.last_message_at) {
            const time = document.createElement("time");
            time.dateTime = contact.last_message_at;
            time.textContent = formatTime(contact.last_message_at);
            header.appendChild(time);
        }

        const preview = document.createElement("span");
        preview.className = "conversation-preview";
        preview.textContent = contact.last_message || "Start a conversation";
        info.append(header, preview);
        button.append(avatar, info);
        button.addEventListener("click", () => selectContact(contact));
        fragment.appendChild(button);
    }

    conversationList.replaceChildren();
    if (visibleContacts.length === 0) {
        conversationState.textContent = contacts.length === 0
            ? "No other verified users yet. Invite someone to join Netwerk."
            : "No people match your search.";
        conversationList.appendChild(conversationState);
    } else {
        conversationList.appendChild(fragment);
    }
}

function appendMessage(message) {
    const key = dateKey(message.created_at);
    if (key !== lastMessageDate) {
        const divider = document.createElement("div");
        divider.className = "date-divider";
        const label = document.createElement("span");
        label.textContent = formatDate(message.created_at);
        divider.appendChild(label);
        messages.appendChild(divider);
        lastMessageDate = key;
    }

    const wrapper = document.createElement("article");
    wrapper.className = `message ${message.sent ? "sent" : "received"}`;

    if (!message.sent) {
        const avatar = document.createElement("span");
        avatar.className = "message-avatar avatar purple";
        applyAvatar(avatar, selectedContact);
        wrapper.appendChild(avatar);
    }

    const content = document.createElement("div");
    content.className = "message-content";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    bubble.textContent = message.text;

    const time = document.createElement("time");
    time.dateTime = message.created_at;
    time.textContent = formatTime(message.created_at);
    content.append(bubble, time);
    wrapper.appendChild(content);
    messages.appendChild(wrapper);
}

function setComposerEnabled(enabled) {
    messageInput.disabled = !enabled;
    sendButton.disabled = !enabled;
}

async function loadMessages(initial = false) {
    if (!selectedContact || redirected) {
        return;
    }

    const contact = selectedContact;
    const version = conversationVersion;
    if (pollInProgress && pollingContactId === contact.id) {
        return;
    }
    pollInProgress = true;
    pollingContactId = contact.id;
    const wasNearBottom =
        messages.scrollHeight - messages.scrollTop - messages.clientHeight < 100;

    try {
        const query = new URLSearchParams({
            with: String(contact.id),
            after: initial ? "0" : String(lastMessageId)
        });
        const result = await apiRequest(`/chat/messages?${query}`);

        if (version !== conversationVersion) {
            return;
        }
        if (handleAuthFailure(result)) {
            return;
        }
        if (!result.success) {
            showNotice(result.data?.message || "Unable to load this conversation.");
            return;
        }

        showNotice("");
        updateChatPresence(result.data.online, result.data.is_typing);
        if (initial) {
            messages.replaceChildren();
            emptyChat.hidden = true;
            lastMessageId = 0;
            lastMessageDate = null;
        }

        for (const message of result.data.messages) {
            appendMessage(message);
            lastMessageId = Math.max(lastMessageId, message.id);
        }

        if (initial || wasNearBottom) {
            messages.scrollTop = messages.scrollHeight;
        }
    } finally {
        if (version === conversationVersion) {
            pollInProgress = false;
            pollingContactId = null;
        }
    }
}

async function selectContact(contact) {
    if (selectedContact) {
        stopTyping();
    }
    conversationVersion += 1;
    pollInProgress = false;
    pollingContactId = null;
    selectedContact = contact;
    lastMessageId = 0;
    lastMessageDate = null;
    chatName.textContent = contact.username;
    applyAvatar(chatAvatar, contact);
    updateChatPresence(contact.online, false);
    emptyChat.hidden = true;
    messages.replaceChildren();
    messages.appendChild(emptyChat);
    emptyChat.hidden = false;
    setComposerEnabled(true);
    renderContacts();
    closeMobileSidebar();
    showNotice("");
    await loadMessages(true);
    messageInput.focus();
}

async function refreshContacts() {
    const result = await apiRequest("/chat/users");
    if (handleAuthFailure(result)) {
        return;
    }
    if (!result.success) {
        conversationState.textContent =
            result.data?.message || "Unable to load people.";
        return;
    }

    contacts = result.data.users;
    if (
        selectedContact &&
        !contacts.some((contact) => contact.id === selectedContact.id)
    ) {
        selectedContact = null;
        setComposerEnabled(false);
        chatName.textContent = "Choose someone to chat with";
        applyAvatar(chatAvatar, { username: "Netwerk" });
        messages.replaceChildren(emptyChat);
        emptyChat.hidden = false;
    } else if (selectedContact) {
        selectedContact = contacts.find(
            (contact) => contact.id === selectedContact.id
        );
        chatName.textContent = selectedContact.username;
        applyAvatar(chatAvatar, selectedContact);
    }
    renderContacts();
}

messageForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = messageInput.value.trim();
    if (!text || !selectedContact || sendButton.disabled) {
        return;
    }

    const contact = selectedContact;
    const version = conversationVersion;
    stopTyping();
    sendButton.disabled = true;
    showNotice("");
    try {
        const result = await apiRequest("/chat/messages", {
            method: "POST",
            body: JSON.stringify({
                recipient_id: contact.id,
                text
            })
        });

        if (version !== conversationVersion) {
            return;
        }
        if (handleAuthFailure(result)) {
            return;
        }
        if (!result.success) {
            showNotice(result.data?.message || "Unable to send this message.");
            return;
        }

        messageInput.value = "";
        if (emptyChat.isConnected) {
            emptyChat.remove();
        }
        appendMessage(result.data.message);
        lastMessageId = Math.max(lastMessageId, result.data.message.id);
        messages.scrollTop = messages.scrollHeight;

        contact.last_message = text;
        contact.last_message_at = result.data.message.created_at;
        renderContacts();
    } finally {
        if (version === conversationVersion) {
            sendButton.disabled = !selectedContact;
            messageInput.focus();
        }
    }
});

messageInput.addEventListener("input", handleMessageInput);
searchInput.addEventListener("input", renderContacts);

function showSettingsMessage(element, message, isError = true) {
    element.textContent = message;
    element.classList.toggle("error", isError);
}

async function prepareProfilePhoto(file) {
    const supportedTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!supportedTypes.includes(file.type)) {
        throw new Error("Choose a PNG, JPEG, or WebP image.");
    }
    if (file.size > 10 * 1024 * 1024) {
        throw new Error("Choose an image smaller than 10 MB.");
    }

    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 512 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) {
        bitmap.close();
        throw new Error("Your browser could not process this image.");
    }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();

    const blob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/jpeg", 0.84);
    });
    if (!blob) {
        throw new Error("Your browser could not process this image.");
    }
    if (blob.size > 512_000) {
        throw new Error("This image could not be reduced below 512 KB.");
    }

    const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener("load", () => resolve(reader.result));
        reader.addEventListener("error", () => reject(new Error("Unable to read this image.")));
        reader.readAsDataURL(blob);
    });
    return dataUrl.split(",", 2)[1];
}

function openProfileSettings() {
    profileUsernameInput.value = currentUser.username;
    profilePhotoInput.value = "";
    pendingProfilePhoto = null;
    removeProfilePhoto = false;
    showSettingsMessage(profileSettingsMessage, "");
    applyAvatar(profilePhotoPreview, currentUser);
    profileSettingsDialog.showModal();
}

editProfileButton.addEventListener("click", openProfileSettings);
document.getElementById("closeProfileSettings").addEventListener("click", () => {
    profileSettingsDialog.close();
});
document.getElementById("cancelProfileSettings").addEventListener("click", () => {
    profileSettingsDialog.close();
});

profilePhotoInput.addEventListener("change", async () => {
    const file = profilePhotoInput.files?.[0];
    if (!file) {
        return;
    }
    saveProfileButton.disabled = true;
    showSettingsMessage(profileSettingsMessage, "Preparing photo...", false);
    try {
        pendingProfilePhoto = await prepareProfilePhoto(file);
        removeProfilePhoto = false;
        applyAvatar(profilePhotoPreview, {
            ...currentUser,
            photo_data_url: `data:image/jpeg;base64,${pendingProfilePhoto}`
        });
        showSettingsMessage(profileSettingsMessage, "Photo ready to save.", false);
    } catch (error) {
        pendingProfilePhoto = null;
        showSettingsMessage(profileSettingsMessage, error.message);
    } finally {
        saveProfileButton.disabled = false;
    }
});

document.getElementById("removeProfilePhoto").addEventListener("click", () => {
    pendingProfilePhoto = null;
    removeProfilePhoto = true;
    profilePhotoInput.value = "";
    applyAvatar(profilePhotoPreview, {
        ...currentUser,
        has_profile_photo: false
    });
    showSettingsMessage(profileSettingsMessage, "Photo will be removed when you save.", false);
});

profileSettingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!profileSettingsForm.reportValidity()) {
        return;
    }
    saveProfileButton.disabled = true;
    showSettingsMessage(profileSettingsMessage, "");
    const body = {
        username: profileUsernameInput.value,
        remove_photo: removeProfilePhoto
    };
    if (pendingProfilePhoto) {
        body.photo = pendingProfilePhoto;
    }
    const result = await apiRequest("/account/profile", {
        method: "POST",
        body: JSON.stringify(body)
    });
    if (handleAuthFailure(result)) {
        saveProfileButton.disabled = false;
        return;
    }
    if (!result.success) {
        showSettingsMessage(
            profileSettingsMessage,
            result.data?.message || "Unable to update your profile."
        );
        saveProfileButton.disabled = false;
        return;
    }

    currentUser = {
        ...currentUser,
        ...result.data.user
    };
    profileName.textContent = currentUser.username;
    applyAvatar(profileAvatar, currentUser);
    profileSettingsDialog.close();
    showNotice("Profile updated.", false);
    await refreshContacts();
    saveProfileButton.disabled = false;
});

document.getElementById("openDeleteAccount").addEventListener("click", () => {
    profileSettingsDialog.close();
    deleteAccountForm.reset();
    showSettingsMessage(deleteAccountMessage, "");
    deleteAccountDialog.showModal();
    deleteAccountPassword.focus();
});

document.getElementById("cancelDeleteAccount").addEventListener("click", () => {
    deleteAccountDialog.close();
});

deleteAccountForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!deleteAccountForm.reportValidity()) {
        return;
    }
    confirmDeleteAccount.disabled = true;
    showSettingsMessage(deleteAccountMessage, "");
    const result = await apiRequest("/account/delete", {
        method: "POST",
        body: JSON.stringify({
            password: deleteAccountPassword.value
        })
    });
    if (handleAuthFailure(result)) {
        return;
    }
    if (!result.success) {
        showSettingsMessage(
            deleteAccountMessage,
            result.data?.message || "Unable to delete your account."
        );
        confirmDeleteAccount.disabled = false;
        return;
    }

    window.location.href = "/";
});

newChatButton.addEventListener("click", () => {
    closeMobileSidebar();
    searchInput.focus();
    if (window.matchMedia("(max-width: 760px)").matches) {
        sidebar.classList.add("open");
        overlay.classList.add("active");
        mobileMenu.setAttribute("aria-expanded", "true");
        searchInput.focus();
    }
});

mobileMenu.addEventListener("click", () => {
    const isOpen = sidebar.classList.toggle("open");
    overlay.classList.toggle("active", isOpen);
    mobileMenu.setAttribute("aria-expanded", String(isOpen));
});

overlay.addEventListener("click", closeMobileSidebar);

logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    const result = await logout();
    if (handleAuthFailure(result)) {
        return;
    }
    if (result.success) {
        window.location.href = "/";
        return;
    }
    logoutButton.disabled = false;
    showNotice(result.data?.message || "Unable to log out.");
});

async function startChat() {
    const sessionResult = await getSession();
    if (handleAuthFailure(sessionResult)) {
        return;
    }
    if (!sessionResult.success || !sessionResult.data.authenticated) {
        window.location.href = "/";
        return;
    }

    currentUser = sessionResult.data.user;
    adminPanelLink.hidden = !currentUser.is_admin;
    profileName.textContent = currentUser.username;
    applyAvatar(profileAvatar, currentUser);
    setPresenceLight(profilePresenceLight, true);
    profilePresenceText.textContent = "Online";
    profilePresenceText.classList.remove("offline");
    void sendPresence(true);
    await refreshContacts();
    if (contacts.length > 0) {
        await selectContact(contacts[0]);
    }

    window.setInterval(() => {
        if (document.visibilityState === "visible") {
            loadMessages();
        }
    }, 2000);
    window.setInterval(() => {
        if (
            document.visibilityState === "visible" &&
            !redirected &&
            typingActive
        ) {
            void sendPresence(true, true);
        }
    }, 2000);
    window.setInterval(() => {
        if (document.visibilityState === "visible") {
            void sendPresence(true);
            refreshContacts();
        }
    }, 10000);
}

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
        typingActive = false;
        if (typingStopTimer !== null) {
            window.clearTimeout(typingStopTimer);
            typingStopTimer = null;
        }
        setPresenceLight(profilePresenceLight, false);
        profilePresenceText.textContent = "Offline";
        profilePresenceText.classList.add("offline");
        void sendPresence(false);
        if (selectedContact) {
            updateChatPresence(false, false);
        }
    } else if (currentUser && !redirected) {
        setPresenceLight(profilePresenceLight, true);
        profilePresenceText.textContent = "Online";
        profilePresenceText.classList.remove("offline");
        void sendPresence(true);
        if (selectedContact) {
            void loadMessages();
        }
    }
});

window.addEventListener("pagehide", () => {
    void sendPresence(false, false, true);
});

startChat();
