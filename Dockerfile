# FinHOT 的 Python 部分：快讯推送常驻进程（python -m finhot push）。网站引擎的镜像见 web/Dockerfile。
# 构建参数 PIP_INDEX_URL 可以换成国内 PyPI 镜像（deploy/finhot.sh 默认用腾讯云的）。
FROM python:3.12-slim
ARG PIP_INDEX_URL=
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url $PIP_INDEX_URL} -r requirements.txt
COPY finhot finhot
COPY sources sources
COPY prompts prompts
RUN useradd --system --home /app finhot && mkdir -p data && chown finhot data
USER finhot
CMD ["python", "-m", "finhot", "push"]
