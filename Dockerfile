FROM python:3.11-slim

WORKDIR /app
COPY server.py requirements.txt ./
COPY static ./static
RUN mkdir -p /data

ENV PORT=8080
ENV DATA_DIR=/data
EXPOSE 8080
VOLUME ["/data"]

CMD ["python", "server.py"]
