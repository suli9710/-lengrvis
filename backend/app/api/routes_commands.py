from __future__ import annotations

from fastapi import APIRouter

from app.commands.schemas import CommandExecuteRequest, CommandResult
from app.commands.service import execute_command, list_commands


router = APIRouter()


@router.get("/commands")
def commands() -> dict:
    return list_commands()


@router.post("/commands/execute", response_model=CommandResult)
async def commands_execute(payload: CommandExecuteRequest) -> CommandResult:
    return await execute_command(payload)
