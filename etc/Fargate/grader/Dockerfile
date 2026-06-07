FROM python:3.11-slim

# gcc, g++ 설치 (C/C++ 컴파일용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY grader.py .

CMD ["python3", "grader.py"]
