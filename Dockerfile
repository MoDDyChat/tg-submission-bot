FROM python:3.12-slim

WORKDIR /app

RUN useradd -m -u 1000 botuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R botuser:botuser /app

USER botuser

CMD ["python", "main.py"]
