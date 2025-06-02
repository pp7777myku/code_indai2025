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
COPY . .

# 声明容器在运行时监听的端口（这里是 8000）
# 这只是一个元数据声明，实际端口映射在 docker run 时指定
EXPOSE 8000

# 设置环境变量 (可选，但推荐)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 容器启动时运行的命令
# 假设你的 Flask 应用对象在 main.py 文件中名为 'app'
# 并且你希望使用 Gunicorn 来运行它
# 如果你的入口文件或应用对象名称不同，请相应修改 'main:app'
# 如果 main.py 是直接可执行的并且自己启动服务器 (例如 app.run(host='0.0.0.0'))
# 你可以使用: CMD ["python", "main.py"]
# 但对于生产环境，推荐为 Flask 使用 Gunicorn 这样的 WSGI 服务器。
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "main:app"]