import os
import json
import threading
from datetime import datetime
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit
from flask import request

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yapa_secret')

# SocketIO with eventlet async mode for Render
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Use /tmp for ephemeral file storage on Render
DATA_DIR = '/tmp/database'
DATA_FILE = os.path.join(DATA_DIR, 'data.json')
file_lock = threading.Lock()

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_data():
    ensure_data_dir()
    if not os.path.exists(DATA_FILE):
        with file_lock:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'users': {},
                    'global_messages': [],
                    'private_messages': []
                }, f, ensure_ascii=False, indent=2)
    with file_lock:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

def save_data(data):
    ensure_data_dir()
    temp_file = DATA_FILE + '.tmp'
    with file_lock:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, DATA_FILE)

active_sessions = {}
username_to_sid = {}

def get_client_ip():
    return request.remote_addr

def get_all_registered_users_with_status():
    db = load_data()
    users = db.get('users', {})
    result = []
    for username in users.keys():
        online = username in username_to_sid
        result.append({'username': username, 'online': online})
    return result

@socketio.on('join')
def handle_join(data):
    username = data.get('username', '').strip()
    if not username:
        emit('join_error', {'error': 'Username required'})
        return

    client_ip = get_client_ip()
    sid = request.sid

    db = load_data()
    users = db.get('users', {})

    if username in users:
        saved_ip = users[username]
        if saved_ip != client_ip:
            emit('join_error', {'error': f'Username "{username}" is locked to a different IP. Access denied.'})
            return

    if username in username_to_sid:
        emit('join_error', {'error': f'Username "{username}" is already online.'})
        return

    users[username] = client_ip
    db['users'] = users
    save_data(db)

    active_sessions[sid] = username
    username_to_sid[username] = sid

    emit('join_success', {'username': username})

    system_msg = {
        'type': 'system',
        'sender': 'System',
        'message': f'{username} joined the chat',
        'timestamp': datetime.now().isoformat()
    }
    db = load_data()
    db['global_messages'].append(system_msg)
    if len(db['global_messages']) > 200:
        db['global_messages'] = db['global_messages'][-200:]
    save_data(db)
    emit('new_message', system_msg, broadcast=True, include_self=False)

    global_history = db['global_messages'][-100:]
    private_history = [pm for pm in db['private_messages'] if pm['from'] == username or pm['to'] == username][-100:]
    emit('message_history', {'global': global_history, 'private': private_history})

    registered_users = get_all_registered_users_with_status()
    emit('registered_users', registered_users)

    online_users = list(active_sessions.values())
    emit('update_users', online_users, broadcast=True)

@socketio.on('message')
def handle_global_message(data):
    sid = request.sid
    if sid not in active_sessions:
        return
    username = active_sessions[sid]
    msg_text = data.get('message', '').strip()
    if not msg_text:
        return

    msg_obj = {
        'type': 'global',
        'sender': username,
        'message': msg_text,
        'timestamp': datetime.now().isoformat()
    }
    db = load_data()
    db['global_messages'].append(msg_obj)
    if len(db['global_messages']) > 200:
        db['global_messages'] = db['global_messages'][-200:]
    save_data(db)
    emit('new_message', msg_obj, broadcast=True)

@socketio.on('private')
def handle_private_message(data):
    sid = request.sid
    if sid not in active_sessions:
        return
    sender = active_sessions[sid]
    recipient = data.get('recipient', '').strip()
    msg_text = data.get('message', '').strip()
    if not recipient or not msg_text:
        emit('private_error', {'error': 'Recipient and message required'})
        return

    db = load_data()
    if recipient not in db.get('users', {}):
        emit('private_error', {'error': f'User {recipient} does not exist'})
        return

    msg_obj = {
        'type': 'private',
        'from': sender,
        'to': recipient,
        'message': msg_text,
        'timestamp': datetime.now().isoformat()
    }
    db['private_messages'].append(msg_obj)
    if len(db['private_messages']) > 1000:
        db['private_messages'] = db['private_messages'][-1000:]
    save_data(db)

    if recipient in username_to_sid:
        recipient_sid = username_to_sid[recipient]
        emit('private_message', msg_obj, to=recipient_sid)
    emit('private_message', msg_obj, to=sid)

@socketio.on('typing')
def handle_typing(data):
    sid = request.sid
    if sid not in active_sessions:
        return
    username = active_sessions[sid]
    target = data.get('target', 'global')
    is_typing = data.get('typing', False)

    if target == 'global':
        emit('user_typing', {'username': username, 'typing': is_typing}, broadcast=True, include_self=False)
    else:
        if target in username_to_sid:
            target_sid = username_to_sid[target]
            emit('user_typing', {'username': username, 'typing': is_typing, 'target': target}, to=target_sid)
            emit('user_typing', {'username': username, 'typing': is_typing, 'target': target}, to=sid)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in active_sessions:
        username = active_sessions[sid]
        del active_sessions[sid]
        if username in username_to_sid and username_to_sid[username] == sid:
            del username_to_sid[username]

        system_msg = {
            'type': 'system',
            'sender': 'System',
            'message': f'{username} left the chat',
            'timestamp': datetime.now().isoformat()
        }
        db = load_data()
        db['global_messages'].append(system_msg)
        if len(db['global_messages']) > 200:
            db['global_messages'] = db['global_messages'][-200:]
        save_data(db)
        emit('new_message', system_msg, broadcast=True)

        registered_users = get_all_registered_users_with_status()
        for s in active_sessions.values():
            emit('registered_users', registered_users, to=username_to_sid[s])

        online_users = list(active_sessions.values())
        emit('update_users', online_users, broadcast=True)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes, viewport-fit=cover">
    <title>Yapa</title>
    <link rel="icon" type="image/svg+xml" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="%233a86ff"/><text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-size="34" font-weight="700" fill="white" font-family="system-ui">Y</text></svg>'>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, sans-serif;
        }
        body {
            background: #060a0f;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 1rem;
        }
        .yapa-container {
            width: 100%;
            max-width: 1400px;
            height: 92vh;
            background: #0c1117;
            border-radius: 8px;
            box-shadow: 0 20px 35px rgba(0,0,0,0.6);
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .header {
            background: #070b10;
            padding: 0.9rem 2rem;
            border-bottom: 1px solid #1f3d5c;
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .logo-wrap {
            width: 36px;
            height: 36px;
            flex-shrink: 0;
        }
        .logo-icon {
            width: 100%;
            height: 100%;
            display: block;
        }
        .title {
            color: #cde5ff;
            font-size: 1.6rem;
            font-weight: 500;
            letter-spacing: 1px;
        }
        .login-card {
            background: #0e1620ee;
            border-radius: 8px;
            padding: 2.2rem;
            margin: auto;
            width: 90%;
            max-width: 380px;
            text-align: center;
            box-shadow: 0 12px 28px rgba(0,0,0,0.5);
            border: 1px solid #2a4c73;
        }
        .login-card h2 {
            color: #cce4ff;
            margin-bottom: 1.2rem;
            font-weight: 500;
        }
        .login-card input {
            width: 100%;
            padding: 12px 18px;
            margin: 12px 0;
            background: #101a24;
            border: 1px solid #2a4c73;
            border-radius: 8px;
            color: #e6f0ff;
            font-size: 1rem;
            outline: none;
            transition: 0.2s;
        }
        .login-card input:focus {
            border-color: #3a86ff;
            box-shadow: 0 0 6px #3a86ff66;
        }
        .login-card button {
            background: #1e4b7a;
            color: white;
            border: none;
            padding: 10px 28px;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
            transition: 0.2s;
        }
        .login-card button:hover {
            background: #2c64a0;
            transform: translateY(-1px);
        }
        .error {
            color: #ffb3b3;
            background: #4a1a1a;
            padding: 8px;
            border-radius: 8px;
            margin-top: 12px;
            font-size: 0.85rem;
            display: none;
        }
        .chat-layout {
            display: flex;
            flex: 1;
            overflow: hidden;
            flex-direction: row;
        }
        .sidebar {
            width: 280px;
            background: #080e14;
            border-right: 1px solid #1f3d5c;
            display: flex;
            flex-direction: column;
            padding: 1.2rem;
            gap: 1rem;
        }
        .online-header {
            color: #8abcec;
            font-weight: 500;
            padding-bottom: 8px;
            border-bottom: 1px solid #2a4c73;
        }
        .search-box {
            background: #0f1822;
            border: 1px solid #2a4c73;
            padding: 8px 14px;
            border-radius: 8px;
            color: #d9ecff;
            font-size: 0.85rem;
            outline: none;
            width: 100%;
            transition: 0.2s;
        }
        .search-box:focus {
            border-color: #3a86ff;
            box-shadow: 0 0 4px #3a86ff66;
        }
        .search-box::placeholder {
            color: #4a6f96;
        }
        .user-list {
            list-style: none;
            overflow-y: auto;
            flex: 1;
        }
        .user-list li {
            background: #0f1822;
            margin: 8px 0;
            padding: 8px 14px;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.9rem;
        }
        .user-list li.online {
            color: #cde2ff;
        }
        .user-list li.offline {
            color: #7e8d9c;
            opacity: 0.7;
        }
        .dm-btn {
            background: #1c4870;
            border: none;
            color: white;
            border-radius: 8px;
            padding: 6px 14px;
            cursor: pointer;
            font-size: 0.7rem;
            transition: background 0.2s;
        }
        .dm-btn:hover {
            background: #2c64a0;
        }
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #0c1117;
        }
        .messages-pane {
            flex: 1;
            overflow-y: auto;
            padding: 1.2rem;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .message {
            background: #101a24;
            border-radius: 8px;
            padding: 10px 16px;
            max-width: 85%;
            align-self: flex-start;
            color: #e6edf5;
            font-size: 0.9rem;
            word-break: break-word;
        }
        .message.global {
            border-left: 5px solid #3a86ff;
        }
        .message.private {
            border-left: 5px solid #f0a54a;
            background: #1e2a36;
        }
        .message.system {
            border-left: 5px solid #7e8d9c;
            font-style: italic;
            background: #141e28;
        }
        .msg-sender {
            font-weight: 600;
            color: #7ab3f0;
            margin-right: 8px;
        }
        .typing-area {
            color: #6b9ad0;
            font-size: 0.8rem;
            padding: 6px 18px;
            height: 34px;
            font-style: italic;
        }
        .input-bar {
            background: #070b10;
            padding: 1rem;
            display: flex;
            gap: 12px;
            border-top: 1px solid #1f3d5c;
        }
        #targetSelect {
            background: #101a24;
            color: white;
            border: 1px solid #2a4c73;
            padding: 10px 14px;
            border-radius: 8px;
            font-weight: 500;
            cursor: pointer;
            transition: 0.2s;
        }
        #targetSelect:hover {
            background: #1c2b3b;
        }
        #messageInput {
            flex: 3;
            background: #101a24;
            border: 1px solid #2a4c73;
            padding: 12px 18px;
            border-radius: 8px;
            color: white;
            outline: none;
            transition: 0.2s;
        }
        #messageInput:focus {
            border-color: #3a86ff;
        }
        #sendBtn {
            background: #1e4b7a;
            border: none;
            padding: 0 28px;
            border-radius: 8px;
            font-weight: 600;
            color: white;
            cursor: pointer;
            transition: background 0.2s;
        }
        #sendBtn:hover {
            background: #2c64a0;
        }
        @media (max-width: 680px) {
            .sidebar {
                width: 220px;
            }
            .title {
                font-size: 1.3rem;
            }
            .header {
                padding: 0.6rem 1rem;
            }
            .input-bar {
                gap: 6px;
            }
            .dm-btn {
                padding: 4px 10px;
            }
        }
        .hidden {
            display: none;
        }

        @media (max-width: 768px) {
            body {
                padding: 0;
            }
            .yapa-container {
                height: 100vh;
                border-radius: 0;
            }
            .chat-layout {
                flex-direction: column;
            }
            .sidebar {
                width: 100%;
                height: 90px;
                flex-direction: row;
                overflow-x: auto;
                overflow-y: hidden;
                padding: 0.5rem;
                gap: 8px;
            }
            .user-list {
                display: flex;
                flex-direction: row;
                gap: 8px;
            }
            .user-list li {
                min-width: 120px;
                font-size: 0.75rem;
                flex-direction: column;
                gap: 4px;
            }
            .chat-area {
                flex: 1;
            }
            .messages-pane {
                padding: 0.7rem;
            }
            .message {
                max-width: 95%;
                font-size: 0.85rem;
            }
            .input-bar {
                flex-direction: column;
                gap: 6px;
            }
            #messageInput {
                width: 100%;
                font-size: 1rem;
            }
            #sendBtn {
                width: 100%;
                padding: 12px;
            }
            #targetSelect {
                width: 100%;
            }
            .typing-area {
                font-size: 0.75rem;
                padding: 4px 10px;
            }
        }
    </style>
</head>
<body>
<div class="yapa-container">
    <div class="header">
        <div class="logo-wrap">
            <svg class="logo-icon" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
                <!-- Bubble -->
                <rect x="12" y="14" width="40" height="26" rx="10" fill="#3a86ff"/>
                <polygon points="24,40 30,40 26,50" fill="#3a86ff"/>
                <!-- Typing dots -->
                <circle cx="24" cy="27" r="2.5" fill="white">
                    <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite"/>
                </circle>
                <circle cx="32" cy="27" r="2.5" fill="white">
                    <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" begin="0.2s" repeatCount="indefinite"/>
                </circle>
                <circle cx="40" cy="27" r="2.5" fill="white">
                    <animate attributeName="opacity" values="1;0.3;1" dur="1.2s" begin="0.4s" repeatCount="indefinite"/>
                </circle>
            </svg>
        </div>
        <span class="title">Yapa</span>
    </div>
    <div id="loginScreen" class="login-card">
        <h2>Connect</h2>
        <input type="text" id="usernameInput" placeholder="username" maxlength="20">
        <button id="joinBtn">Join</button>
        <div id="loginError" class="error"></div>
    </div>
    <div id="chatInterface" class="chat-layout hidden">
        <div class="sidebar">
            <div class="online-header">Users · <span id="userCount">0</span></div>
            <input type="text" id="searchUsers" class="search-box" placeholder="search contact">
            <ul id="userList" class="user-list"></ul>
        </div>
        <div class="chat-area">
            <div id="messagesContainer" class="messages-pane"></div>
            <div id="typingIndicator" class="typing-area"></div>
            <div class="input-bar">
                <select id="targetSelect">
                    <option value="__global__">🌎 World chat</option>
                </select>
                <input type="text" id="messageInput" placeholder="Type message..." autocomplete="off">
                <button id="sendBtn">Send</button>
            </div>
        </div>
    </div>
</div>
<script>
    const socket = io();
    let currentUser = null;
    let typingTimer = null;
    let allUsers = [];  // { username, online }
    let allGlobalMessages = [];
    let allPrivateMessages = [];
    let currentChatTarget = "__global__";

    const loginScreen = document.getElementById('loginScreen');
    const chatInterface = document.getElementById('chatInterface');
    const loginError = document.getElementById('loginError');
    const usernameInput = document.getElementById('usernameInput');
    const joinBtn = document.getElementById('joinBtn');
    const messagesContainer = document.getElementById('messagesContainer');
    const userList = document.getElementById('userList');
    const userCountSpan = document.getElementById('userCount');
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const targetSelect = document.getElementById('targetSelect');
    const typingIndicator = document.getElementById('typingIndicator');
    const searchInput = document.getElementById('searchUsers');

    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/[&<>]/g, function(m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }

    function renderMessages() {
        messagesContainer.innerHTML = '';
        let messagesToShow = [];
        if (currentChatTarget === "__global__") {
            messagesToShow = allGlobalMessages;
        } else {
            messagesToShow = allPrivateMessages.filter(msg => 
                (msg.from === currentUser && msg.to === currentChatTarget) ||
                (msg.from === currentChatTarget && msg.to === currentUser)
            );
        }
        messagesToShow.forEach(msg => {
            const div = document.createElement('div');
            div.classList.add('message');
            let content = '';
            if (msg.type === 'global') {
                div.classList.add('global');
                content = `<span class="msg-sender">${escapeHtml(msg.sender)}</span> ${escapeHtml(msg.message)}`;
            } else if (msg.type === 'private') {
                div.classList.add('private');
                content = `<span class="msg-sender">${escapeHtml(msg.from)} → ${escapeHtml(msg.to)}</span> ${escapeHtml(msg.message)}`;
            } else if (msg.type === 'system') {
                div.classList.add('system');
                content = `${escapeHtml(msg.message)}`;
            } else {
                return;
            }
            div.innerHTML = content;
            messagesContainer.appendChild(div);
        });
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function renderUserList(filterText) {
        userList.innerHTML = '';
        let filtered = allUsers;
        if (filterText) {
            const lowerFilter = filterText.toLowerCase();
            filtered = allUsers.filter(u => u.username.toLowerCase().includes(lowerFilter));
        }
        filtered.forEach(user => {
            const li = document.createElement('li');
            li.className = user.online ? 'online' : 'offline';
            li.innerHTML = `<span>${escapeHtml(user.username)}${user.online ? ' ●' : ' ○'}</span><button class="dm-btn" data-user="${escapeHtml(user.username)}">dm</button>`;
            userList.appendChild(li);
        });
        document.querySelectorAll('.dm-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const user = btn.getAttribute('data-user');
                if (user && user !== currentUser) {
                    targetSelect.value = user;
                    switchChat(user);
                }
            });
        });
    }

    function switchChat(target) {
        currentChatTarget = target;
        renderMessages();
        if (target !== "__global__" && target !== currentUser) {
            typingIndicator.innerText = '';
        }
    }

    function updateUserList(users) {
        allUsers = users;
        userCountSpan.innerText = users.length;
        const previousTarget = targetSelect.value;
        targetSelect.innerHTML = '<option value="__global__">world chat</option>';
        users.forEach(user => {
            if (user.username !== currentUser) {
                const opt = document.createElement('option');
                opt.value = user.username;
                opt.textContent = `dm to ${user.username}${user.online ? ' (online)' : ' (offline)'}`;
                targetSelect.appendChild(opt);
            }
        });
        if (previousTarget !== "__global__" && users.some(u => u.username === previousTarget) && previousTarget !== currentUser) {
            targetSelect.value = previousTarget;
            if (currentChatTarget !== previousTarget) switchChat(previousTarget);
        } else if (currentChatTarget !== "__global__" && (!users.some(u => u.username === currentChatTarget) || currentChatTarget === currentUser)) {
            targetSelect.value = "__global__";
            switchChat("__global__");
        } else if (currentChatTarget === "__global__") {
            targetSelect.value = "__global__";
        }
        renderUserList(searchInput.value);
    }

    searchInput.addEventListener('input', function() {
        renderUserList(this.value);
    });

    targetSelect.addEventListener('change', function() {
        const newTarget = targetSelect.value;
        switchChat(newTarget);
    });

    socket.on('join_success', (data) => {
        currentUser = data.username;
        loginScreen.classList.add('hidden');
        chatInterface.classList.remove('hidden');
        loginError.innerText = '';
        loginError.style.display = 'none';
    });

    socket.on('join_error', (err) => {
        loginError.innerText = err.error;
        loginError.style.display = 'block';
    });

    socket.on('message_history', (history) => {
        allGlobalMessages = history.global || [];
        allPrivateMessages = history.private || [];
        renderMessages();
    });

    socket.on('registered_users', (users) => {
        updateUserList(users);
    });

    socket.on('new_message', (msg) => {
        if (msg.type === 'global' || msg.type === 'system') {
            allGlobalMessages.push(msg);
            if (currentChatTarget === "__global__") renderMessages();
        }
    });

    socket.on('private_message', (msg) => {
        allPrivateMessages.push(msg);
        if (currentChatTarget !== "__global__" && 
            ((msg.from === currentUser && msg.to === currentChatTarget) ||
             (msg.from === currentChatTarget && msg.to === currentUser))) {
            renderMessages();
        }
    });

    socket.on('update_users', (onlineUsers) => {
        const updatedUsers = allUsers.map(u => ({
            username: u.username,
            online: onlineUsers.includes(u.username)
        }));
        updateUserList(updatedUsers);
    });

    socket.on('user_typing', (data) => {
        const target = data.target || 'global';
        const isTargetingMe = (target !== 'global' && target === currentUser);
        if (data.typing) {
            if (target === 'global' && currentChatTarget === "__global__") {
                typingIndicator.innerText = `${escapeHtml(data.username)} is typing...`;
            } else if (isTargetingMe && currentChatTarget !== "__global__" && currentChatTarget === data.username) {
                typingIndicator.innerText = `${escapeHtml(data.username)} is typing a private message...`;
            }
        } else {
            if ((target === 'global' && typingIndicator.innerText.includes(data.username)) ||
                (isTargetingMe && typingIndicator.innerText.includes(data.username))) {
                typingIndicator.innerText = '';
            }
        }
        setTimeout(() => {
            if (typingIndicator.innerText !== '' && !typingIndicator.innerText.includes('...')) return;
            if (!data.typing) typingIndicator.innerText = '';
        }, 1200);
    });

    function emitTyping(typingState) {
        if (!currentUser) return;
        const target = targetSelect.value;
        socket.emit('typing', { typing: typingState, target: target === '__global__' ? 'global' : target });
    }

    messageInput.addEventListener('input', () => {
        if (!currentUser) return;
        emitTyping(true);
        if (typingTimer) clearTimeout(typingTimer);
        typingTimer = setTimeout(() => {
            emitTyping(false);
        }, 1000);
    });

    function sendMessage() {
        if (!currentUser) return;
        const text = messageInput.value.trim();
        if (text === '') return;
        const target = targetSelect.value;
        if (target === '__global__') {
            socket.emit('message', { message: text });
        } else {
            socket.emit('private', { recipient: target, message: text });
        }
        messageInput.value = '';
        messageInput.focus();
        if (typingTimer) clearTimeout(typingTimer);
        emitTyping(false);
    }

    sendBtn.addEventListener('click', sendMessage);
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    joinBtn.addEventListener('click', () => {
        const username = usernameInput.value.trim();
        if (username === '') {
            loginError.innerText = 'Username cannot be empty';
            loginError.style.display = 'block';
            return;
        }
        socket.emit('join', { username: username });
    });
</script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)