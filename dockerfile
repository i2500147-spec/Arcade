FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install aiogram aiosqlite aiohttp flask
CMD ["python", "bot.py"]
