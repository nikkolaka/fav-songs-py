# For more information, please refer to https://aka.ms/vscode-docker-python
FROM node:20-alpine AS ui-build

WORKDIR /ui
COPY ui/option-1/package.json ./
RUN npm install --no-audit --no-fund
COPY ui/option-1/ ./
RUN npm run build

FROM python:3.12-slim

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install pip requirements
COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . /app
COPY --from=ui-build /ui/dist /app/ui/option-1/dist

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser --uid 5678 --disabled-password --gecos "" appuser \
    && mkdir -p /app/data \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
