FROM python:3.11
WORKDIR /app
COPY . .
RUN pip install flask aiosqlite aiohttp requests
CMD ["python", "bot.py"]
