#!/usr/bin/env python3
"""
SharePoint OAuth2 Client with On-Behalf-Of (OBO) Flow Support

This module handles OAuth2 authentication for SharePoint access, supporting both:
1. Application authentication (client credentials flow)
2. User-delegated authentication (on-behalf-of flow)

The OBO flow allows the MCP server to act on behalf of authenticated users,
respecting their individual SharePoint permissions.
"""

import os
import asyncio
import aiohttp
import json
import logging
import base64
import time
from typing import Dict, Any, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger("sharepoint_oauth")


class SharePointOAuthClient:
    """OAuth2 client for SharePoint with On-Behalf-Of flow support"""

    def __init__(
        self,
        client_id: str = None,
        client_secret: str = None,
        tenant_id: str = None,
        site_url: str = None,
        use_delegated_access: bool = None,
        obo_scope: str = None,
    ):
        """
        Initialize the OAuth client with configuration.

        Args:
            client_id: Azure AD application client ID (if None, uses SHP_ID_APP env var)
            client_secret: Azure AD application client secret (if None, uses SHP_ID_APP_SECRET env var)
            tenant_id: Azure AD tenant ID (if None, uses SHP_TENANT_ID env var)
            site_url: SharePoint site URL (if None, uses SHP_SITE_URL env var)
            use_delegated_access: Enable OBO flow (if None, uses SHP_USE_DELEGATED_ACCESS env var)
            obo_scope: OBO token scopes (if None, uses SHP_OBO_SCOPE env var)
        """
        # Support both direct parameters and environment variables for backward compatibility
        self.client_id = client_id or os.getenv("SHP_ID_APP")
        self.client_secret = client_secret or os.getenv("SHP_ID_APP_SECRET")
        self.tenant_id = tenant_id or os.getenv("SHP_TENANT_ID")
        self.site_url = site_url or os.getenv("SHP_SITE_URL")

        # Global settings with defaults - check environment override for testing
        env_delegated = os.getenv("SHP_USE_DELEGATED_ACCESS", "true").lower()
        self.use_delegated_access = (
            use_delegated_access
            if use_delegated_access is not None
            else (env_delegated in ("true", "1", "yes"))
        )

        self.obo_scope = obo_scope or os.getenv(
            "SHP_OBO_SCOPE",
            "https://graph.microsoft.com/Sites.Read.All https://graph.microsoft.com/Files.Read.All",
        )

        # Validation - different requirements based on access mode
        if self.use_delegated_access:
            # For delegated access, only site_url is required
            if not self.site_url:
                raise ValueError(
                    "Missing required SharePoint site URL for delegated access"
                )
        else:
            # For application access, all credentials are required
            if not all(
                [self.client_id, self.client_secret, self.tenant_id, self.site_url]
            ):
                raise ValueError(
                    "Missing required SharePoint OAuth configuration for application access"
                )

        # OAuth2 endpoints
        self.token_endpoint = (
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        )
        self.graph_base_url = "https://graph.microsoft.com/v1.0"

        logger.info(
            f"Initialized SharePoint OAuth client (delegated: {self.use_delegated_access})"
        )

    async def get_obo_token(self, user_access_token: str) -> Optional[str]:
        """
        Get on-behalf-of access token for SharePoint access

        Args:
            user_access_token: User's access token from CANchat authentication

        Returns:
            OBO access token string or None if failed
        """
        try:
            token_data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "assertion": user_access_token,
                "scope": self.obo_scope,
                "requested_token_use": "on_behalf_of",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.token_endpoint, data=token_data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        access_token = result.get("access_token")
                        logger.info("Successfully obtained OBO token")
                        return access_token
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"OBO token request failed: {response.status} - {error_text}"
                        )
                        return None

        except Exception as e:
            logger.error(f"Error obtaining OBO token: {e}")
            return None

    async def get_application_token(self) -> Optional[str]:
        """
        Get application access token using client credentials flow.

        Returns:
            Access token string or None if failed
        """
        try:
            async with aiohttp.ClientSession() as session:
                token_data = {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                }

                async with session.post(
                    self.token_endpoint, data=token_data
                ) as response:
                    if response.status == 200:
                        token_response = await response.json()
                        access_token = token_response.get("access_token")

                        if access_token:
                            # Suppress verbose logging during parallel processing
                            return access_token
                        else:
                            logger.error("No access token in response")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Token request failed: {response.status} - {error_text}"
                        )
                        return None

        except Exception as e:
            logger.error(f"Error obtaining application token: {e}")
            return None

    async def get_access_token(self, user_token: Optional[str] = None) -> Optional[str]:
        """
        Get appropriate access token based on configuration.
        Supports both delegated access (OBO flow) and application access.

        Args:
            user_token: Optional user access token for OBO flow

        Returns:
            Access token string or None if failed
        """
        if self.use_delegated_access:
            # Delegated access requires user token
            if (
                not user_token
                or user_token == "user_token_placeholder"
                or not user_token.strip()
            ):
                logger.error(
                    "No valid user token provided for OBO flow. Delegated access requires user authentication."
                )
                return None

            logger.info("Attempting delegated access (OBO flow)")
            obo_token = await self.get_obo_token(user_token)

            if not obo_token:
                logger.error(
                    "OBO flow failed. User may not have appropriate SharePoint permissions."
                )
                return None

            return obo_token
        else:
            # Application access - use client credentials flow
            logger.info("Attempting application access (client credentials flow)")
            return await self.get_application_token()

    def get_site_identifier(self) -> str:
        """
        Extract site identifier from SharePoint URL for Graph API calls

        Returns:
            Site identifier in format: hostname:path
        """
        from urllib.parse import urlparse

        parsed = urlparse(self.site_url)
        hostname = parsed.netloc
        path = parsed.path

        return f"{hostname}:{path}"

    async def make_graph_request(
        self,
        endpoint: str,
        access_token: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Make authenticated request to Microsoft Graph API

        Args:
            endpoint: Graph API endpoint (relative to base URL)
            access_token: Access token for authentication
            method: HTTP method (GET, POST, etc.)
            data: Optional request body data

        Returns:
            Tuple of (success, response_data)
        """
        try:
            url = f"{self.graph_base_url}/{endpoint.lstrip('/')}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                kwargs = {"headers": headers}
                if data:
                    kwargs["json"] = data

                async with session.request(method, url, **kwargs) as response:
                    if response.status in [200, 201, 204]:
                        try:
                            result = await response.json()
                        except:
                            result = {"status": "success"}
                        logger.debug(
                            f"Graph API request successful: {method} {endpoint}"
                        )
                        return True, result
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Graph API request failed: {response.status} - {error_text}"
                        )
                        return False, {
                            "error": error_text,
                            "status_code": response.status,
                        }

        except Exception as e:
            logger.error(f"Error making Graph API request: {e}")
            return False, {"error": str(e)}

    async def get_site_info(self, access_token: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Get SharePoint site information

        Args:
            access_token: Access token for authentication

        Returns:
            Tuple of (success, site_info)
        """
        site_id = self.get_site_identifier()
        endpoint = f"sites/{site_id}"

        return await self.make_graph_request(endpoint, access_token)

    async def get_site_drives(
        self, access_token: str, site_id: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Get document libraries (drives) for a SharePoint site

        Args:
            access_token: Access token for authentication
            site_id: Optional specific site ID (uses configured site if not provided)

        Returns:
            Tuple of (success, drives_data)
        """
        if not site_id:
            # Get site info first to get site ID
            success, site_info = await self.get_site_info(access_token)
            if not success:
                return False, site_info
            site_id = site_info.get("id")

        if not site_id:
            return False, {"error": "Could not determine site ID"}

        endpoint = f"sites/{site_id}/drives"
        return await self.make_graph_request(endpoint, access_token)

    async def get_drive_items(
        self,
        access_token: str,
        site_id: Optional[str] = None,
        drive_id: Optional[str] = None,
        folder_path: str = "",
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Get items (files/folders) from a SharePoint drive

        Args:
            access_token: Access token for authentication
            site_id: SharePoint site ID
            drive_id: Drive (document library) ID
            folder_path: Path within the drive (empty for root)

        Returns:
            Tuple of (success, items_data)
        """
        if not site_id or not drive_id:
            # Get default drive
            success, drives_data = await self.get_site_drives(access_token, site_id)
            if not success:
                return False, drives_data

            drives = drives_data.get("value", [])
            if not drives:
                return False, {"error": "No drives found"}

            drive_id = drives[0].get("id")  # Use first drive
            if not site_id:
                site_id = drives[0].get("parentReference", {}).get("siteId")

        if folder_path:
            # Navigate to specific folder
            folder_path_encoded = quote(folder_path, safe="")
            endpoint = f"sites/{site_id}/drives/{drive_id}/root:/{folder_path_encoded}:/children"
        else:
            # Root items
            endpoint = f"sites/{site_id}/drives/{drive_id}/root/children"

        return await self.make_graph_request(endpoint, access_token)

    async def get_file_content(
        self, access_token: str, site_id: str, drive_id: str, file_id: str
    ) -> Tuple[bool, Any]:
        """
        Get content of a specific file by ID

        Args:
            access_token: Access token for authentication
            site_id: SharePoint site ID
            drive_id: Drive ID
            file_id: File ID (not path)

        Returns:
            Tuple of (success, file_content)
        """
        # Use file ID to get content directly
        endpoint = f"sites/{site_id}/drives/{drive_id}/items/{file_id}/content"

        try:
            url = f"{self.graph_base_url}/{endpoint}"
            headers = {"Authorization": f"Bearer {access_token}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        content = await response.read()
                        return True, content
                    else:
                        error_text = await response.text()
                        return False, {
                            "error": error_text,
                            "status_code": response.status,
                        }

        except Exception as e:
            logger.error(f"Error getting file content: {e}")
            return False, {"error": str(e)}

    async def search_documents(
        self,
        access_token: str,
        query: str,
        site_id: Optional[str] = None,
        limit: int = 25,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Search SharePoint documents using Microsoft Graph Search API with KQL support.
        This is MUCH faster than traversing folders (~sub-1 second vs 20-60 seconds).

        Results are sorted by relevance score (most relevant first).

        Args:
            access_token: Access token for authentication
            query: Search query (supports KQL syntax for advanced filtering)
            site_id: Optional site ID to limit search scope
            limit: Maximum number of results (default 25, max 500)

        Returns:
            Tuple of (success, search_results)

        Example queries:
            - Simple text: "railway infrastructure"
            - KQL: "filename:*.pdf AND (railway OR infrastructure)"
            - KQL with date: "lastModifiedTime>=2024-01-01 AND railway"
        """
        try:
            url = f"{self.graph_base_url}/search/query"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            # Build search request
            search_request = {
                "requests": [
                    {
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": query},
                        "from": 0,
                        "size": min(limit, 500),  # Max 500 per request
                        "fields": [
                            "id",
                            "name",
                            "webUrl",
                            "lastModifiedDateTime",
                            "size",
                            "createdDateTime",
                            "fileSystemInfo",
                            "parentReference",
                        ],
                    }
                ]
            }

            # Region parameter only supported with application permissions, not delegated
            if not self.use_delegated_access:
                search_request["requests"][0]["region"] = "CAN"

            # If site_id is provided, add it to limit scope
            if site_id:
                search_request["requests"][0]["sharePointOneDriveOptions"] = {
                    "includeSites": [site_id]
                }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=search_request
                ) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Extract hits from search response
                        hits = []
                        if "value" in data and len(data["value"]) > 0:
                            hits_container = data["value"][0].get("hitsContainers", [])
                            if hits_container:
                                hits = hits_container[0].get("hits", [])

                        return True, {
                            "hits": hits,
                            "total": len(hits),
                            "query": query,
                        }
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Search API error: {response.status} - {error_text}"
                        )

                        # Check for permission/authorization errors
                        is_permission_error = response.status in [401, 403]

                        return False, {
                            "error": error_text,
                            "status_code": response.status,
                            "is_permission_error": is_permission_error,
                        }

        except Exception as e:
            logger.error(f"Error in search_documents: {e}")
            return False, {"error": str(e)}

    async def get_document_content_by_url(
        self,
        access_token: str,
        web_url: str,
        item_id: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Retrieve document content using the document's web URL and item ID from search results.

        Args:
            access_token: Access token for authentication
            web_url: The webUrl from search results
            item_id: The document ID from search results

        Returns:
            Tuple of (success, content_data)
        """
        try:
            # Use the Graph API to get the item metadata to extract drive ID
            metadata_url = f"{self.graph_base_url}/sites/root/drive/items/{item_id}"

            headers = {
                "Authorization": f"Bearer {access_token}",
            }

            async with aiohttp.ClientSession() as session:
                # First, get the item metadata to find the correct drive ID
                async with session.get(metadata_url, headers=headers) as response:
                    if response.status != 200:
                        # If direct access fails, try using the driveItem path
                        # Parse the webUrl to construct a Graph API path
                        error_text = await response.text()
                        logger.warning(
                            f"Direct item access failed: {response.status} - {error_text}"
                        )
                        logger.info(
                            f"Attempting alternative method using item ID directly"
                        )

                        # Try a more direct approach: use the item ID with the shares endpoint
                        # This works across both personal and shared locations
                        encoded_url = (
                            base64.urlsafe_b64encode(web_url.encode())
                            .decode()
                            .rstrip("=")
                        )
                        share_url = (
                            f"{self.graph_base_url}/shares/u!{encoded_url}/driveItem"
                        )

                        async with session.get(
                            share_url, headers=headers
                        ) as share_response:
                            if share_response.status != 200:
                                share_error = await share_response.text()
                                logger.error(
                                    f"Share access also failed: {share_response.status} - {share_error}"
                                )
                                return False, {
                                    "error": share_error,
                                    "status_code": share_response.status,
                                }

                            item_data = await share_response.json()
                    else:
                        item_data = await response.json()

                    # Now get the content using the drive ID and item ID
                    drive_id = item_data.get("parentReference", {}).get("driveId")
                    if not drive_id:
                        drive_id = item_data.get("driveId")

                    if not drive_id:
                        logger.error("Could not determine drive ID from item metadata")
                        return False, {"error": "Could not determine drive ID"}

                    content_url = f"{self.graph_base_url}/drives/{drive_id}/items/{item_id}/content"

                    async with session.get(
                        content_url, headers=headers
                    ) as content_response:
                        if content_response.status == 200:
                            content_bytes = await content_response.read()

                            # Extract text content
                            file_name = item_data.get("name", "")
                            content_text = await self._extract_text_from_bytes(
                                content_bytes, file_name
                            )

                            return True, {
                                "content": content_text,
                                "name": file_name,
                                "size": len(content_bytes),
                                "mime_type": item_data.get("file", {}).get(
                                    "mimeType", ""
                                ),
                            }
                        else:
                            error_text = await content_response.text()
                            logger.error(
                                f"Content download error: {content_response.status} - {error_text}"
                            )
                            return False, {
                                "error": error_text,
                                "status_code": content_response.status,
                            }

        except Exception as e:
            logger.error(f"Error in get_document_content_by_url: {e}")
            return False, {"error": str(e)}

    async def _extract_text_from_bytes(
        self, content_bytes: bytes, file_name: str
    ) -> str:
        """Extract text from file bytes based on file extension."""
        try:
            from io import BytesIO

            file_lower = file_name.lower()

            # PDF files
            if file_lower.endswith(".pdf"):
                try:
                    import pypdf

                    pdf_stream = BytesIO(content_bytes)
                    reader = pypdf.PdfReader(pdf_stream)
                    text_parts = []
                    for page_num, page in enumerate(reader.pages):
                        try:
                            text = page.extract_text()
                            if text.strip():
                                text_parts.append(
                                    f"--- Page {page_num + 1} ---\n{text.strip()}"
                                )
                        except Exception:
                            continue
                    return (
                        "\n\n".join(text_parts)
                        if text_parts
                        else "No text extracted from PDF"
                    )
                except Exception as e:
                    return f"Error parsing PDF: {str(e)}"

            # Word documents
            elif file_lower.endswith((".docx", ".doc")):
                try:
                    import zipfile
                    import xml.etree.ElementTree as ET

                    docx_stream = BytesIO(content_bytes)
                    with zipfile.ZipFile(docx_stream, "r") as zip_file:
                        doc_xml = zip_file.read("word/document.xml")
                        root = ET.fromstring(doc_xml)
                        namespace = {
                            "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                        }
                        paragraphs = root.findall(".//w:t", namespace)
                        text_parts = [p.text for p in paragraphs if p.text]
                        return (
                            "\n".join(text_parts)
                            if text_parts
                            else "No text extracted from Word document"
                        )
                except Exception as e:
                    return f"Error parsing Word document: {str(e)}"

            # PowerPoint files
            elif file_lower.endswith((".pptx", ".ppt")):
                try:
                    import zipfile
                    import xml.etree.ElementTree as ET

                    pptx_stream = BytesIO(content_bytes)
                    with zipfile.ZipFile(pptx_stream, "r") as zip_file:
                        text_parts = []
                        slide_files = [
                            f
                            for f in zip_file.namelist()
                            if f.startswith("ppt/slides/slide") and f.endswith(".xml")
                        ]
                        for slide_file in sorted(slide_files):
                            slide_xml = zip_file.read(slide_file)
                            root = ET.fromstring(slide_xml)
                            namespace = {
                                "a": "http://schemas.openxmlformats.org/drawingml/2006/main"
                            }
                            texts = root.findall(".//a:t", namespace)
                            slide_text = " ".join([t.text for t in texts if t.text])
                            if slide_text.strip():
                                text_parts.append(slide_text.strip())
                        return (
                            "\n\n".join(text_parts)
                            if text_parts
                            else "No text extracted from PowerPoint"
                        )
                except Exception as e:
                    return f"Error parsing PowerPoint: {str(e)}"

            # Text files
            elif file_lower.endswith((".txt", ".md", ".csv")):
                try:
                    return content_bytes.decode("utf-8")
                except Exception:
                    try:
                        return content_bytes.decode("latin-1")
                    except Exception as e:
                        return f"Error decoding text file: {str(e)}"

            else:
                return f"Unsupported file type: {file_name}"

        except Exception as e:
            return f"Error extracting text from {file_name}: {str(e)}"

    async def test_connection(self, user_token: Optional[str] = None) -> Dict[str, Any]:
        """
        Test the SharePoint connection and permissions

        Args:
            user_token: Optional user token for delegated access

        Returns:
            Dictionary with test results
        """
        result = {
            "oauth_config": "configured",
            "delegated_access": self.use_delegated_access,
            "token_acquisition": "failed",
            "site_access": "failed",
            "drives_access": "failed",
        }

        try:
            # Step 1: Get access token
            access_token = await self.get_access_token(user_token)
            if access_token:
                result["token_acquisition"] = "success"
            else:
                return result

            # Step 2: Test site access
            success, site_info = await self.get_site_info(access_token)
            if success:
                result["site_access"] = "success"
                result["site_info"] = site_info
            else:
                result["site_error"] = site_info
                return result

            # Step 3: Test drives access
            success, drives_data = await self.get_site_drives(access_token)
            if success:
                result["drives_access"] = "success"
                result["drives_count"] = len(drives_data.get("value", []))
                result["drives"] = [d.get("name") for d in drives_data.get("value", [])]
            else:
                result["drives_error"] = drives_data

            return result

        except Exception as e:
            result["test_error"] = str(e)
            return result


# Global OAuth client instance
_oauth_client = None


def get_oauth_client() -> SharePointOAuthClient:
    """Get or create the global OAuth client instance"""
    global _oauth_client
    if _oauth_client is None:
        _oauth_client = SharePointOAuthClient()
    return _oauth_client
