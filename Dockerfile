FROM python:3.12.6

RUN python -mpip install --upgrade pip

RUN mkdir -p /app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
