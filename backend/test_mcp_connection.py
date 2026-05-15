import asyncio
import os
import sys
from mcp import StdioServerParameters
from langchain_mcp_adapters.tools import load_mcp_tools

async def test_mcp():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(current_dir, "chatbot", "mcp_server.py")
    
    print(f"Testing MCP server at: {server_script}")
    print(f"Using executable: {sys.executable}")
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[server_script],
        env=os.environ.copy()
    )
    
    try:
        print("Loading MCP tools...")
        # Use the correct signature for langchain-mcp-adapters 0.1.x
        mcp_tools = await load_mcp_tools(
            None, 
            connection={
                "transport": "stdio",
                "command": sys.executable,
                "args": [server_script],
                "env": os.environ.copy()
            }
        )
        print(f"Tools found: {[t.name for t in mcp_tools]}")
        
        search_tool = next((t for t in mcp_tools if t.name == "web_search"), None)
        if search_tool:
            print("Invoking web_search tool for 'weather in Tunisia'...")
            result = await search_tool.ainvoke({"query": "weather in Tunisia"})
            print(f"Result: {str(result)[:500]}...")
        else:
            print("Error: web_search tool not found!")
            
    except Exception as e:
        print(f"MCP Test Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_mcp())
