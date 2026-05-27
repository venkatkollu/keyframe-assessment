import asyncio
import os
import sys
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test_mcp():
    # 1. Define server parameters to run our mcp_server.py in the virtual environment
    python_exe = os.path.abspath(".venv/Scripts/python.exe")
    server_params = StdioServerParameters(
        command=python_exe,
        args=["app/mcp_server.py"],
    )

    print("=== Testing MCP Server over stdio ===")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Handshake
            await session.initialize()
            print("Handshake initialized successfully.")

            # List tools
            tools_response = await session.list_tools()
            tools = tools_response.tools
            print(f"Discovered {len(tools)} tool(s).")
            for t in tools:
                print(f"  Tool Name: {t.name}")
                print(f"  Description: {t.description}")
                print(f"  Schema: {t.inputSchema}")
            
            assert len(tools) > 0, "No tools discovered"
            assert tools[0].name == "transcribe_media", "Expected tool 'transcribe_media' not found"

            # Call the tool using the Big Buck Bunny mp4 url
            print("\nCalling tool 'transcribe_media'...")
            result = await session.call_tool(
                name="transcribe_media",
                arguments={"url": "https://www.w3schools.com/html/mov_bbb.mp4"}
            )
            
            print(f"Response content count: {len(result.content)}")
            text_content = result.content[0]
            print("Response content text snippet:")
            print(text_content.text[:500] + "...")
            
            # Verify the response contains expected fields
            res_dict = json.loads(text_content.text)
            print("Response successfully parsed as JSON!")
            print(f"audioMode: {res_dict.get('audioMode')}")
            assert "text" in res_dict, "Result missing 'text' field"
            
    print("\nALL MCP TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_mcp())
