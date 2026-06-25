"""轻量 HTTP 服务：提供健康检查与状态查询端点。

端点：
    GET /healthz   —— 存活探针（Docker HEALTHCHECK 使用）
    GET /readyz    —— 就绪探针
    GET /stats     —— 运行统计

实现说明：
    使用标准库 http.server 避免引入额外依赖（如 FastAPI）。
    若后续需要更丰富的 API，可迁移至 FastAPI。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


class HealthHandler(BaseHTTPRequestHandler):
    """健康检查请求处理器。"""

    # 由 HealthServer 注入
    stats_provider: Callable[[], dict[str, Any]] = staticmethod(lambda: {})

    def do_GET(self) -> None:
        """处理 GET 请求。"""
        routes = {
            "/healthz": self._handle_health,
            "/readyz": self._handle_ready,
            "/stats": self._handle_stats,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._respond(404, {"error": "not found"})
            return
        handler()

    def _handle_health(self) -> None:
        """存活探针：进程能响应即健康。"""
        self._respond(200, {"status": "ok"})

    def _handle_ready(self) -> None:
        """就绪探针：核心组件已初始化。"""
        # TODO(P5): 检查 Telegram 客户端连接状态
        self._respond(200, {"status": "ready"})

    def _handle_stats(self) -> None:
        """运行统计。"""
        self._respond(200, self.stats_provider())

    def _respond(self, code: int, body: dict[str, Any]) -> None:
        """发送 JSON 响应。"""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """禁用默认访问日志，避免刷屏。"""
        pass


class HealthServer:
    """健康检查 HTTP 服务器。"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 18080,
        stats_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        HealthHandler.stats_provider = staticmethod(stats_provider or (lambda: {}))

    def start(self) -> None:
        """启动 HTTP 服务器（阻塞，应在独立线程中运行）。"""
        self._server = ThreadingHTTPServer((self._host, self._port), HealthHandler)
        logger.info("健康检查服务已启动: http://{}:{}/healthz", self._host, self._port)
        self._server.serve_forever()

    def stop(self) -> None:
        """停止服务器。"""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("健康检查服务已停止")
