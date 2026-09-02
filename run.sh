#!/usr/bin/env bash
# 开发模式启动（热重载）
exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
