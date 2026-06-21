import os, base64, random, shutil
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet
import pyotp, qrcode
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'syst-ultimate-secret-key-change-in-production'

# ========== الإعدادات ==========
UPLOAD_FOLDER = 'uploads'
PROFILE_FOLDER = 'uploads/profiles'
STORY_FOLDER = 'uploads/stories'
ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','bmp','webp','mp4','webm','mov','avi','mkv','mp3','wav','ogg','pdf','txt','zip','rar','doc','docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

for folder in [UPLOAD_FOLDER, PROFILE_FOLDER, STORY_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'syst.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ========== التشفير ==========
key_file = os.path.join(app.instance_path, 'encryption.key')
if os.path.exists(key_file):
    with open(key_file, 'rb') as f: ENCRYPTION_KEY = f.read()
else:
    ENCRYPTION_KEY = Fernet.generate_key()
    with open(key_file, 'wb') as f: f.write(ENCRYPTION_KEY)
cipher = Fernet(ENCRYPTION_KEY)

def encrypt(t):
    return cipher.encrypt(t.encode()).decode() if t else t

def decrypt(t):
    try:
        return cipher.decrypt(t.encode()).decode() if t else t
    except:
        return "[مشفر]"

# ========== النماذج ==========
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False, default='')
    email = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(200), nullable=False)
    otp_secret = db.Column(db.String(32), nullable=True)
    profile_pic = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(100), nullable=True)
    last_seen = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    privacy_last_seen = db.Column(db.String(20), default='everyone')
    privacy_online = db.Column(db.String(20), default='everyone')
    is_online = db.Column(db.Boolean, default=False)
    blocked_users = db.Column(db.Text, default='')
    deleted = db.Column(db.Boolean, default=False)

    def is_blocked(self, user_id):
        if not self.blocked_users: return False
        return str(user_id) in self.blocked_users.split(',')

    def block_user(self, user_id):
        if not self.is_blocked(user_id):
            self.blocked_users = (self.blocked_users + f",{user_id}") if self.blocked_users else str(user_id)
            db.session.commit()
            return True
        return False

    def unblock_user(self, user_id):
        if self.is_blocked(user_id):
            ids = [i for i in self.blocked_users.split(',') if i != str(user_id)]
            self.blocked_users = ','.join(ids) if ids else ''
            db.session.commit()
            return True
        return False

    def get_blocked_list(self):
        return [int(i) for i in self.blocked_users.split(',') if i.isdigit()] if self.blocked_users else []

    def set_password(self, p):
        self.password_hash = generate_password_hash(p)
    def check_password(self, p):
        return check_password_hash(self.password_hash, p)
    def enable_2fa(self):
        self.otp_secret = pyotp.random_base32()
        return self.otp_secret
    def disable_2fa(self):
        self.otp_secret = None
    def verify_otp(self, code):
        if not self.otp_secret: return False
        return pyotp.TOTP(self.otp_secret).verify(code)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content_encrypted = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=True)
    private_with = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    file_name = db.Column(db.String(200), nullable=True)
    file_path = db.Column(db.String(300), nullable=True)
    file_type = db.Column(db.String(50), nullable=True)
    deleted_by = db.Column(db.Text, nullable=True)
    pinned = db.Column(db.Boolean, default=False)
    reply_to = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    replies = db.relationship('Message', backref='parent', remote_side=[id])
    user = db.relationship('User', foreign_keys=[user_id], backref='all_messages')
    private_user = db.relationship('User', foreign_keys=[private_with])

    @property
    def content(self):
        return decrypt(self.content_encrypted)
    @content.setter
    def content(self, plaintext):
        self.content_encrypted = encrypt(plaintext)

    def is_deleted_for(self, user_id):
        if not self.deleted_by: return False
        return str(user_id) in self.deleted_by.split(',')

    def delete_for_user(self, user_id):
        if not self.is_deleted_for(user_id):
            self.deleted_by = (self.deleted_by + f",{user_id}") if self.deleted_by else str(user_id)
            db.session.commit()

    def delete_for_all(self):
        db.session.delete(self)
        db.session.commit()

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class GroupMembership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    media_path = db.Column(db.String(300), nullable=True)
    media_type = db.Column(db.String(20), nullable=True)
    caption = db.Column(db.String(500), nullable=True)
    content_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc) + timedelta(hours=24))
    user = db.relationship('User', backref='stories')

class CallLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    call_type = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    end_time = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='initiated')

# ========== إنشاء الجداول ==========
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        a = User(username='admin', display_name='المدير', email='admin@example.com', status='مرحباً')
        a.set_password('admin123')
        db.session.add(a); db.session.commit()
        print('✅ admin/admin123')

# ========== دوال مساعدة ==========
def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def file_type(fn):
    ext = fn.rsplit('.',1)[1].lower()
    if ext in {'png','jpg','jpeg','gif','bmp','webp'}: return 'image'
    if ext in {'mp4','webm','mov','avi','mkv'}: return 'video'
    if ext in {'mp3','wav','ogg','webm','aac','flac'}: return 'audio'
    return 'document'

def get_user_contacts(user_id):
    user = User.query.get(user_id)
    if not user: return []
    blocked = user.get_blocked_list()
    contacts = db.session.query(User).join(Message, (Message.private_with == user.id) | (Message.user_id == user.id))\
        .filter(Message.private_with.isnot(None)).filter(User.id != user.id).filter(User.deleted == False).distinct().all()
    return [u for u in contacts if u.id not in blocked]

def format_last_seen(dt):
    if not dt: return "غير معروف"
    # التأكد من أن dt هو aware (له منطقة زمنية)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dt
    if diff.total_seconds() < 60: return "منذ لحظات"
    elif diff.total_seconds() < 3600:
        mins = int(diff.total_seconds() // 60)
        return f"منذ {mins} دقيقة"
    elif diff.total_seconds() < 86400:
        hours = int(diff.total_seconds() // 3600)
        return f"منذ {hours} ساعة"
    else:
        days = int(diff.total_seconds() // 86400)
        return f"منذ {days} يوم"
    return redirect(url_for('dashboard' if 'username' in session else 'login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']; p = request.form['password']
        user = User.query.filter_by(username=u, deleted=False).first()
        if user and user.check_password(p):
            session['pre_login_username'] = u
            if user.otp_secret: return redirect(url_for('verify_2fa'))
            session['username'] = u
            user.last_seen = datetime.now(timezone.utc)
            user.is_online = True
            db.session.commit()
            flash('تم الدخول', 'success')
            return redirect(url_for('dashboard'))
        flash('خطأ في الاسم أو كلمة المرور', 'danger')
    return render_template('login.html')

@app.route('/verify_2fa', methods=['GET','POST'])
def verify_2fa():
    u = session.get('pre_login_username')
    if not u: return redirect(url_for('login'))
    user = User.query.filter_by(username=u, deleted=False).first()
    if not user: return redirect(url_for('login'))
    if request.method == 'POST':
        code = request.form.get('otp','').strip()
        if user.verify_otp(code):
            session['username'] = u
            session.pop('pre_login_username', None)
            user.last_seen = datetime.now(timezone.utc)
            user.is_online = True
            db.session.commit()
            flash('تم الدخول', 'success')
            return redirect(url_for('dashboard'))
        flash('رمز غير صحيح', 'danger')
    return render_template('verify_2fa.html', username=u)

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        display_name = request.form.get('display_name','').strip()
        email = request.form.get('email','').strip()
        password = request.form.get('password','').strip()
        confirm = request.form.get('confirm_password','').strip()
        enable_2fa = request.form.get('enable_2fa') == 'on'
        if len(username)<3: flash('اسم المستخدم 3 أحرف على الأقل','danger'); return render_template('signup.html')
        if not display_name: flash('الاسم المعروض مطلوب','danger'); return render_template('signup.html')
        if not email: flash('البريد مطلوب','danger'); return render_template('signup.html')
        if len(password)<6: flash('كلمة المرور 6 أحرف','danger'); return render_template('signup.html')
        if password != confirm: flash('كلمتا المرور غير متطابقتين','danger'); return render_template('signup.html')
        if User.query.filter_by(username=username).first(): flash('اسم المستخدم موجود','danger'); return render_template('signup.html')
        session['signup_temp'] = {'username':username,'display_name':display_name,'email':email,'password':password}
        if enable_2fa: return redirect(url_for('signup_2fa'))
        new_user = User(username=username, display_name=display_name, email=email)
        new_user.set_password(password)
        db.session.add(new_user); db.session.commit()
        session.pop('signup_temp', None)
        flash('تم التسجيل، يمكنك الدخول','success')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/signup-2fa', methods=['GET','POST'])
def signup_2fa():
    temp = session.get('signup_temp')
    if not temp: return redirect(url_for('signup'))
    secret = pyotp.random_base32()
    if request.method == 'POST':
        code = request.form.get('otp','').strip()
        if pyotp.TOTP(secret).verify(code):
            u = User(username=temp['username'], display_name=temp['display_name'], email=temp['email'], otp_secret=secret)
            u.set_password(temp['password'])
            db.session.add(u); db.session.commit()
            session.pop('signup_temp', None)
            flash('تم التسجيل مع 2FA','success')
            return redirect(url_for('login'))
        flash('رمز غير صحيح','danger')
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=temp['username'], issuer_name="Syst")
    qr = qrcode.make(uri)
    buffered = BytesIO()
    qr.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode()
    return render_template('signup_2fa.html', username=temp['username'], display_name=temp['display_name'], secret=secret, qr_base64=qr_base64)

@app.route('/logout')
def logout():
    user = User.query.filter_by(username=session.get('username')).first()
    if user:
        user.is_online = False
        user.last_seen = datetime.now(timezone.utc)
        db.session.commit()
    session.clear()
    flash('تم الخروج','info')
    return redirect(url_for('login'))

@app.route('/delete_account', methods=['POST'])
def delete_account():
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'المستخدم غير موجود'}), 404
    for folder in [PROFILE_FOLDER, STORY_FOLDER, UPLOAD_FOLDER]:
        for f in os.listdir(folder):
            if f.startswith(f"profile_{user.id}_") or f.startswith(f"story_{user.id}_"):
                try: os.remove(os.path.join(folder, f))
                except: pass
    messages = Message.query.filter((Message.user_id == user.id) | (Message.private_with == user.id)).all()
    for msg in messages: msg.delete_for_all()
    Story.query.filter_by(user_id=user.id).delete()
    GroupMembership.query.filter_by(user_id=user.id).delete()
    user.deleted = True
    db.session.commit()
    session.clear()
    return jsonify({'success': True, 'message': 'تم حذف الحساب'})

@app.route('/delete_private_chat/<int:other_id>', methods=['POST'])
def delete_private_chat(other_id):
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'مستخدم غير موجود'}), 404
    messages = Message.query.filter(
        ((Message.user_id == user.id) & (Message.private_with == other_id)) |
        ((Message.user_id == other_id) & (Message.private_with == user.id))
    ).all()
    for msg in messages: msg.delete_for_all()
    return jsonify({'success': True, 'message': 'تم تدمير المحادثة'})

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        u = request.form.get('username','').strip()
        if not u: flash('أدخل اسم المستخدم','danger'); return render_template('forgot_password.html')
        user = User.query.filter_by(username=u, deleted=False).first()
        if not user: flash('المستخدم غير موجود','danger'); return render_template('forgot_password.html')
        code = str(random.randint(100000,999999))
        session['reset_code'] = code; session['reset_username'] = u; session['reset_code_time'] = datetime.now(timezone.utc).timestamp()
        print(f'🔑 كود إعادة التعيين لـ {u}: {code}')
        flash(f'الكود: {code} (ظهر في المحطة)','warning')
        return redirect(url_for('verify_reset_code'))
    return render_template('forgot_password.html')

@app.route('/verify-reset-code', methods=['GET','POST'])
def verify_reset_code():
    u = session.get('reset_username')
    if not u: return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        code = request.form.get('code','').strip()
        stored = session.get('reset_code')
        if (datetime.now(timezone.utc).timestamp() - session.get('reset_code_time',0)) > 600:
            flash('انتهت صلاحية الكود','danger')
            session.pop('reset_code',None); session.pop('reset_username',None); session.pop('reset_code_time',None)
            return redirect(url_for('forgot_password'))
        if code == stored:
            flash('تم التحقق','success')
            session.pop('reset_code',None); session.pop('reset_code_time',None)
            return redirect(url_for('reset_password'))
        flash('كود غير صحيح','danger')
    return render_template('verify_reset_code.html', username=u)

@app.route('/reset-password', methods=['GET','POST'])
def reset_password():
    u = session.get('reset_username')
    if not u: return redirect(url_for('forgot_password'))
    user = User.query.filter_by(username=u, deleted=False).first()
    if not user: return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        p1 = request.form.get('password','').strip()
        p2 = request.form.get('confirm_password','').strip()
        if len(p1)<6: flash('6 أحرف على الأقل','danger'); return render_template('reset_password.html', username=u)
        if p1 != p2: flash('غير متطابقة','danger'); return render_template('reset_password.html', username=u)
        user.set_password(p1); db.session.commit()
        session.pop('reset_username', None)
        flash('تم إعادة التعيين','success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', username=u)

@app.route('/dashboard')
def dashboard():
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    private_users = get_user_contacts(user.id)
    groups = Group.query.join(GroupMembership).filter(GroupMembership.user_id == user.id).all()
    contacts_ids = [u.id for u in private_users]
    stories = Story.query.filter(Story.user_id.in_(contacts_ids), Story.expires_at > datetime.now(timezone.utc)).order_by(Story.created_at.desc()).all()
    return render_template('dashboard.html', username=session['username'], display_name=user.display_name,
                           private_users=private_users, groups=groups, stories=stories, user=user)

@app.route('/chat/private/<int:user_id>')
def private_chat(user_id):
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    other = db.session.get(User, user_id)
    if not other or other.deleted: flash('المستخدم غير موجود','danger'); return redirect(url_for('dashboard'))
    if user.is_blocked(user_id) or other.is_blocked(user.id):
        flash('لا يمكنك التواصل مع هذا المستخدم (محظور)', 'danger')
        return redirect(url_for('dashboard'))
    messages = Message.query.filter(
        ((Message.user_id == user.id) & (Message.private_with == other.id)) |
        ((Message.user_id == other.id) & (Message.private_with == user.id))
    ).order_by(Message.timestamp.asc()).all()
    messages = [m for m in messages if not m.is_deleted_for(user.id)]
    pinned_msgs = [m for m in messages if m.pinned]
    normal_msgs = [m for m in messages if not m.pinned]
    messages = pinned_msgs + normal_msgs
    return render_template('private_chat.html', username=session['username'], display_name=user.display_name,
                           other=other, messages=messages, user=user)

@app.route('/chat/group/<int:group_id>')
def group_chat(group_id):
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    group = db.session.get(Group, group_id)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('dashboard'))
    if not GroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first():
        flash('لست عضواً','danger'); return redirect(url_for('dashboard'))
    messages = Message.query.filter(Message.group_id == group_id).order_by(Message.timestamp.asc()).all()
    messages = [m for m in messages if not m.is_deleted_for(user.id)]
    pinned_msgs = [m for m in messages if m.pinned]
    normal_msgs = [m for m in messages if not m.pinned]
    messages = pinned_msgs + normal_msgs
    return render_template('group_chat.html', username=session['username'], display_name=user.display_name,
                           messages=messages, group=group, user=user)

@app.route('/create_group', methods=['POST'])
def create_group():
    if 'username' not in session: return redirect(url_for('login'))
    name = request.form.get('name','').strip()
    if not name: flash('اسم المجموعة مطلوب','danger'); return redirect(url_for('settings'))
    user = User.query.filter_by(username=session['username']).first()
    if not user: return redirect(url_for('login'))
    g = Group(name=name, created_by=user.id); db.session.add(g); db.session.commit()
    db.session.add(GroupMembership(user_id=user.id, group_id=g.id, is_admin=True)); db.session.commit()
    flash('تم إنشاء المجموعة','success'); return redirect(url_for('settings'))

@app.route('/join_group', methods=['POST'])
def join_group():
    if 'username' not in session: return redirect(url_for('login'))
    gid = request.form.get('group_id', type=int)
    if not gid: flash('معرف المجموعة مطلوب','danger'); return redirect(url_for('settings'))
    user = User.query.filter_by(username=session['username']).first()
    if not user: return redirect(url_for('login'))
    group = db.session.get(Group, gid)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('settings'))
    if GroupMembership.query.filter_by(user_id=user.id, group_id=gid).first():
        flash('أنت عضو بالفعل','info'); return redirect(url_for('settings'))
    db.session.add(GroupMembership(user_id=user.id, group_id=gid)); db.session.commit()
    flash('تم الانضمام','success'); return redirect(url_for('settings'))

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    private_with = request.form.get('private_with', type=int)
    group_id = request.form.get('group_id', type=int)
    if 'files' not in request.files:
        flash('لا يوجد ملفات','danger')
        if private_with: return redirect(url_for('private_chat', user_id=private_with))
        elif group_id: return redirect(url_for('group_chat', group_id=group_id))
        return redirect(url_for('dashboard'))
    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        flash('لم يتم اختيار ملفات','danger')
        if private_with: return redirect(url_for('private_chat', user_id=private_with))
        elif group_id: return redirect(url_for('group_chat', group_id=group_id))
        return redirect(url_for('dashboard'))
    uploaded = 0
    for file in files:
        if not allowed_file(file.filename): continue
        filename = secure_filename(file.filename)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(file_path)
        file_url = url_for('download_file', filename=unique_filename)
        ftype = file_type(filename)
        if ftype == 'image': msg_content = f"🖼️ صورة: {filename}"
        elif ftype == 'audio': msg_content = f"🎵 مقطع صوتي: {filename}"
        elif ftype == 'video': msg_content = f"🎬 فيديو: {filename}"
        else: msg_content = f"📎 ملف: {filename} (حجم: {os.path.getsize(file_path)//1024} كيلوبايت)"
        msg = Message(content=msg_content, user_id=user.id, file_name=filename, file_path=file_path, file_type=ftype,
                      private_with=private_with, group_id=group_id)
        db.session.add(msg); db.session.commit()
        uploaded += 1
        room = None
        if private_with: room = f"private_{min(user.id, private_with)}_{max(user.id, private_with)}"
        elif group_id: room = f"group_{group_id}"
        socketio.emit('new_message', {
            'username': user.username, 'display_name': user.display_name, 'content': msg.content,
            'timestamp': msg.timestamp.strftime('%H:%M'), 'file_name': filename, 'file_url': file_url,
            'file_type': ftype, 'private_with': private_with, 'group_id': group_id, 'message_id': msg.id
        }, namespace='/', room=room)
    if uploaded > 0: flash(f'تم رفع {uploaded} ملفات', 'success')
    else: flash('لم يتم رفع أي ملف (أنواع غير مدعومة)', 'danger')
    if private_with: return redirect(url_for('private_chat', user_id=private_with))
    elif group_id: return redirect(url_for('group_chat', group_id=group_id))
    return redirect(url_for('dashboard'))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/upload_profile_pic', methods=['POST'])
def upload_profile_pic():
    if 'username' not in session:
        flash('يجب تسجيل الدخول', 'danger')
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted:
        flash('المستخدم غير موجود', 'danger')
        return redirect(url_for('login'))
    if 'profile_pic' not in request.files:
        flash('لا توجد صورة', 'danger')
        return redirect(url_for('settings'))
    file = request.files['profile_pic']
    if file.filename == '':
        flash('لم يتم اختيار صورة', 'danger')
        return redirect(url_for('settings'))
    if not allowed_file(file.filename):
        flash('نوع الملف غير مسموح', 'danger')
        return redirect(url_for('settings'))
    if user.profile_pic:
        old_path = os.path.join(PROFILE_FOLDER, user.profile_pic)
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except: pass
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    unique_filename = f"profile_{user.id}_{timestamp}_{filename}"
    file_path = os.path.join(PROFILE_FOLDER, unique_filename)
    file.save(file_path)
    user.profile_pic = unique_filename
    db.session.commit()
    flash('✅ تم تحديث الصورة الشخصية', 'success')
    return redirect(url_for('settings'))

@app.route('/upload_story', methods=['POST'])
def upload_story():
    if 'username' not in session:
        flash('يجب تسجيل الدخول', 'danger')
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted:
        flash('المستخدم غير موجود', 'danger')
        return redirect(url_for('login'))
    story_type = request.form.get('story_type', 'media')
    caption = request.form.get('caption', '').strip()
    content_text = request.form.get('content_text', '').strip()
    if story_type == 'text':
        if not content_text:
            flash('يرجى إدخال نص القصة', 'danger')
            return redirect(url_for('dashboard'))
        story = Story(user_id=user.id, media_type='text', caption=caption, content_text=content_text)
        db.session.add(story)
        db.session.commit()
        flash('تم نشر القصة النصية', 'success')
        return redirect(url_for('dashboard'))
    if 'story_media' not in request.files:
        flash('لا يوجد ملف', 'danger')
        return redirect(url_for('dashboard'))
    file = request.files['story_media']
    if file.filename == '':
        flash('لم يتم اختيار ملف', 'danger')
        return redirect(url_for('dashboard'))
    if not allowed_file(file.filename):
        flash('نوع الملف غير مسموح', 'danger')
        return redirect(url_for('dashboard'))
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    unique_filename = f"story_{user.id}_{timestamp}_{filename}"
    file_path = os.path.join(STORY_FOLDER, unique_filename)
    file.save(file_path)
    media_type = file_type(filename)
    story = Story(user_id=user.id, media_path=unique_filename, media_type=media_type, caption=caption)
    db.session.add(story)
    db.session.commit()
    flash('✅ تم نشر القصة', 'success')
    return redirect(url_for('dashboard'))

@app.route('/none')
def none_image():
    from flask import send_file
    from io import BytesIO
    img = BytesIO()
    img.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\x87\x1bU\xd2\x00\x00\x00\x00IEND\xaeB`\x82')
    img.seek(0)
    return send_file(img, mimetype='image/png')

@app.route('/profiles/<filename>')
def download_profile_pic(filename):
    return send_from_directory(PROFILE_FOLDER, filename)

@app.route('/stories/<filename>')
def download_story(filename):
    return send_from_directory(STORY_FOLDER, filename)

@app.route('/delete_message/<int:message_id>', methods=['POST'])
def delete_message(message_id):
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'مستخدم غير موجود'}), 404
    msg = Message.query.get(message_id)
    if not msg: return jsonify({'error': 'الرسالة غير موجودة'}), 404
    if msg.user_id == user.id or (msg.private_with == user.id) or (msg.group_id and GroupMembership.query.filter_by(user_id=user.id, group_id=msg.group_id).first()):
        msg.delete_for_user(user.id)
        return jsonify({'success': True})
    return jsonify({'error': 'غير مصرح'}), 403

@app.route('/pin_message/<int:message_id>', methods=['POST'])
def pin_message(message_id):
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'مستخدم غير موجود'}), 404
    msg = Message.query.get(message_id)
    if not msg: return jsonify({'error': 'الرسالة غير موجودة'}), 404
    if msg.user_id == user.id or (msg.private_with == user.id) or (msg.group_id and GroupMembership.query.filter_by(user_id=user.id, group_id=msg.group_id).first()):
        msg.pinned = not msg.pinned
        db.session.commit()
        return jsonify({'success': True, 'pinned': msg.pinned})
    return jsonify({'error': 'غير مصرح'}), 403

@app.route('/block_user/<int:user_id>', methods=['POST'])
def block_user(user_id):
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'مستخدم غير موجود'}), 404
    target = User.query.get(user_id)
    if not target: return jsonify({'error': 'المستخدم المستهدف غير موجود'}), 404
    if user.block_user(user_id):
        return jsonify({'success': True, 'message': f'تم حظر {target.display_name}'})
    return jsonify({'error': 'المستخدم محظور بالفعل'}), 400

@app.route('/unblock_user/<int:user_id>', methods=['POST'])
def unblock_user(user_id):
    if 'username' not in session: return jsonify({'error': 'غير مسجل'}), 401
    user = User.query.filter_by(username=session['username']).first()
    if not user: return jsonify({'error': 'مستخدم غير موجود'}), 404
    target = User.query.get(user_id)
    if not target: return jsonify({'error': 'المستخدم المستهدف غير موجود'}), 404
    if user.unblock_user(user_id):
        return jsonify({'success': True, 'message': f'تم إلغاء حظر {target.display_name}'})
    return jsonify({'error': 'المستخدم غير محظور'}), 400

@app.route('/settings', methods=['GET','POST'])
def settings():
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    if request.method == 'POST':
        new_name = request.form.get('display_name','').strip()
        if new_name: user.display_name = new_name
        new_status = request.form.get('status','').strip()
        user.status = new_status
        privacy_last_seen = request.form.get('privacy_last_seen', 'everyone')
        privacy_online = request.form.get('privacy_online', 'everyone')
        user.privacy_last_seen = privacy_last_seen
        user.privacy_online = privacy_online
        db.session.commit()
        flash('تم تحديث الإعدادات','success')
        return redirect(url_for('settings'))
    blocked_users = User.query.filter(User.id.in_(user.get_blocked_list())).all()
    return render_template('settings.html', username=session['username'], display_name=user.display_name,
                           user=user, blocked_users=blocked_users)

@app.route('/search')
def search():
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user: return redirect(url_for('login'))
    blocked_ids = user.get_blocked_list()
    users = User.query.filter(User.id != user.id, User.deleted == False).all()
    users = [u for u in users if u.id not in blocked_ids]
    return render_template('search.html', username=session['username'], display_name=user.display_name, users=users)

@app.route('/call_log')
def call_log():
    if 'username' not in session: return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted: return redirect(url_for('login'))
    calls = CallLog.query.filter((CallLog.caller_id==user.id)|(CallLog.receiver_id==user.id)).order_by(CallLog.start_time.desc()).all()
    return render_template('call_log.html', username=session['username'], display_name=user.display_name, calls=calls)

@app.route('/story/<int:story_id>')
def view_story(story_id):
    if 'username' not in session: return redirect(url_for('login'))
    story = Story.query.get(story_id)
    if not story: flash('القصة غير موجودة', 'danger'); return redirect(url_for('dashboard'))
    if story.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        flash('انتهت صلاحية القصة', 'info')
        return redirect(url_for('dashboard'))
    return render_template('view_story.html', story=story)

# ========== صفحة البروفايل ==========
@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))
    user = User.query.filter_by(username=session['username']).first()
    if not user or user.deleted:
        return redirect(url_for('login'))
    private_users = get_user_contacts(user.id)
    groups = Group.query.join(GroupMembership).filter(GroupMembership.user_id == user.id).all()
    blocked_users = User.query.filter(User.id.in_(user.get_blocked_list())).all()
    return render_template('profile.html',
                           username=session['username'],
                           display_name=user.display_name,
                           user=user,
                           private_users=private_users,
                           groups=groups,
                           blocked_users=blocked_users,
                           format_last_seen=format_last_seen)

# ========== WebSocket ==========
@socketio.on('connect')
def handle_connect():
    un = session.get('username')
    if un:
        user = User.query.filter_by(username=un).first()
        if user and not user.deleted:
            user.is_online = True
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    print(f"🟢 {un or 'غير معروف'}")

@socketio.on('disconnect')
def handle_disconnect():
    un = session.get('username')
    if un:
        user = User.query.filter_by(username=un).first()
        if user and not user.deleted:
            user.is_online = False
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    print(f"🔴 {un or 'غير معروف'}")

@socketio.on('join_private')
def handle_join_private(data):
    uid = data.get('user_id'); un = session.get('username')
    if not un or not uid: return
    user = User.query.filter_by(username=un).first()
    if not user or user.deleted: return
    other = User.query.get(uid)
    if not other or other.deleted: return
    if user.is_blocked(uid) or other.is_blocked(user.id): return
    room = f"private_{min(user.id, uid)}_{max(user.id, uid)}"
    join_room(room)
    print(f"👤 {un} انضم للغرفة {room}")

@socketio.on('join_group')
def handle_join_group(data):
    gid = data.get('group_id'); un = session.get('username')
    if not un or not gid: return
    user = User.query.filter_by(username=un).first()
    if not user or user.deleted: return
    room = f"group_{gid}"
    join_room(room)
    print(f"👥 {un} انضم للغرفة {room}")

@socketio.on('typing')
def handle_typing(data):
    private_with = data.get('private_with'); group_id = data.get('group_id')
    un = session.get('username')
    if not un: return
    user = User.query.filter_by(username=un).first()
    if not user or user.deleted: return
    if private_with:
        other = User.query.get(private_with)
        if not other or other.deleted: return
        if user.is_blocked(private_with) or other.is_blocked(user.id): return
        room = f"private_{min(user.id, private_with)}_{max(user.id, private_with)}"
        emit('typing', {'from': un, 'display_name': user.display_name, 'private_with': private_with}, room=room)
    elif group_id:
        emit('typing', {'from': un, 'display_name': user.display_name, 'group_id': group_id}, room=f"group_{group_id}")

@socketio.on('send_message')
def handle_send_message(data):
    content = data.get('message','').strip()
    private_with = data.get('private_with')
    group_id = data.get('group_id')
    reply_to = data.get('reply_to')
    un = session.get('username')
    if not un or not content: return
    user = User.query.filter_by(username=un).first()
    if not user or user.deleted: return
    if private_with:
        other = User.query.get(private_with)
        if not other or other.deleted: return
        if user.is_blocked(private_with) or other.is_blocked(user.id): return
        room = f"private_{min(user.id, private_with)}_{max(user.id, private_with)}"
    elif group_id:
        if not GroupMembership.query.filter_by(user_id=user.id, group_id=group_id).first(): return
        room = f"group_{group_id}"
    else: return
    msg = Message(content=content, user_id=user.id, private_with=private_with, group_id=group_id, reply_to=reply_to)
    db.session.add(msg); db.session.commit()
    reply_content = None
    if reply_to:
        parent = Message.query.get(reply_to)
        if parent: reply_content = parent.content
    emit('new_message', {
        'username': un, 'display_name': user.display_name, 'content': msg.content,
        'timestamp': msg.timestamp.strftime('%H:%M'), 'private_with': private_with,
        'group_id': group_id, 'message_id': msg.id, 'reply_to': reply_to,
        'reply_content': reply_content
    }, broadcast=True, room=room)

@socketio.on('signal')
def handle_signal(data):
    target = data.get('target')
    signal_data = data.get('signal')
    video = data.get('video', False)
    from_user = session.get('username')
    if not from_user or not target: return
    emit('signal', {'from': from_user, 'signal': signal_data, 'video': video}, to=target)

# ========== تشغيل التطبيق ==========
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=7070, debug=True, allow_unsafe_werkzeug=True)
