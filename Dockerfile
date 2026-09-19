FROM python:3.12-slim
WORKDIR /app
COPY app.py .
COPY static ./static
RUN pip install --no-cache-dir flask==3.1.2
EXPOSE 8787
CMD ["python","app.py"]
