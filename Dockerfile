FROM python:3.13

WORKDIR /emi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .env ./src

EXPOSE 8000

CMD ["python", "./src/emi"]
