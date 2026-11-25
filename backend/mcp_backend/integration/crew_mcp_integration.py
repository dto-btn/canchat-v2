#!/usr/bin/env python3
"""
CrewAI MCP Integration for Open WebUI.
"""

import os
import re
import logging
from pathlib import Path
from pydantic import BaseModel

# Disable CrewAI telemetry to avoid connection timeout errors
os.environ["OTEL_SDK_DISABLED"] = "true"

from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import BaseTool
from crewai_tools.adapters.mcp_adapter import CrewAIToolAdapter
from mcp_backend.management.builtin_sessions import BuiltinTool, builtin_sessions

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CrewAI verbosity control - disable in production for cleaner logs
CREW_VERBOSE = os.getenv("CREW_VERBOSE", "false").lower() == "true"


def _extract_token_usage(crew_output) -> dict:
    """
    Extract token usage from a CrewAI CrewOutput object.
    Returns a dict with prompt_tokens, completion_tokens, total_tokens.
    Returns empty totals if the information is unavailable.
    """
    try:
        usage = getattr(crew_output, "token_usage", None)
        if usage is not None:
            return {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
    except Exception as exc:
        logger.warning(f"Could not extract token usage from crew output: {exc}")
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# Azure OpenAI Configuration
class AzureConfig(BaseModel):
    """Azure OpenAI configuration from environment variables"""

    api_key: str = os.getenv("CREWAI_AZURE_OPENAI_API_KEY", "")
    endpoint: str = os.getenv("CREWAI_AZURE_OPENAI_ENDPOINT", "")
    deployment: str = os.getenv("CREWAI_AZURE_OPENAI_DEPLOYMENT_NAME", "")
    api_version: str = os.getenv("CREWAI_AZURE_OPENAI_API_VERSION", "")
    api_mode: str = os.getenv("CREWAI_AZURE_OPENAI_API_MODE", "legacy")

    def validate_config(self) -> bool:
        """Validate that required Azure configuration is present"""
        if not self.api_key or not self.endpoint:
            logger.error(
                "Missing required Azure OpenAI configuration. Please set CREWAI_AZURE_OPENAI_API_KEY and CREWAI_AZURE_OPENAI_ENDPOINT environment variables."
            )
            return False
        return True


class CrewMCPManager:
    """Manager for CrewAI with MCP integration"""

    def __init__(self):
        self.azure_config = AzureConfig()
        if not self.azure_config.validate_config():
            raise ValueError("Invalid Azure OpenAI configuration")
        self.backend_dir = Path(__file__).parent.parent.parent  # Go up to backend dir
        self.time_server_path = (
            self.backend_dir / "mcp_backend" / "servers" / "fastmcp_time_server.py"
        )
        self.news_server_path = (
            self.backend_dir / "mcp_backend" / "servers" / "fastmcp_news_server.py"
        )
        # Discover SharePoint server scripts dynamically.
        # Any *_sharepoint_server.py in the servers directory is a department server.
        self._sharepoint_server_paths = self._discover_sharepoint_servers()

    def _discover_sharepoint_servers(self) -> dict:
        """Read SHAREPOINT_DEPARTMENTS env var to determine active departments.

        Returns a dict mapping uppercase department key → Path to the generic
        server script, e.g. {"MPO": Path(".../generic_sharepoint_server_multi_dept.py")}

        ADDING A NEW SHAREPOINT DEPARTMENT — zero code changes required here.
        Add the prefix to the SHAREPOINT_DEPARTMENTS env var (comma-separated)
        and set the {DEPT}_SHP_* environment variables. No application files
        need to be created or modified.
        """
        generic_server_path = (
            self.backend_dir
            / "mcp_backend"
            / "servers"
            / "generic_sharepoint_server_multi_dept.py"
        )
        departments_env = os.getenv("SHAREPOINT_DEPARTMENTS", "")
        result = {}
        for dept in departments_env.split(","):
            dept = dept.strip().upper()
            if dept:
                result[dept] = generic_server_path
                logger.info(f"Configured SharePoint department: {dept}")
        if not result:
            logger.warning(
                "SHAREPOINT_DEPARTMENTS is not set — SharePoint CrewAI crews will be unavailable. "
                "Set SHAREPOINT_DEPARTMENTS=MPO,PMO (comma-separated) to enable SharePoint integration."
            )
        return result

    def cleanup_mcp_adapters(self):
        """Cleanup all MCP server adapters on shutdown"""
        builtin_sessions.cleanup()

    @staticmethod
    def _crewai_tools(tools: list[BuiltinTool]) -> list[BaseTool]:
        adapter = CrewAIToolAdapter()
        return [adapter.adapt(tool.call, tool.definition) for tool in tools]

    def get_azure_llm_config(self) -> LLM:
        """Get Azure OpenAI LLM configuration for CrewAI"""
        if self.azure_config.api_mode == "v1":
            return LLM(
                model=f"openai/{self.azure_config.deployment}",
                base_url=f"{self.azure_config.endpoint.rstrip('/')}/openai/v1/",
                api_key=self.azure_config.api_key,
                timeout=30,
            )
        if self.azure_config.api_mode != "legacy":
            raise ValueError("CREWAI_AZURE_OPENAI_API_MODE must be 'legacy' or 'v1'")

        os.environ["AZURE_API_KEY"] = self.azure_config.api_key
        os.environ["AZURE_API_BASE"] = self.azure_config.endpoint
        os.environ["AZURE_API_VERSION"] = self.azure_config.api_version

        return LLM(
            model=f"azure/{self.azure_config.deployment}",
            timeout=30,
        )

    def run_time_crew(
        self, query: str = "What's the current time?"
    ) -> tuple[str, dict[str, int]]:
        with builtin_sessions.session("time_server") as tools:
            return self._run_time_crew(query, self._crewai_tools(tools))

    def _run_time_crew(
        self, query: str, mcp_tools: list[BaseTool]
    ) -> tuple[str, dict[str, int]]:
        """
        Run a CrewAI crew with MCP time server tools

        Args:
            query: The time-related query to process

        Returns:
            The crew's response and token usage.
        """
        if not self.time_server_path.exists():
            raise FileNotFoundError(f"Time server not found at {self.time_server_path}")

        logger.info(f"Starting CrewAI MCP integration for query: {query}")

        try:
            logger.info(f"Available MCP tools: {[tool.name for tool in mcp_tools]}")

            # Create Azure OpenAI LLM
            llm = self.get_azure_llm_config()

            # Create time specialist agent with MCP tools
            time_agent = Agent(
                role="Time Specialist",
                goal="Provide accurate time information using available time tools",
                backstory="I am an AI specialist focused on time-related queries. I have access to time tools via MCP and can provide current time in various formats and timezones.",
                tools=mcp_tools,  # Pass MCP tools to agent
                llm=llm,
                verbose=CREW_VERBOSE,
                max_iter=3,  # Limit iterations to prevent excessive thinking
            )

            # Create task for time query
            time_task = Task(
                description=f"Process this time-related query: {query}. Use the available time tools to get accurate information.",
                expected_output="A clear and accurate response to the time query with proper formatting.",
                agent=time_agent,
            )

            # Create and execute crew
            time_crew = Crew(
                agents=[time_agent],
                tasks=[time_task],
                process=Process.sequential,
                verbose=CREW_VERBOSE,
            )

            # Execute the crew
            logger.info("Executing CrewAI crew...")
            result = time_crew.kickoff()
            logger.info("CrewAI crew execution completed successfully")

            # Extract token usage from the crew result
            token_usage = _extract_token_usage(result)
            return str(result), token_usage

        except Exception as e:
            logger.error(f"Error in CrewAI MCP integration: {e}")
            raise

    def run_news_crew(
        self, query: str = "Get the latest news headlines"
    ) -> tuple[str, dict[str, int]]:
        with builtin_sessions.session("news_server") as tools:
            return self._run_news_crew(query, self._crewai_tools(tools))

    def _run_news_crew(
        self, query: str, mcp_tools: list[BaseTool]
    ) -> tuple[str, dict[str, int]]:
        """
        Run a CrewAI crew with MCP news server tools

        Args:
            query: The news-related query to process

        Returns:
            The crew's response and token usage.
        """
        if not self.news_server_path.exists():
            raise FileNotFoundError(f"News server not found at {self.news_server_path}")

        logger.info(f"Starting CrewAI MCP news integration for query: {query}")

        try:
            logger.info(
                f"Available MCP news tools: {[tool.name for tool in mcp_tools]}"
            )

            # Create Azure OpenAI LLM
            llm = self.get_azure_llm_config()

            # Create news specialist agent with MCP tools
            news_agent = Agent(
                role="News Specialist",
                goal="Provide current news information exactly as returned by news tools, preserving original formatting and language",
                backstory="I am an AI specialist focused on news and current events. I have access to NewsDesk via MCP tools and can fetch the latest headlines. My role is to pass through the news information exactly as provided by the tools, preserving all formatting, emojis, language (French/English), and structure without translation or summarization.",
                tools=mcp_tools,  # Pass MCP tools to agent
                llm=llm,
                verbose=CREW_VERBOSE,
                max_iter=5,  # Limit iterations to prevent excessive thinking
            )

            # Create task for news query
            news_task = Task(
                description=f"Process this news-related query: {query}. Use the available news tools to get current headlines and relevant information. If the query asks for specific topics, filter or search for relevant articles.",
                expected_output="Return the news information EXACTLY as provided by the news tools, preserving all original formatting, emojis, language, and structure. Do NOT translate, summarize, or reformat the content. Simply pass through the original response from the news tools with minimal additional commentary.",
                agent=news_agent,
            )

            # Create and execute crew
            news_crew = Crew(
                agents=[news_agent],
                tasks=[news_task],
                process=Process.sequential,
                verbose=CREW_VERBOSE,
            )

            # Execute the crew
            logger.info("Executing CrewAI news crew...")
            result = news_crew.kickoff()

            # Extract token usage from the crew result
            token_usage = _extract_token_usage(result)
            return str(result), token_usage

        except Exception as e:
            logger.error(f"Error in CrewAI MCP news integration: {e}")
            raise

    def run_sharepoint_crew(
        self,
        department: str,
        query: str = "Search SharePoint documents",
        user_access_token: str | None = None,
    ) -> tuple[str, dict[str, int]]:
        """
        Run a CrewAI crew with MCP SharePoint server tools for a given department.

        Args:
            department: Department key, e.g. "MPO" or "PMO".  Case-insensitive.
            query: The SharePoint-related query to process.
            user_access_token: Request-scoped token for delegated access.

        Returns:
            (result_str, token_usage) tuple.
        """
        dept_key = department.upper()
        dept_lower = dept_key.lower()

        server_path = self._sharepoint_server_paths.get(dept_key)
        if not server_path or not server_path.exists():
            raise FileNotFoundError(
                f"{dept_key} SharePoint server not found at {server_path}"
            )

        logger.info(f"Starting CrewAI MCP SharePoint integration for query: {query}")
        logger.info(f"Using {dept_key} SharePoint server: {server_path}")
        logger.info("🔄 Initializing fresh SharePoint adapter for this request")

        session = builtin_sessions.session(
            f"{dept_lower}_sharepoint_server", user_access_token
        )
        session_entered = False
        try:
            tools = session.__enter__()
            session_entered = True
            mcp_tools = self._crewai_tools(tools)

            logger.info(
                f"✅ {dept_key} SharePoint adapter initialized with {len(mcp_tools)} tools"
            )
            logger.info(
                f"Available MCP SharePoint tools: {[tool.name for tool in mcp_tools]}"
            )

            # Create Azure OpenAI LLM
            llm = self.get_azure_llm_config()

            # Create SharePoint specialist agent with MCP tools
            sharepoint_agent = Agent(
                role="SharePoint Document Specialist",
                goal="Find and retrieve relevant information from SharePoint using FAST search or list files in specific folders",
                backstory=f"""I am a SharePoint document specialist who uses the Microsoft Graph Search API for lightning-fast document searches and folder listings.

    🚨 CRITICAL RULES:
    1. For LISTING/COUNTING files in a specific folder → Use {dept_lower}_list_folder_contents
    2. For SEARCHING content across documents → Use {dept_lower}_search_documents_fast
    3. If search snippet doesn't have the complete answer, use {dept_lower}_get_document_by_id to get full content
    4. NEVER answer from training data - ONLY from SharePoint results
    5. If no documents found, say so - don't make up answers
    
    RESPONSE FORMAT:
    - Provide ONLY the final answer extracted from SharePoint
    - Do NOT show your reasoning process ("Thought:", "Action:", etc.)
    - Include document name and source when applicable
    - Be direct and concise""",
                tools=mcp_tools,
                llm=llm,
                verbose=CREW_VERBOSE,
                max_iter=5,
                allow_delegation=False,
            )

            # Create SharePoint task with smart routing
            sharepoint_task = Task(
                description=f"""Retrieve information from SharePoint for: {query}

    🔍 STEP 1 - DETERMINE REQUEST TYPE:
    
    A) If asking to LIST/COUNT files in a SPECIFIC FOLDER (keywords: "list", "show files", "count", "how many files", "in the folder", "in folder"):
       → Use {dept_lower}_list_folder_contents
       → Extract folder name from query (e.g., "Canchat Demo", "Documents/Reports")
       → Call: {dept_lower}_list_folder_contents(folder_path="<folder_name>")
       → If successful: Return the list of files found
       → If folder not found: FALLBACK to {dept_lower}_search_documents_fast(query="<folder_name>", limit=10) to find documents
    
    B) If asking for CONTENT/INFORMATION ABOUT something (keywords: "tell me about", "what is", "explain", "information about", "details on"):
       → STEP 1: Use {dept_lower}_search_documents_fast
         • Call: {dept_lower}_search_documents_fast(query="{query}", limit=10)
       
       → STEP 2: Analyze results:
         • If ANY FILES found (is_folder=false): GO TO STEP 3
         • If ONLY folders found (all is_folder=true): GO TO STEP 4
       
       → STEP 3: Process FILES:
         • If file has useful summary: Extract and respond
         • If need full content: Use {dept_lower}_get_document_by_id(web_url=..., item_id=...)
         • DONE - provide answer to user
       
       → STEP 4: MANDATORY REFINED SEARCH (when only folders found):
         • Try search with hyphenated version: {dept_lower}_search_documents_fast(query="Budget-2025", limit=10)
         • If no files, try: {dept_lower}_search_documents_fast(query="Budget 2025 pdf", limit=10)
         • If files found: Use {dept_lower}_get_document_by_id to retrieve content
         • If still no files after 2 refined searches: Tell user no document files found, only folders exist
       
       → NEVER try to retrieve folder content with {dept_lower}_get_document_by_id

    🚨 ABSOLUTE RULES:
    - For "Tell me about X" queries: Prioritize finding DOCUMENT CONTENT, not folder structure
    - Search returns folders + files: Use the FILES first, ignore folders unless no files exist
    - Search returns ONLY folders: Try refined search with specific terms before listing folder contents
    - Only use {dept_lower}_get_document_by_id for actual FILES (is_folder=false)
    - Do NOT retry the same tool with same input more than once
    - Include document names and sources in your final answer
    - If you get an error suggesting to use search, DO IT - don't give up
    - If you receive a permission/access denied error (🔒), STOP and tell the user they don't have access
    - Do NOT say "no information found" when it's actually a permissions issue
    - 404 errors on folders mean you tried to get a folder as a document - use {dept_lower}_list_folder_contents instead
                """,
                expected_output="""A DIRECT, CONCISE answer to the user's question without showing any reasoning process.

    FORMAT REQUIREMENTS:
    - Start immediately with the answer (NO "Thought:", "Action:", or process explanations)
    - Extract the key information from the search results
    - Include document name and source for credibility
    - Keep response focused on what was asked
    - Maximum 2-3 sentences unless more detail is specifically requested

    EXAMPLE RESPONSES:
    - Success: "Canada's new high-speed railway is proposed to span approximately 1,000 km, according to the document '2025-12-05-Alto-Letter to MPO.pdf' from the Major Projects Office."
    - Permission Error: "You do not have permission to access the requested documents. Please contact your SharePoint administrator for access."
    - Not Found: "No documents found matching your query in accessible SharePoint locations."

    AVOID:  
    - Showing reasoning ("Thought:", "I will use tool X")
    - Dumping entire document contents
    - Being overly verbose
    - Including process descriptions
    - Saying "no information found" when it's a permissions issue""",
                agent=sharepoint_agent,
            )

            # Create and execute crew
            sharepoint_crew = Crew(
                agents=[sharepoint_agent],
                tasks=[sharepoint_task],
                process=Process.sequential,
                verbose=CREW_VERBOSE,
            )

            # Execute the crew
            logger.info(f"Executing CrewAI {dept_key} SharePoint crew...")
            result = sharepoint_crew.kickoff()

            token_usage = _extract_token_usage(result)
            return str(result), token_usage

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"Error in CrewAI MCP {dept_key} SharePoint integration: {error_msg}"
            )

            # Provide specific error messages based on the type of failure
            if (
                "Authentication" in error_msg
                or "access token" in error_msg
                or "401" in error_msg
            ):
                return (
                    "SharePoint access failed due to authentication issues. This may be because:\n\n"
                    + "1. You're in a local development environment where OAuth2 proxy is not configured\n"
                    + "2. Your authentication token has expired\n"
                    + "3. You don't have the necessary SharePoint permissions\n\n"
                    + "For local development, SharePoint integration requires deployment to environments with proper OAuth2 configuration (dev/staging/production).",
                    {},
                )
            elif "No documents found" in error_msg or "no results" in error_msg:
                return (
                    "I searched the available SharePoint documents but could not find information related to your query. The documents may not contain this information, or it might be located in a different location.",
                    {},
                )
            else:
                return (
                    f"I encountered an issue while searching SharePoint documents: {error_msg}. Please try rephrasing your query or contact support if the problem persists.",
                    {},
                )
        finally:
            if session_entered:
                session.__exit__(None, None, None)

    def run_intelligent_crew(
        self,
        query: str,
        selected_tools: list = None,
        user_access_token: str | None = None,
    ) -> tuple:
        """
        Route a query to the appropriate specialist crew based on selected_tools.

        Args:
            query: The query to process.
            selected_tools: List of tool IDs selected by the user (optional).
            user_access_token: Request-scoped token for delegated SharePoint access.

        Returns:
            A 3-tuple of (result_str, token_usage_dict, mcp_process_name).
        """
        logger.info(f"Starting intelligent router for query: {query}")
        logger.info(f"Selected tools: {selected_tools}")

        try:
            # Determine available specialists based on selected tools
            available_specialists = []
            if selected_tools:
                if any("time" in tool.lower() for tool in selected_tools):
                    available_specialists.append("TIME")
                if any(
                    "news" in tool.lower() or "headline" in tool.lower()
                    for tool in selected_tools
                ):
                    available_specialists.append("NEWS")
                # Detect any department SharePoint server dynamically.
                # Tool IDs follow the pattern "mcp_{dept}_sharepoint_server_*".
                for tool in selected_tools:
                    m = re.match(r"mcp_([a-z0-9]+)_sharepoint_server", tool.lower())
                    if m:
                        dept = m.group(1).upper()
                        specialist_key = f"{dept}_SHAREPOINT"
                        if specialist_key not in available_specialists:
                            available_specialists.append(specialist_key)
            else:
                # If no tools selected, only non-SharePoint specialists are available by default
                available_specialists = ["TIME", "NEWS"]

            logger.info(f"Available specialists: {available_specialists}")

            # FAST PATH: If user explicitly selected tools for a single specialist,
            # skip router overhead and go straight to the specialist crew.
            if len(available_specialists) == 1:
                specialist = available_specialists[0]
                logger.info(
                    f"🚀 FAST PATH: Single specialist detected ({specialist}), skipping router crew overhead"
                )

                if specialist == "TIME":
                    result_str, token_usage = self.run_time_crew(query)
                    return result_str, token_usage, "time_server"
                elif specialist == "NEWS":
                    result_str, token_usage = self.run_news_crew(query)
                    return result_str, token_usage, "news_server"
                elif specialist.endswith("_SHAREPOINT"):
                    dept = specialist[: -len("_SHAREPOINT")]
                    result_str, token_usage = self.run_sharepoint_crew(
                        dept, query, user_access_token
                    )
                    return result_str, token_usage, f"{dept.lower()}_sharepoint_server"

            # Multiple specialists - simple priority routing
            logger.info("Multiple specialists available - using simple routing")

            if "TIME" in available_specialists:
                result_str, token_usage = self.run_time_crew(query)
                return result_str, token_usage, "time_server"
            elif "NEWS" in available_specialists:
                result_str, token_usage = self.run_news_crew(query)
                return result_str, token_usage, "news_server"
            else:
                # Route to the first available SharePoint specialist
                for specialist in available_specialists:
                    if specialist.endswith("_SHAREPOINT"):
                        dept = specialist[: -len("_SHAREPOINT")]
                        result_str, token_usage = self.run_sharepoint_crew(
                            dept, query, user_access_token
                        )
                        return (
                            result_str,
                            token_usage,
                            f"{dept.lower()}_sharepoint_server",
                        )

            return (
                "I apologize, but I was unable to process your request at this time.",
                {},
                None,
            )

        except Exception as e:
            logger.error(f"Error in intelligent routing: {e}")
            # Emergency fallback - route based on selected tools only
            logger.info("Using emergency fallback routing...")

            if selected_tools:
                has_time = any("time" in tool.lower() for tool in selected_tools)
                has_news = any(
                    "news" in tool.lower() or "headline" in tool.lower()
                    for tool in selected_tools
                )

                if has_time:
                    logger.info("Emergency fallback: TIME")
                    result_str, token_usage = self.run_time_crew(query)
                    return result_str, token_usage, "time_server"
                elif has_news:
                    logger.info("Emergency fallback: NEWS")
                    result_str, token_usage = self.run_news_crew(query)
                    return result_str, token_usage, "news_server"
                else:
                    # Try SharePoint fallback
                    for tool in selected_tools:
                        m = re.match(r"mcp_([a-z0-9]+)_sharepoint_server", tool.lower())
                        if m:
                            dept = m.group(1).upper()
                            logger.info(f"Emergency fallback: {dept} SHAREPOINT")
                            result_str, token_usage = self.run_sharepoint_crew(
                                dept, query, user_access_token
                            )
                            return (
                                result_str,
                                token_usage,
                                f"{dept.lower()}_sharepoint_server",
                            )

            logger.info("Emergency fallback: TIME (default)")
            result_str, token_usage = self.run_time_crew(query)
            return result_str, token_usage, "time_server"

    def _combine_specialist_responses(
        self, responses: list, original_query: str
    ) -> str:
        """
        Combine multiple specialist responses in a structured way that maintains
        compatibility with Open WebUI's chat naming and tagging systems.
        """
        logger.info(f"Combining {len(responses)} specialist responses")

        # For now, use a simple structured combination that preserves key information
        # This should work better with Open WebUI's automated analysis systems

        if len(responses) == 1:
            return responses[0]

        # Create a structured combination with clear sections
        combined_parts = []

        for i, response in enumerate(responses):
            # Add each response with minimal formatting
            if i == 0:
                combined_parts.append(response.strip())
            else:
                combined_parts.append(f"\n{response.strip()}")

        # Join with double line breaks for clear separation
        return "\n\n".join(combined_parts)

    def get_available_servers(self) -> dict:
        """Get all available MCP servers dynamically"""
        servers = {}

        # Add any other fastmcp_*.py servers discovered in the servers directory
        servers_dir = self.backend_dir / "mcp_backend" / "servers"
        for server_file in servers_dir.glob("fastmcp_*.py"):
            server_name = server_file.stem
            servers[server_name] = server_file
            logger.info(f"Discovered additional MCP server: {server_name}")

        # Fixed known servers
        known_servers = {
            "time_server": self.time_server_path,
            "news_server": self.news_server_path,
        }
        # Dynamically discovered SharePoint servers
        for dept_key, path in self._sharepoint_server_paths.items():
            known_servers[f"{dept_key.lower()}_sharepoint_server"] = path

        servers.update(known_servers)

        # Filter to only existing servers
        return {name: path for name, path in servers.items() if path.exists()}

    def get_available_tools(self) -> list:
        """Discover built-in tools through the shared session owner."""
        all_tools = []

        built_in_servers = {
            "time_server": self.time_server_path,
            "news_server": self.news_server_path,
        }
        for server_name, server_path in built_in_servers.items():
            if not server_path.exists():
                continue
            try:
                all_tools.extend(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "server": server_name,
                    }
                    for tool in builtin_sessions.describe(server_name)
                )
            except Exception:
                logger.exception("Error getting tools from %s", server_name)

        for dept_key, server_path in self._sharepoint_server_paths.items():
            server_name = f"{dept_key.lower()}_sharepoint_server"
            try:
                all_tools.extend(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "server": server_name,
                    }
                    for tool in builtin_sessions.describe(server_name)
                )
            except Exception:
                logger.exception("Error getting tools from %s", server_name)

        return all_tools


# Simple CLI interface for testing
def main():
    """Main function for testing the CrewAI MCP integration"""
    manager = CrewMCPManager()

    print("🚀 Starting CrewAI MCP Integration Test")
    print("=" * 50)

    try:
        # Test server discovery
        print("\n📡 Available MCP Servers:")
        available_servers = manager.get_available_servers()
        for server_name, server_path in available_servers.items():
            print(f"   ✓ {server_name}: {server_path}")

        # Test getting available tools first
        tools = manager.get_available_tools()
        print(f"\n📋 Available MCP Tools: {len(tools)}")
        for tool in tools:
            print(f"   - {tool['name']} ({tool['server']}): {tool['description']}")

        if not tools:
            print("❌ No MCP tools available. Check FastMCP servers.")
            return

        # Test individual server capabilities
        if any(tool["server"] == "time_server" for tool in tools):
            print("\n🕐 Testing Time Server:")
            print("Query: 'What's the current time in UTC?'")
            result = manager.run_time_crew("What's the current time in UTC?")
            print("Result:", result[:200] + "..." if len(result) > 200 else result)

        if any(tool["server"] == "news_server" for tool in tools):
            print("\n📰 Testing News Server:")
            print("Query: 'Get the latest news headlines'")
            result = manager.run_news_crew("Get the latest news headlines")
            print("Result:", result[:200] + "..." if len(result) > 200 else result)

        print("\n✅ All tests completed successfully!")

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        manager.cleanup_mcp_adapters()


if __name__ == "__main__":
    main()
