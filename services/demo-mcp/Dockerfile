# One image for both demo MCP servers; the Helm chart picks the module:
#   app.server:app   -> store-info      app.shipping:app -> shipping-info
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEMO_APP=app.server:app
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY services/demo-mcp/app ./app
EXPOSE 8000
USER 10001:10001
CMD ["sh", "-c", "exec uvicorn \"$DEMO_APP\" --host 0.0.0.0 --port 8000"]
