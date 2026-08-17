"""Web UI for the lab N9020A."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rfsa.errors import ConnectionFailed, ParameterError, RfsaError, ScpiError
from rfsa.limits import Limits
from rfsa.models import DETECTORS
from rfsa.web.session import Lab, NotConnected

STATIC = Path(__file__).parent / "static"


class ConnectBody(BaseModel):
    address: str = ""
    fake: bool = False


class ConfigureBody(BaseModel):
    center_hz: float | None = None
    span_hz: float | None = None
    rbw_hz: float | None = None
    vbw_hz: float | None = None
    points: int | None = None
    sweep_time_s: float | None = None
    ref_level_dbm: float | None = None
    attenuation_db: float | None = None
    preamp: bool | None = None
    detector: str | None = None


class ScanBody(ConfigureBody):
    label: str | None = Field(default=None)


def create_app(db_path: str = "measurements.db", *,
               screenshot_dir: str = "screenshots",
               auto_fake: bool = False,
               auto_address: str | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lab = Lab(db_path, screenshot_dir=screenshot_dir)
        app.state.lab = lab
        try:
            if auto_fake:
                lab.connect(fake=True)
            elif auto_address:
                lab.connect(auto_address)
            yield
        finally:
            lab.close()

    app = FastAPI(title="频谱仪 N9020A", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def lab() -> Lab:
        return app.state.lab

    @app.exception_handler(NotConnected)
    async def not_connected(_request: Request, exc: NotConnected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ParameterError)
    async def bad_parameter(_request: Request, exc: ParameterError) -> JSONResponse:
        return JSONResponse({"detail": f"参数错误：{exc}"}, status_code=400)

    @app.exception_handler(ConnectionFailed)
    async def connection_failed(_request: Request, exc: ConnectionFailed) -> JSONResponse:
        return JSONResponse({"detail": f"连接失败：{exc}"}, status_code=502)

    @app.exception_handler(ScpiError)
    async def scpi_error(_request: Request, exc: ScpiError) -> JSONResponse:
        return JSONResponse({"detail": f"仪器错误：{exc}"}, status_code=502)

    @app.exception_handler(RfsaError)
    async def rfsa_error(_request: Request, exc: RfsaError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(_request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/limits")
    def limits() -> dict[str, Any]:
        payload = asdict(Limits())
        payload["detectors"] = list(DETECTORS)
        return payload

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return lab().status()

    @app.post("/api/connect")
    def connect(body: ConnectBody) -> dict[str, Any]:
        return lab().connect(body.address, fake=body.fake)

    @app.post("/api/disconnect")
    def disconnect() -> dict[str, Any]:
        return lab().disconnect()

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        return lab().configure({})

    @app.post("/api/configure")
    def configure(body: ConfigureBody) -> dict[str, Any]:
        return lab().configure(body.model_dump())

    @app.post("/api/scan")
    def scan(body: ScanBody = ScanBody()) -> dict[str, Any]:
        payload = body.model_dump()
        label = payload.pop("label", None)
        return lab().scan(label, payload)

    @app.get("/api/sweeps")
    def sweeps() -> list[dict[str, Any]]:
        return lab().list_sweeps()

    @app.get("/api/sweeps/{sweep_id}")
    def sweep(sweep_id: int) -> dict[str, Any]:
        try:
            return lab().get_sweep(sweep_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有编号 {sweep_id} 的扫描记录")

    @app.get("/api/sweeps/{sweep_id}/screenshot")
    def screenshot(sweep_id: int) -> FileResponse:
        try:
            path = lab().screenshot_file(sweep_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"没有编号 {sweep_id} 的扫描记录")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="N9020A 网页控制")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址，局域网访问请保持 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="measurements.db")
    parser.add_argument("--screenshots", default="screenshots",
                        help="截图保存目录（相对当前工作目录）")
    parser.add_argument("--fake", action="store_true", help="启动时连上假仪器")
    parser.add_argument("--address", default="",
                        help="启动时连接的 VISA 地址")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("请先安装：pip install -e .") from exc

    app = create_app(args.db, screenshot_dir=args.screenshots,
                     auto_fake=args.fake,
                     auto_address=args.address or None)
    print(f"本机: http://127.0.0.1:{args.port}")
    print(f"局域网: http://<这台电脑的IP>:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
