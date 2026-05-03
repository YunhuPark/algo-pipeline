"""
단일 ngrok 터널로 두 프로젝트 동시 서빙.

라우팅:
  /output_img/*  → localhost:5001  (알고 카드뉴스 Flask)
  /*             → localhost:8080  (SafeKids API)

ngrok은 이 프록시(9000)만 터널링하면 됨.
"""
from __future__ import annotations

from flask import Flask, request, Response
import httpx

app = Flask(__name__)

ALGO_BASE      = "http://localhost:5001"
SAFEKIDS_BASE  = "http://localhost:8080"

_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
}


def _proxy(target: str) -> Response:
    url = target + request.path
    if request.query_string:
        url += "?" + request.query_string.decode()

    headers = {
        k: v for k, v in request.headers
        if k.lower() not in _HOP_HEADERS and k.lower() != "host"
    }

    try:
        resp = httpx.request(
            method=request.method,
            url=url,
            headers=headers,
            content=request.get_data(),
            timeout=60,
            follow_redirects=True,
        )
    except Exception as e:
        return Response(f"Proxy error: {e}", status=502)

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_HEADERS}
    return Response(resp.content, status=resp.status_code, headers=resp_headers)


@app.route("/output_img/<path:path>", methods=["GET", "HEAD"])
def route_algo_img(path: str):
    """알고 카드 이미지 서빙 (Instagram 업로드용)"""
    return _proxy(ALGO_BASE)


@app.route("/", defaults={"path": ""}, methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
@app.route("/<path:path>",            methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
def route_safekids(path: str):
    """SafeKids API 전달"""
    return _proxy(SAFEKIDS_BASE)


if __name__ == "__main__":
    print("[ProxyRouter] 9000 → /output_img/* : 5001 (알고)")
    print("[ProxyRouter] 9000 → /*            : 8080 (SafeKids)")
    app.run(host="0.0.0.0", port=9000, threaded=True)
