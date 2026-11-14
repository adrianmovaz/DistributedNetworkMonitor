FROM python:3.12-slim
RUN pip install --no-cache-dir pyftpdlib==2.1.0
WORKDIR /srv/ftp
CMD ["python", "-m", "pyftpdlib", "--interface", "0.0.0.0", "--port", "2121", "--username", "lab", "--password", "lab-only", "--write", "--directory", "/srv/ftp", "--range", "30000-30009", "--nat-address", "127.0.0.1"]
