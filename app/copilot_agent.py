"""AI Copilot integration using claude-agent-sdk."""

import asyncio
import base64
import mimetypes
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from claude_agent_sdk import ClaudeAgentOptions, query, tool, create_sdk_mcp_server
from claude_agent_sdk.types import AssistantMessage, TextBlock

SYSTEM_PROMPT = """You are the PhotoSuit AI Copilot, specialized in designing SVG templates for PhotoSuit.
PhotoSuit is an image border and watermark compositing tool.
SVG Templates must contain Jinja2 variables for dynamic rendering.

Key concepts:
1. `layout.image_width` and `layout.image_height` represent the main image dimensions.
2. Calculate padded dimensions `canvas_w` and `canvas_h` using a padding property.
3. Templates use `<svg viewBox="0 0 {{ canvas_w }} {{ canvas_h }}">`.

You have tools to read and write `template.svg` and `config.json` in the current template directory.
Use them to help the user modify the template if requested.
Always double check the SVG structure when making changes.
Do not output the entire SVG file in chat if you can use the `write_file` tool to save it, but explain what you changed.
"""

class CopilotManager:
    def __init__(self, callback: Callable[[str, str], None]):
        """
        callback: invoked when new messages arrive. callback(role, text)
        """
        self.callback = callback
        self.current_tpl_dir: Optional[Path] = None
        self.current_tpl_id: Optional[str] = None
        
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        self.mcp_server = None
        self._setup_tools()

    def _setup_tools(self):
        @tool("read_file", "Read a file from the current template directory (e.g. template.svg, config.json)",
              {"type": "object", "properties": {"filename": {"type": "string", "description": "Name of the file to read"}}, "required": ["filename"]})
        async def read_file(args: dict) -> dict:
            if not self.current_tpl_dir:
                return {"error": "No template selected"}
            filepath = self.current_tpl_dir / args["filename"]
            if not filepath.exists():
                return {"error": f"File not found: {args['filename']}"}
            return {"content": filepath.read_text(encoding="utf-8")}

        @tool("write_file", "Write content to a file in the current template directory",
              {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]})
        async def write_file(args: dict) -> dict:
            if not self.current_tpl_dir:
                return {"error": "No template selected"}
            filepath = self.current_tpl_dir / args["filename"]
            filepath.write_text(args["content"], encoding="utf-8")
            return {"success": f"Wrote to {args['filename']}"}
        
        self.mcp_server = create_sdk_mcp_server("PhotoSuitCopilot", "1.0", tools=[read_file, write_file])

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def set_template(self, tpl_id: str, tpl_dir: Path):
        self.current_tpl_id = tpl_id
        self.current_tpl_dir = tpl_dir
        
    def send_message(self, text: str, image_path: Optional[str] = None):
        asyncio.run_coroutine_threadsafe(self._do_query(text, image_path), self.loop)
        
    async def _do_query(self, text: str, image_path: Optional[str] = None):
        if not self.current_tpl_id:
            self.callback("system", "请先在左侧选择或新建一个模板。")
            return
            
        if not os.environ.get("ANTHROPIC_API_KEY"):
            self.callback("system", "请点击设置按钮配置 ANTHROPIC_API_KEY 环境变量以连接 AI。")
            return

        prompt: Union[str, List[Dict[str, Any]]] = text
        
        if image_path and os.path.exists(image_path):
            # Encode image to base64 for Claude
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/jpeg"
            with open(image_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            
            prompt = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64_data,
                    }
                },
                {
                    "type": "text",
                    "text": text
                }
            ]

        # Use the template ID as the resume session identifier
        session_id = f"photosuit_tpl_{self.current_tpl_id}"

        env_dict = {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "")
        }
        if os.environ.get("ANTHROPIC_BASE_URL"):
            env_dict["ANTHROPIC_BASE_URL"] = os.environ.get("ANTHROPIC_BASE_URL")

        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"CopilotTools": self.mcp_server},
            tools=["read_file", "write_file"],
            cwd=self.current_tpl_dir,
            resume=session_id,
            env=env_dict,
        )
        
        try:
            # We already stream to UI what user sent, skip user echo here
            full_reply = ""
            
            async for event in query(prompt=prompt, options=options):
                if isinstance(event, AssistantMessage):
                    for content in event.content:
                        if hasattr(content, "text") and content.text:
                            full_reply += content.text
                            # Update UI
                            self.callback("assistant_partial", full_reply)
                            
            # Finalize
            if full_reply:
                self.callback("assistant", full_reply)
            else:
                self.callback("system", "Agent completed operation.")
                
        except Exception as e:
            self.callback("system", f"AI 连接错误: {str(e)}")
