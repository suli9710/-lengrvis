from __future__ import annotations

from fastapi import APIRouter

from app.services import app_service


router = APIRouter()


@router.get("/apps")
def apps():
    return app_service.list_apps()


@router.post("/apps/launch")
def launch(payload: dict):
    return app_service.launch(payload)


@router.post("/apps/open-file")
def open_file(payload: dict):
    return app_service.open_file(payload)


@router.post("/apps/open-folder")
def open_folder(payload: dict):
    return app_service.open_folder(payload)


@router.post("/apps/reveal")
def reveal(payload: dict):
    return app_service.reveal(payload)

