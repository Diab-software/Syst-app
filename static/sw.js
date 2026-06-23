self.addEventListener('push', function(event) {
    const data = event.data ? event.data.json() : {};
    const title = data.title || '💬 Syst';
    const options = {
        body: data.body || 'لديك رسالة جديدة',
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        vibrate: [200, 100, 200],
        data: {
            url: data.url || '/dashboard'
        },
        actions: [
            { action: 'open', title: '📨 فتح' },
            { action: 'dismiss', title: 'تجاهل' }
        ]
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    if (event.action === 'open' || !event.action) {
        const url = event.notification.data.url || '/dashboard';
        event.waitUntil(
            clients.matchAll({ type: 'window' }).then(function(clientList) {
                for (let i = 0; i < clientList.length; i++) {
                    const client = clientList[i];
                    if (client.url === url && 'focus' in client) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) {
                    return clients.openWindow(url);
                }
            })
        );
    }
});
