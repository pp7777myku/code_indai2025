# 使用官方 Python 运行时作为父镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 将依赖文件复制到工作目录
COPY requirements.txt .

# 安装依赖 (使用 --no-cache-dir 减小镜像体积)
RUN pip install --no-cache-dir -r requirements.txt

# 将当前目录内容复制到容器的 /app 目录
COPY . .

# 暴露 FastAPI 应用运行的端口 (容器内部端口)
EXPOSE 8000

# 运行 uvicorn 服务器的命令
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
# 在生产环境中，通常不使用 --reload
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]