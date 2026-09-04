FROM python:3.13

WORKDIR /emi

COPY requirements.txt pyproject.toml README.md .

RUN pip install --no-cache-dir -r requirements.txt


COPY src ./src

RUN pip install --no-cache-dir .

COPY .env .

EXPOSE 8000

CMD ["python", "-m", "emi"]
