FROM python:3.11-slim

# Trusted coordinator: no compiler or submitted-code execution path.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY grader.py .
COPY runner_client.py execution_protocol.py ./
COPY obo_checker.py obo_assessment.py ./
COPY obo_runtime ./obo_runtime

CMD ["python3", "grader.py"]
