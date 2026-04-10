import asyncio
from .node_summary import NodeSummary
from .graph import AgentEntryPoint

class TestCase:
    @staticmethod
    async def CaseGenerate(data: dict):
        return await AgentEntryPoint.case_generate(data)

    @staticmethod
    async def SingleEnhance(data: dict):
        return await AgentEntryPoint.case_draft(data)

    @staticmethod
    async def CaseSummary(data: dict):
        return await NodeSummary.case_summary(data)

class TestScript:
    @staticmethod
    async def ScriptGenerateStream(data: dict, event_queue: asyncio.Queue):
        return await AgentEntryPoint.script_generate_stream(data, event_queue)
