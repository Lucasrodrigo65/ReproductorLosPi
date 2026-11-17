FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt
COPY discord_player.py shared_server.py multi_guild.py ./
RUN useradd --create-home lospi
USER lospi
EXPOSE 8080
CMD ["python", "shared_server.py"]
