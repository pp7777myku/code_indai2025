# 使用官方 Python 运行时作为基础镜像
# Python 3.13 是一个较新的版本，-slim 镜像是减小体积的好选择。
# 构建时请确保 Docker Hub 上已有官方的 Python 3.13 镜像。
# 如果没有，你可能需要使用 3.12 或等待 3.13 的官方镜像。
FROM python:3.13-slim

# 设置容器内的工作目录
WORKDIR /app

# 将 requirements.txt 文件复制到容器的 /app 目录下
COPY requirements.txt .

# 安装 requirements.txt 中指定的所有依赖包
# --no-cache-dir 可以减小镜像体积
# --trusted-host pypi.python.org 在连接 PyPI 遇到 SSL 问题时可以尝试添加
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# 将项目中的其他所有文件和文件夹复制到容器的 /app 目录下
# 强烈建议在此之前添加一个 .dockerignore 文件，以排除不必要的文件
COPY . .

# 声明容器在运行时监听的端口（这里是 8000）
# 这只是一个元数据声明，实际端口映射在 docker run 时指定
EXPOSE 8000

# 设置环境变量 (可选，但推荐)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 容器启动时运行的命令
# 使用 Gunicorn 并指定 Uvicorn worker 来运行 FastAPI 应用
# -w 4: 指定4个worker进程 (您可以根据服务器CPU核心数调整，通常是 2 * CPU核心数 + 1)
# -k uvicorn.workers.UvicornWorker: 指定使用Uvicorn的worker类来处理ASGI应用
# --bind 0.0.0.0:8000: 监听所有网络接口的8000端口
# main:app: 假设您的FastAPI应用实例在main.py文件中名为app
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "main:app"]