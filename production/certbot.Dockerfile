FROM certbot/certbot:v5.9.0
COPY production/tls.py /opt/onepost/tls.py
ENTRYPOINT ["python", "/opt/onepost/tls.py"]
CMD ["renew-loop"]
