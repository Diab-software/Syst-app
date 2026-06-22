FROM python:3.10-slim

WORKDIR /app

# نسخ المتطلبات أولاً للاستفادة من التخزين المؤقت
COPY requirements.txt .

# تثبيت المتطلبات (بدون gevent/eventlet)
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الملفات
COPY . .

# تعيين المنفذ الذي يستخدمه Render (10000)
EXPOSE 10000

# تشغيل التطبيق باستخدام gunicorn على المنفذ الصحيح
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:10000", "app:app"]
