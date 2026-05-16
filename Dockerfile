FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
CMD ["python3", "main.py"]
