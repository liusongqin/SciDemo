在项目根目录执行：

```bash
mkdir -p logs run

nohup bash scripts/start.sh \
  > logs/scidemo.log 2>&1 \
  < /dev/null &

echo $! > run/scidemo.pid
```

之后关闭终端，服务仍会运行。

查看日志：

```bash
tail -f logs/scidemo.log
```

停止全部服务：

```bash
kill "$(cat run/scidemo.pid)"
```

检查是否还在运行：

```bash
ps -p "$(cat run/scidemo.pid)" -f
```

`start.sh` 已有退出清理逻辑，因此停止主脚本时，它启动的模型、后端、前端和 SSH 隧道也会一起停止。注意脚本当前使用了 Uvicorn `--reload` 和前端开发服务器，更适合开发环境。