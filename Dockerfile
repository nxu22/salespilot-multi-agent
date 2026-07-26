# Kept close to the Render runtime: same Python minor version, same entrypoint.
FROM python:3.12-slim

WORKDIR /app

# Dependencies in their own layer so editing source doesn't reinstall chromadb.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
