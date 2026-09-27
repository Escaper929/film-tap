# film-tap —— 页面和后端同一个进程，一个容器装完。
#
# 镜像由 GitHub Actions 自动构建并推到 Docker Hub（见 .github/workflows/docker.yml），
# NAS 上由 watchtower 自动拉新版。所以这个文件里的东西**直接决定线上跑什么**。
#
# 为什么代码要进镜像、而不是像原来那样挂一个 app 卷：
#   挂卷的话「更新」= 往 NAS 上传新文件，于是 NAS 上的代码和仓库里的代码
#   会各自漂移，出问题时第一件要确认的事就是「NAS 上那份到底是哪一版」。
#   打进镜像之后，镜像标签就是版本，回滚就是换一个标签。

FROM python:3.11-slim

# 运行时不需要装任何第三方包 —— 后端只用标准库。
# FT_* 是后端自己的配置入口（见 nas/server.py 的 env_flag）。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FT_APP=/app \
    FT_DATA=/data \
    FT_PORT=8300 \
    FT_ASSUME_HTTPS=1

WORKDIR /app

# 只拷真正要发出去的那几个文件。测试脚本、shots/、model/ 都不进镜像
# （.dockerignore 里也再挡一道）。
COPY index.html sw.js manifest.webmanifest /app/
COPY nas/server.py /app/server.py

# 非 root 跑。镜像里选的 uid/gid 只是默认值，实际部署时用 `-u 1000:1001`
# 对齐 NAS 上数据目录的属主，所以这里不追求和 NAS 完全一致。
RUN useradd --system --uid 1000 --user-group --home-dir /app --shell /usr/sbin/nologin filmtap \
 && mkdir -p /data \
 && chown 1000:1000 /data

USER 1000:1000
VOLUME ["/data"]
EXPOSE 8300

# 探活打的是同一个 /api/health —— 它报的 https 字段能看出反代有没有把
# 协议转发过来，也就是 Secure cookie 到底有没有生效。
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8300/api/health',timeout=2).status==200 else 1)"

CMD ["python3", "/app/server.py"]
