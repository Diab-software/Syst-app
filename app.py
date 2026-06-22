import os, base64, random, shutil, json
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
app.secret_key = os.environ.get('SECRET_KEY', 'syst-ultimate-secret-key-v2')

# ========== الإعدادات ==========
UPLOAD_FOLDER = 'uploads'
PROFILE_FOLDER = 'uploads/profiles'
STORY_FOLDER = 'uploads/stories'
FILE_FOLDER = 'uploads/files'
AUDIO_FOLDER = 'uploads/audio'
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico',
    'mp4', 'mov', 'avi', 'mkv', 'm4v', '3gp', 'ogv',
    'mp3', 'wav', 'ogg', 'webm', 'oga', 'm4a', 'aac', 'flac', 'opus',
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'rtf', 'odt', 'ods', 'odp',
    'zip', 'rar', '7z', 'tar', 'gz'
}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

for folder in [UPLOAD_FOLDER, PROFILE_FOLDER, STORY_FOLDER, FILE_FOLDER, AUDIO_FOLDER]:
    os.makedirs(folder, exist_ok=True)
os.makedirs(app.instance_path, exist_ok=True)

# ========== قاعدة البيانات ==========
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'syst.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ========== SocketIO ==========
try:
    import eventlet
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
except:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ========== تشفير ==========
key_file = os.path.join(app.instance_path, 'encryption.key')
if os.path.exists(key_file):
    with open(key_file, 'rb') as f: ENCRYPTION_KEY = f.read()
else:
    ENCRYPTION_KEY = Fernet.generate_key()
    with open(key_file, 'wb') as f: f.write(ENCRYPTION_KEY)
cipher = Fernet(ENCRYPTION_KEY)

def encrypt(t): return cipher.encrypt(t.encode()).decode() if t else t
def decrypt(t):
    try: return cipher.decrypt(t.encode()).decode() if t else t
    except: return "[مشفر]"

# ========== نماذج ==========
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

    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)
    def enable_2fa(self): self.otp_secret = pyotp.random_base32(); return self.otp_secret
    def disable_2fa(self): self.otp_secret = None
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
    def content(self): return decrypt(self.content_encrypted)
    @content.setter
    def content(self, plaintext): self.content_encrypted = encrypt(plaintext)

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
    description = db.Column(db.String(500), nullable=True)
    group_pic = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(50), default='active')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    creator = db.relationship('User', foreign_keys=[created_by])

class GroupMembership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    joined_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', backref='memberships')
    group = db.relationship('Group', backref='memberships')

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

class StorySettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    privacy = db.Column(db.String(20), default='everyone')
    duration_hours = db.Column(db.Integer, default=24)
    user = db.relationship('User', backref='story_settings')

class CallLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    call_type = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    end_time = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='initiated')

with app.app_context():
    db.create_all()
    try:
        db.session.execute('ALTER TABLE "group" ADD COLUMN description VARCHAR(500)')
    except: pass
    try:
        db.session.execute('ALTER TABLE "group" ADD COLUMN group_pic VARCHAR(200)')
    except: pass
    try:
        db.session.execute('ALTER TABLE "group" ADD COLUMN status VARCHAR(50) DEFAULT "active"')
    except: pass
    try:
        db.session.execute('ALTER TABLE group_membership ADD COLUMN joined_at DATETIME')
    except: pass
    db.session.commit()
    if not User.query.filter_by(username='admin').first():
        a = User(username='admin', display_name='المدير', email='admin@example.com', status='مرحباً')
        a.set_password('admin123')
        db.session.add(a); db.session.commit()
        if not StorySettings.query.filter_by(user_id=a.id).first():
            db.session.add(StorySettings(user_id=a.id))
            db.session.commit()
        print('✅ admin/admin123')

# ========== دوال مساعدة ==========
def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def file_type(fn):
    ext = fn.rsplit('.',1)[1].lower()
    if ext in {'png','jpg','jpeg','gif','bmp','webp'}: return 'image'
    if ext in {'mp4','mov','avi','mkv','m4v','3gp','ogv'}: return 'video'
    if ext in {'mp3','wav','ogg','webm','oga','m4a','aac','flac','opus'}: return 'audio'
    if ext in {'pdf','doc','docx','xls','xlsx','txt','zip','rar','7z'}: return 'document'
    return 'file'

def get_user_contacts(user_id):
    user = db.session.get(User, user_id)
    if not user: return []
    blocked = user.get_blocked_list()
    contacts = db.session.query(User).join(Message, (Message.private_with == user.id) | (Message.user_id == user.id))\
        .filter(Message.private_with.isnot(None)).filter(User.id != user.id).filter(User.deleted == False).distinct().all()
    return [u for u in contacts if u.id not in blocked]

def get_user_by_session():
    if 'username' not in session: return None
    return db.session.execute(db.select(User).filter_by(username=session['username'], deleted=False)).scalar_one_or_none()

def is_group_admin(user_id, group_id):
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user_id, group_id=group_id)).scalar_one_or_none()
    return membership and membership.is_admin

# ========== المسارات ==========
@app.route('/')
def home():
    return redirect(url_for('dashboard' if 'username' in session else 'login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']; p = request.form['password']
        user = db.session.execute(db.select(User).filter_by(username=u, deleted=False)).scalar_one_or_none()
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
    user = db.session.execute(db.select(User).filter_by(username=u, deleted=False)).scalar_one_or_none()
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
        if db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none():
            flash('اسم المستخدم موجود','danger'); return render_template('signup.html')
        session['signup_temp'] = {'username':username,'display_name':display_name,'email':email,'password':password}
        if enable_2fa: return redirect(url_for('signup_2fa'))
        new_user = User(username=username, display_name=display_name, email=email)
        new_user.set_password(password)
        db.session.add(new_user); db.session.commit()
        db.session.add(StorySettings(user_id=new_user.id))
        db.session.commit()
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
            db.session.add(StorySettings(user_id=u.id))
            db.session.commit()
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
    user = get_user_by_session()
    if user:
        user.is_online = False
        user.last_seen = datetime.now(timezone.utc)
        db.session.commit()
    session.clear()
    flash('تم الخروج','info')
    return redirect(url_for('login'))

@app.route('/delete_account', methods=['POST'])
def delete_account():
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    for folder in [PROFILE_FOLDER, STORY_FOLDER, UPLOAD_FOLDER, FILE_FOLDER, AUDIO_FOLDER]:
        for f in os.listdir(folder):
            if f.startswith(f"profile_{user.id}_") or f.startswith(f"story_{user.id}_"):
                try: os.remove(os.path.join(folder, f))
                except: pass
    messages = Message.query.filter((Message.user_id == user.id) | (Message.private_with == user.id)).all()
    for msg in messages: msg.delete_for_all()
    Story.query.filter_by(user_id=user.id).delete()
    GroupMembership.query.filter_by(user_id=user.id).delete()
    StorySettings.query.filter_by(user_id=user.id).delete()
    user.deleted = True
    db.session.commit()
    session.clear()
    return jsonify({'success': True, 'message': 'تم حذف الحساب'})

@app.route('/delete_private_chat/<int:other_id>', methods=['POST'])
def delete_private_chat(other_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
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
        user = db.session.execute(db.select(User).filter_by(username=u, deleted=False)).scalar_one_or_none()
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
    user = db.session.execute(db.select(User).filter_by(username=u, deleted=False)).scalar_one_or_none()
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
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    private_users = get_user_contacts(user.id)
    groups = Group.query.join(GroupMembership).filter(GroupMembership.user_id == user.id).all()
    contacts_ids = [u.id for u in private_users]
    stories = Story.query.filter(Story.user_id.in_(contacts_ids), Story.expires_at > datetime.now(timezone.utc)).order_by(Story.created_at.desc()).all()
    my_stories = Story.query.filter(Story.user_id == user.id, Story.expires_at > datetime.now(timezone.utc)).order_by(Story.created_at.desc()).all()
    return render_template('dashboard.html', user=user, private_users=private_users, groups=groups, stories=stories, my_stories=my_stories)

@app.route('/profile')
def profile():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    return render_template('profile.html', user=user)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    display_name = request.form.get('display_name','').strip()
    status = request.form.get('status','').strip()
    email = request.form.get('email','').strip()
    if display_name: user.display_name = display_name
    if status: user.status = status
    if email: user.email = email
    db.session.commit()
    flash('تم تحديث الملف الشخصي', 'success')
    return redirect(url_for('profile'))

@app.route('/upload_profile_pic', methods=['POST'])
def upload_profile_pic():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    if 'profile_pic' not in request.files:
        flash('لا توجد صورة','danger'); return redirect(url_for('profile'))
    file = request.files['profile_pic']
    if file.filename == '':
        flash('لم يتم اختيار صورة','danger'); return redirect(url_for('profile'))
    if not allowed_file(file.filename):
        flash('نوع الملف غير مسموح','danger'); return redirect(url_for('profile'))
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    unique_filename = f"profile_{user.id}_{timestamp}_{filename}"
    file_path = os.path.join(PROFILE_FOLDER, unique_filename)
    file.save(file_path)
    if user.profile_pic and os.path.exists(os.path.join(PROFILE_FOLDER, user.profile_pic)):
        try: os.remove(os.path.join(PROFILE_FOLDER, user.profile_pic))
        except: pass
    user.profile_pic = unique_filename
    db.session.commit()
    flash('تم تحديث الصورة الشخصية','success')
    return redirect(url_for('profile'))

@app.route('/story_settings', methods=['GET','POST'])
def story_settings():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    settings = StorySettings.query.filter_by(user_id=user.id).first()
    if not settings:
        settings = StorySettings(user_id=user.id)
        db.session.add(settings); db.session.commit()
    if request.method == 'POST':
        privacy = request.form.get('privacy', 'everyone')
        duration_hours = int(request.form.get('duration_hours', 24))
        settings.privacy = privacy
        settings.duration_hours = duration_hours
        db.session.commit()
        flash('تم تحديث إعدادات الستوري', 'success')
        return redirect(url_for('story_settings'))
    return render_template('story_settings.html', user=user, settings=settings)

@app.route('/upload_story', methods=['POST'])
def upload_story():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    story_type = request.form.get('story_type', 'media')
    caption = request.form.get('caption', '').strip()
    content_text = request.form.get('content_text', '').strip()
    settings = StorySettings.query.filter_by(user_id=user.id).first()
    duration_hours = settings.duration_hours if settings else 24
    expires_at = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

    if story_type == 'text':
        if not content_text:
            flash('يرجى إدخال نص القصة', 'danger')
            return redirect(url_for('dashboard'))
        story = Story(user_id=user.id, media_type='text', caption=caption, content_text=content_text, expires_at=expires_at)
        db.session.add(story); db.session.commit()
        flash('تم نشر القصة النصية', 'success')
        return redirect(url_for('dashboard'))
    else:
        if 'story_media' not in request.files:
            flash('لا يوجد ملف', 'danger'); return redirect(url_for('dashboard'))
        file = request.files['story_media']
        if file.filename == '':
            flash('لم يتم اختيار ملف', 'danger'); return redirect(url_for('dashboard'))
        if not allowed_file(file.filename):
            flash('نوع الملف غير مسموح', 'danger'); return redirect(url_for('dashboard'))
        filename = secure_filename(file.filename)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        unique_filename = f"story_{user.id}_{timestamp}_{filename}"
        file_path = os.path.join(STORY_FOLDER, unique_filename)
        file.save(file_path)
        media_type = file_type(filename)
        story = Story(user_id=user.id, media_path=unique_filename, media_type=media_type, caption=caption, expires_at=expires_at)
        db.session.add(story); db.session.commit()
        flash('تم نشر القصة', 'success')
        return redirect(url_for('dashboard'))

@app.route('/story/<int:story_id>')
def view_story(story_id):
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    story = db.session.get(Story, story_id)
    if not story: flash('القصة غير موجودة', 'danger'); return redirect(url_for('dashboard'))
    if story.expires_at.tzinfo is None:
        expires_at = story.expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = story.expires_at
    if expires_at < datetime.now(timezone.utc):
        flash('انتهت صلاحية القصة', 'info')
        return redirect(url_for('dashboard'))
    if story.user_id != user.id and user.is_blocked(story.user_id):
        flash('لا يمكنك رؤية هذه القصة', 'danger'); return redirect(url_for('dashboard'))
    return render_template('view_story.html', story=story)

@app.route('/chat/private/<int:user_id>')
def private_chat(user_id):
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
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
    return render_template('private_chat.html', user=user, other=other, messages=messages)

@app.route('/chat/group/<int:group_id>')
def group_chat(group_id):
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    group = db.session.get(Group, group_id)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('dashboard'))
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership:
        flash('لست عضواً','danger'); return redirect(url_for('dashboard'))
    messages = Message.query.filter(Message.group_id == group_id).order_by(Message.timestamp.asc()).all()
    messages = [m for m in messages if not m.is_deleted_for(user.id)]
    pinned_msgs = [m for m in messages if m.pinned]
    normal_msgs = [m for m in messages if not m.pinned]
    messages = pinned_msgs + normal_msgs
    return render_template('group_chat.html', user=user, group=group, messages=messages, is_admin=membership.is_admin)

@app.route('/group_settings/<int:group_id>', methods=['GET','POST'])
def group_settings(group_id):
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    group = db.session.get(Group, group_id)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('dashboard'))
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership:
        flash('لست عضواً','danger'); return redirect(url_for('dashboard'))
    if not membership.is_admin:
        flash('أنت لست مشرفاً','danger'); return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        status = request.form.get('status', 'active')
        if name:
            group.name = name
        group.description = description
        group.status = status
        db.session.commit()
        flash('تم تحديث إعدادات المجموعة', 'success')
        return redirect(url_for('group_settings', group_id=group.id))
    members = db.session.execute(
        db.select(User, GroupMembership).join(GroupMembership, GroupMembership.user_id == User.id)
        .filter(GroupMembership.group_id == group.id)
    ).all()
    return render_template('group_settings.html', user=user, group=group, members=members, is_admin=membership.is_admin)

@app.route('/upload_group_pic/<int:group_id>', methods=['POST'])
def upload_group_pic(group_id):
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    group = db.session.get(Group, group_id)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('dashboard'))
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership or not membership.is_admin:
        flash('غير مصرح','danger'); return redirect(url_for('dashboard'))
    if 'group_pic' not in request.files:
        flash('لا توجد صورة','danger'); return redirect(url_for('group_settings', group_id=group.id))
    file = request.files['group_pic']
    if file.filename == '':
        flash('لم يتم اختيار صورة','danger'); return redirect(url_for('group_settings', group_id=group.id))
    if not allowed_file(file.filename):
        flash('نوع الملف غير مسموح','danger'); return redirect(url_for('group_settings', group_id=group.id))
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    unique_filename = f"group_{group.id}_{timestamp}_{filename}"
    file_path = os.path.join(PROFILE_FOLDER, unique_filename)
    file.save(file_path)
    if group.group_pic and os.path.exists(os.path.join(PROFILE_FOLDER, group.group_pic)):
        try: os.remove(os.path.join(PROFILE_FOLDER, group.group_pic))
        except: pass
    group.group_pic = unique_filename
    db.session.commit()
    flash('تم تحديث صورة المجموعة','success')
    return redirect(url_for('group_settings', group_id=group.id))

@app.route('/group_add_member/<int:group_id>', methods=['POST'])
def group_add_member(group_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    group = db.session.get(Group, group_id)
    if not group: return jsonify({'error': 'المجموعة غير موجودة'}), 404
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'غير مصرح'}), 403
    username = request.form.get('username', '').strip()
    if not username:
        return jsonify({'error': 'اسم المستخدم مطلوب'}), 400
    new_user = db.session.execute(db.select(User).filter_by(username=username, deleted=False)).scalar_one_or_none()
    if not new_user:
        return jsonify({'error': 'المستخدم غير موجود'}), 404
    if db.session.execute(db.select(GroupMembership).filter_by(user_id=new_user.id, group_id=group.id)).scalar_one_or_none():
        return jsonify({'error': 'المستخدم عضو بالفعل'}), 400
    db.session.add(GroupMembership(user_id=new_user.id, group_id=group.id, is_admin=False))
    db.session.commit()
    return jsonify({'success': True, 'message': f'تمت إضافة {new_user.display_name}'})

@app.route('/group_remove_member/<int:group_id>/<int:member_id>', methods=['POST'])
def group_remove_member(group_id, member_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    group = db.session.get(Group, group_id)
    if not group: return jsonify({'error': 'المجموعة غير موجودة'}), 404
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'غير مصرح'}), 403
    if user.id == member_id:
        return jsonify({'error': 'لا يمكنك إزالة نفسك'}), 400
    target_membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=member_id, group_id=group.id)).scalar_one_or_none()
    if not target_membership:
        return jsonify({'error': 'المستخدم ليس عضواً'}), 404
    db.session.delete(target_membership)
    db.session.commit()
    return jsonify({'success': True, 'message': 'تمت إزالة العضو'})

@app.route('/group_set_admin/<int:group_id>/<int:member_id>', methods=['POST'])
def group_set_admin(group_id, member_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    group = db.session.get(Group, group_id)
    if not group: return jsonify({'error': 'المجموعة غير موجودة'}), 404
    membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group.id)).scalar_one_or_none()
    if not membership or not membership.is_admin:
        return jsonify({'error': 'غير مصرح'}), 403
    target_membership = db.session.execute(db.select(GroupMembership).filter_by(user_id=member_id, group_id=group.id)).scalar_one_or_none()
    if not target_membership:
        return jsonify({'error': 'المستخدم ليس عضواً'}), 404
    target_membership.is_admin = not target_membership.is_admin
    db.session.commit()
    return jsonify({'success': True, 'message': 'تم تحديث صلاحيات المشرف'})

@app.route('/upload_file', methods=['POST'])
def upload_file_general():
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    private_with = request.form.get('private_with', type=int)
    group_id = request.form.get('group_id', type=int)
    if 'file' not in request.files:
        return jsonify({'error': 'لا يوجد ملف'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'نوع الملف غير مسموح'}), 400
    filename = secure_filename(file.filename)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    unique_filename = f"{timestamp}_{filename}"
    ftype = file_type(filename)
    if filename.startswith('recording_'):
        ftype = 'audio'
    if ftype == 'audio':
        folder = AUDIO_FOLDER
    elif ftype in ['image', 'video']:
        folder = UPLOAD_FOLDER
    else:
        folder = FILE_FOLDER
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, unique_filename)
    file.save(file_path)
    file_url = url_for('download_file', filename=unique_filename)
    if ftype == 'image': msg_content = f"🖼️ صورة: {filename}"
    elif ftype == 'video': msg_content = f"🎬 فيديو: {filename}"
    elif ftype == 'audio': msg_content = f"🎵 تسجيل صوتي: {filename}"
    else: msg_content = f"📎 ملف: {filename} (حجم: {os.path.getsize(file_path)//1024} كيلوبايت)"
    msg = Message(content=msg_content, user_id=user.id, file_name=filename, file_path=file_path, file_type=ftype,
                  private_with=private_with, group_id=group_id)
    db.session.add(msg); db.session.commit()
    room = None
    if private_with:
        room = f"private_{min(user.id, private_with)}_{max(user.id, private_with)}"
    elif group_id:
        room = f"group_{group_id}"
    if room:
        socketio.emit('new_message', {
            'username': user.username, 'display_name': user.display_name, 'content': msg.content,
            'timestamp': msg.timestamp.strftime('%H:%M'), 'file_name': filename, 'file_url': file_url,
            'file_type': ftype, 'private_with': private_with, 'group_id': group_id, 'message_id': msg.id
        }, namespace='/', room=room)
    return jsonify({'success': True, 'message': 'تم رفع الملف'})

@app.route('/create_group', methods=['POST'])
def create_group():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    name = request.form.get('name','').strip()
    description = request.form.get('description','').strip()
    if not name: flash('اسم المجموعة مطلوب','danger'); return redirect(url_for('settings'))
    g = Group(name=name, description=description, created_by=user.id)
    db.session.add(g); db.session.commit()
    db.session.add(GroupMembership(user_id=user.id, group_id=g.id, is_admin=True)); db.session.commit()
    flash('تم إنشاء المجموعة','success'); return redirect(url_for('group_settings', group_id=g.id))

@app.route('/join_group', methods=['POST'])
def join_group():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    gid = request.form.get('group_id', type=int)
    if not gid: flash('معرف المجموعة مطلوب','danger'); return redirect(url_for('settings'))
    group = db.session.get(Group, gid)
    if not group: flash('المجموعة غير موجودة','danger'); return redirect(url_for('settings'))
    if db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=gid)).scalar_one_or_none():
        flash('أنت عضو بالفعل','info'); return redirect(url_for('settings'))
    db.session.add(GroupMembership(user_id=user.id, group_id=gid)); db.session.commit()
    flash('تم الانضمام','success'); return redirect(url_for('settings'))

@app.route('/download/<path:filename>')
def download_file(filename):
    if filename.startswith('profiles/'):
        return send_from_directory(PROFILE_FOLDER, filename.replace('profiles/', ''))
    elif filename.startswith('stories/'):
        return send_from_directory(STORY_FOLDER, filename.replace('stories/', ''))
    else:
        for folder in [UPLOAD_FOLDER, FILE_FOLDER, AUDIO_FOLDER]:
            if os.path.exists(os.path.join(folder, filename)):
                return send_from_directory(folder, filename)
        return 'File not found', 404

@app.route('/delete_message/<int:message_id>', methods=['POST'])
def delete_message(message_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    msg = db.session.get(Message, message_id)
    if not msg: return jsonify({'error': 'الرسالة غير موجودة'}), 404
    if msg.user_id == user.id or (msg.private_with == user.id) or (msg.group_id and GroupMembership.query.filter_by(user_id=user.id, group_id=msg.group_id).first()):
        msg.delete_for_user(user.id)
        return jsonify({'success': True})
    return jsonify({'error': 'غير مصرح'}), 403

@app.route('/pin_message/<int:message_id>', methods=['POST'])
def pin_message(message_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    msg = db.session.get(Message, message_id)
    if not msg: return jsonify({'error': 'الرسالة غير موجودة'}), 404
    if msg.user_id == user.id or (msg.private_with == user.id) or (msg.group_id and GroupMembership.query.filter_by(user_id=user.id, group_id=msg.group_id).first()):
        msg.pinned = not msg.pinned
        db.session.commit()
        return jsonify({'success': True, 'pinned': msg.pinned})
    return jsonify({'error': 'غير مصرح'}), 403

@app.route('/block_user/<int:user_id>', methods=['POST'])
def block_user(user_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    target = db.session.get(User, user_id)
    if not target: return jsonify({'error': 'المستخدم المستهدف غير موجود'}), 404
    if user.block_user(user_id):
        return jsonify({'success': True, 'message': f'تم حظر {target.display_name}'})
    return jsonify({'error': 'المستخدم محظور بالفعل'}), 400

@app.route('/unblock_user/<int:user_id>', methods=['POST'])
def unblock_user(user_id):
    user = get_user_by_session()
    if not user: return jsonify({'error': 'غير مسجل'}), 401
    target = db.session.get(User, user_id)
    if not target: return jsonify({'error': 'المستخدم المستهدف غير موجود'}), 404
    if user.unblock_user(user_id):
        return jsonify({'success': True, 'message': f'تم إلغاء حظر {target.display_name}'})
    return jsonify({'error': 'المستخدم غير محظور'}), 400

@app.route('/settings', methods=['GET','POST'])
def settings():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
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
    return render_template('settings.html', user=user, blocked_users=blocked_users)

@app.route('/search')
def search():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    blocked_ids = user.get_blocked_list()
    users = User.query.filter(User.id != user.id, User.deleted == False).all()
    users = [u for u in users if u.id not in blocked_ids]
    return render_template('search.html', user=user, users=users)

@app.route('/call_log')
def call_log():
    user = get_user_by_session()
    if not user: return redirect(url_for('login'))
    calls = CallLog.query.filter((CallLog.caller_id==user.id)|(CallLog.receiver_id==user.id)).order_by(CallLog.start_time.desc()).all()
    return render_template('call_log.html', user=user, calls=calls)

# ========== WebSocket ==========
@socketio.on('connect')
def handle_connect():
    un = session.get('username')
    if un:
        user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
        if user:
            user.is_online = True
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    print(f"🟢 {un or 'غير معروف'}")

@socketio.on('disconnect')
def handle_disconnect():
    un = session.get('username')
    if un:
        user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
        if user:
            user.is_online = False
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()
    print(f"🔴 {un or 'غير معروف'}")

@socketio.on('join_private')
def handle_join_private(data):
    uid = data.get('user_id'); un = session.get('username')
    if not un or not uid: return
    user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
    if not user: return
    other = db.session.get(User, uid)
    if not other or other.deleted: return
    if user.is_blocked(uid) or other.is_blocked(user.id): return
    room = f"private_{min(user.id, uid)}_{max(user.id, uid)}"
    join_room(room)
    print(f"👤 {un} انضم للغرفة {room}")

@socketio.on('join_group')
def handle_join_group(data):
    gid = data.get('group_id'); un = session.get('username')
    if not un or not gid: return
    user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
    if not user: return
    room = f"group_{gid}"
    join_room(room)
    print(f"👥 {un} انضم للغرفة {room}")

@socketio.on('typing')
def handle_typing(data):
    private_with = data.get('private_with'); group_id = data.get('group_id')
    un = session.get('username')
    if not un: return
    user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
    if not user: return
    if private_with:
        other = db.session.get(User, private_with)
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
    user = db.session.execute(db.select(User).filter_by(username=un, deleted=False)).scalar_one_or_none()
    if not user: return
    if private_with:
        other = db.session.get(User, private_with)
        if not other or other.deleted: return
        if user.is_blocked(private_with) or other.is_blocked(user.id): return
        room = f"private_{min(user.id, private_with)}_{max(user.id, private_with)}"
    elif group_id:
        if not db.session.execute(db.select(GroupMembership).filter_by(user_id=user.id, group_id=group_id)).scalar_one_or_none():
            return
        room = f"group_{group_id}"
    else: return
    msg = Message(content=content, user_id=user.id, private_with=private_with, group_id=group_id, reply_to=reply_to)
    db.session.add(msg); db.session.commit()
    reply_content = None
    if reply_to:
        parent = db.session.get(Message, reply_to)
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7070))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
