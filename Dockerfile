FROM python:3.13

WORKDIR /Master_Alchemist

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "Master_Alchemist/main.py"]
