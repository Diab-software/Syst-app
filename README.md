
## 📂 هيكل المشروع

```
Syst-app/
├── app.py                 # ملف التطبيق الرئيسي
├── requirements.txt       # المكتبات المطلوبة
├── Dockerfile             # بناء الصورة لنشر Render
├── Procfile               # أمر تشغيل التطبيق
├── static/
│   ├── style.css          # التصميم الرئيسي
│   ├── favicon.svg        # أيقونة المتصفح
│   ├── manifest.json      # إعدادات PWA
│   ├── sw.js              # Service Worker للإشعارات
│   └── *.png              # أيقونات بأحجام مختلفة
├── templates/
│   ├── base.html          # القالب الأساسي
│   ├── login.html         # صفحة تسجيل الدخول
│   ├── dashboard.html     # لوحة التحكم
│   ├── private_chat.html  # محادثة خاصة
│   ├── group_chat.html    # محادثة مجموعة
│   ├── group_settings.html # إعدادات المجموعة
│   ├── settings.html      # إعدادات المستخدم
│   ├── profile.html       # الملف الشخصي
│   ├── story_settings.html # إعدادات الستوري
│   ├── view_story.html    # عرض القصة
│   ├── call_log.html      # سجل المكالمات
│   ├── search.html        # البحث عن مستخدمين
│   └── notification_settings.html # إعدادات الإشعارات
├── uploads/               # الملفات المرفوعة
│   ├── profiles/          # صور الملفات الشخصية
│   ├── stories/           # وسائط القصص
│   ├── files/             # الملفات العامة
│   └── audio/             # التسجيلات الصوتية
└── instance/              # قاعدة البيانات (SQLite محلياً)
```
