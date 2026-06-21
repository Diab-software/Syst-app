console.log('✅ chat.js loaded');

function initChat() {
    console.log('🔄 ربط الأزرار...');
    
    const socket = window.socket;
    if (!socket) {
        alert('حدث خطأ في الاتصال، يرجى تحديث الصفحة');
        return;
    }

    const messageInput = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const fileInput = document.getElementById('file-input');
    const emojiToggle = document.getElementById('emoji-toggle');
    const emojiPicker = document.getElementById('emoji-picker');
    const recordBtn = document.getElementById('record-btn');
    const deleteChatBtn = document.getElementById('delete-chat-btn');
    const callBtn = document.getElementById('call-btn');
    const videoCallBtn = document.getElementById('video-call-btn');
    const fileUploadBtn = document.getElementById('file-upload-btn');
    const messagesContainer = document.getElementById('chat-messages');

    // 1. إرسال
    if (sendBtn) {
        sendBtn.onclick = function(e) {
            e.preventDefault();
            const content = messageInput.value.trim();
            if (!content) return;
            socket.emit('send_message', {
                message: content,
                private_with: window.privateWithId || null,
                group_id: window.groupId || null
            });
            messageInput.value = '';
            messageInput.focus();
        };
        console.log('✅ زر الإرسال مربوط');
    }

    if (messageInput) {
        messageInput.onkeypress = function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (sendBtn) sendBtn.click();
            }
        };
        let typingTimer;
        messageInput.oninput = function() {
            clearTimeout(typingTimer);
            if (socket && (window.privateWithId || window.groupId)) {
                socket.emit('typing', {
                    private_with: window.privateWithId || null,
                    group_id: window.groupId || null
                });
                typingTimer = setTimeout(() => {}, 1000);
            }
        };
    }

    // 2. رفع ملفات
    if (fileUploadBtn && fileInput) {
        fileUploadBtn.onclick = function() { fileInput.click(); };
        fileInput.onchange = function(e) {
            const files = e.target.files;
            if (files.length === 0) return;
            uploadFiles(files);
            fileInput.value = '';
        };
    }

    function uploadFiles(files) {
        const formData = new FormData();
        for (let f of files) formData.append('files', f);
        if (window.privateWithId) formData.append('private_with', window.privateWithId);
        if (window.groupId) formData.append('group_id', window.groupId);

        fetch('/upload', {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) { location.reload(); }
            else { alert(data.error || 'فشل الرفع'); }
        })
        .catch(err => alert('حدث خطأ أثناء الرفع'));
    }

    // 3. إيموجي
    if (emojiToggle && emojiPicker) {
        emojiToggle.onclick = function(e) {
            e.stopPropagation();
            emojiPicker.classList.toggle('show');
        };
        emojiPicker.querySelectorAll('.emoji-btn').forEach(btn => {
            btn.onclick = function() {
                messageInput.value += this.dataset.emoji;
                messageInput.focus();
                emojiPicker.classList.remove('show');
            };
        });
        document.onclick = function(e) {
            if (!emojiPicker.contains(e.target) && e.target !== emojiToggle) {
                emojiPicker.classList.remove('show');
            }
        };
    }

    // 4. تسجيل صوتي (مع رسالة أوضح)
    let mediaRecorder, audioChunks = [], isRecording = false;
    if (recordBtn) {
        recordBtn.onclick = function() {
            if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
        };
    }

    async function startRecording() {
        // تحقق من التوفر
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            alert('⚠️ لا يمكن الوصول للميكروفون.\nتأكد من:\n1. فتح الموقع على http://127.0.0.1:7070 (وليس 0.0.0.0)\n2. استخدام Chrome أو Firefox\n3. منح الإذن للميكروفون');
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
            mediaRecorder.onstop = () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                const file = new File([audioBlob], `audio_${Date.now()}.webm`, { type: 'audio/webm' });
                uploadFiles([file]);
                recordBtn.innerHTML = '<i class="bi bi-mic"></i>';
                recordBtn.classList.remove('btn-danger');
                isRecording = false;
                stream.getTracks().forEach(track => track.stop());
            };
            mediaRecorder.start();
            isRecording = true;
            recordBtn.innerHTML = '<i class="bi bi-stop-circle-fill"></i>';
            recordBtn.classList.add('btn-danger');
        } catch (err) {
            alert('❌ خطأ في الميكروفون: ' + err.message + '\nتأكد من منح الإذن.');
            console.error(err);
        }
    }

    function stopRecording() {
        if (mediaRecorder && isRecording) {
            mediaRecorder.stop();
        }
    }

    // 5. حذف المحادثة
    if (deleteChatBtn && window.privateWithId) {
        deleteChatBtn.onclick = function() {
            if (confirm('حذف كل رسائل هذه المحادثة؟')) {
                fetch(`/delete_private_chat/${window.privateWithId}`, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        alert('تم الحذف');
                        window.location.href = '/dashboard';
                    } else {
                        alert(data.error || 'فشل الحذف');
                    }
                });
            }
        };
    }

    // 6. المكالمات (رسالة مؤقتة)
    if (callBtn) {
        callBtn.onclick = function() { alert('📞 ميزة المكالمات الصوتية قيد التطوير'); };
    }
    if (videoCallBtn) {
        videoCallBtn.onclick = function() { alert('📹 ميزة مكالمات الفيديو قيد التطوير'); };
    }

    // تمرير للأسفل
    if (messagesContainer) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // دالة إضافة رسالة
    window.appendChatMessage = function(data) {
        const isOwn = (data.username === window.currentUsername);
        const div = document.createElement('div');
        div.className = `d-flex mb-3 ${isOwn ? 'justify-content-end' : 'justify-content-start'}`;
        div.innerHTML = `
            <div class="d-flex ${isOwn ? 'flex-row-reverse' : ''}" style="align-items: flex-end;">
                <div class="avatar me-2 ms-2">
                    <i class="bi ${isOwn ? 'bi-person-circle' : 'bi-person'}"></i>
                </div>
                <div>
                    <div class="message-bubble ${isOwn ? 'sent' : 'received'}">
                        ${data.content}
                        ${data.file_name ? `<br><small>📎 <a href="${data.file_url}" target="_blank">${data.file_name}</a></small>` : ''}
                        ${data.pinned ? '<span class="pinned-badge"><i class="bi bi-pin-fill"></i> مثبت</span>' : ''}
                    </div>
                    <div class="time-stamp text-muted text-end">${data.timestamp || ''}</div>
                </div>
            </div>
        `;
        messagesContainer.appendChild(div);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    };

    console.log('✅ جميع الأزرار جاهزة');
}

// تشغيل الدالة
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initChat);
} else {
    initChat();
}
