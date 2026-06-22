FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 10000
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "-b", "0.0.0.0:10000", "app:app"]
