FROM xserrat/facebook-demucs:latest

WORKDIR /srv
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY worker-server.py .

ENTRYPOINT []
CMD ["python3", "worker-server.py"]
