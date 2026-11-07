FROM python:3.12-slim
# av est fixé : ses versions récentes ont retiré une option utilisée par faster-whisper 1.1.1.
RUN pip install --no-cache-dir --only-binary=av faster-whisper==1.1.1 "av==15.1.0" requests
# Fait échouer la construction si le décodage audio ne fonctionne pas, plutôt que le premier fichier.
RUN python -I -c "import wave; w = wave.open('/tmp/t.wav', 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(32000)); w.close(); from faster_whisper.audio import decode_audio; assert len(decode_audio('/tmp/t.wav')) > 0"
COPY worker.py /app/worker.py
ENV PYTHONUNBUFFERED=1 HF_HOME=/models
ENTRYPOINT ["python", "-I", "/app/worker.py"]
