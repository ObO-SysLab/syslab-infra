FROM python:3.11-slim

# Trusted coordinator: no compiler or submitted-code execution path.

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY grader.py .
COPY runner_client.py execution_protocol.py ./

CMD ["python3", "grader.py"]
