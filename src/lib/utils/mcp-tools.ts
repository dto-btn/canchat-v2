// Helper function to extract tool name from MCP display name
const extractToolName = (displayName: string): string => {
	// If it starts with "MCP: ", remove that prefix
	if (displayName.startsWith('MCP: ')) {
		return displayName.substring(5);
	}
	return displayName;
};

// Helper function to get localized MCP tool descriptions
const getMCPToolDescription = (toolName: string, i18n: any): string => {
	// Extract the actual function name
	const actualToolName = extractToolName(toolName);

	// Static descriptions for well-known tools
	const toolDescriptions: Record<string, string> = {
		get_current_time: 'Get current date and time in any timezone',
		get_top_headlines: 'Get latest news headlines from around the world'
	};

	if (actualToolName in toolDescriptions) {
		return i18n.t(toolDescriptions[actualToolName]);
	}

	// Dynamic pattern for department SharePoint tools: {dept}_{action}
	// e.g. mpo_search_documents_fast, fin_list_folder_contents
	const spPattern =
		/^([a-z0-9]+)_(search_documents_fast|list_folder_contents|get_document_by_id|analyze_all_documents_for_content|get_sharepoint_document_content|get_all_documents_comprehensive|check_sharepoint_permissions)$/;
	const spMatch = actualToolName.match(spPattern);
	if (spMatch) {
		const dept = spMatch[1].toUpperCase();
		const action = spMatch[2];
		if (action === 'search_documents_fast') {
			return i18n.t(`Fast search {{dept}} SharePoint documents`, { dept });
		}
		if (action === 'list_folder_contents') {
			return i18n.t(`List files in {{dept}} SharePoint folders`, { dept });
		}
		if (action === 'get_document_by_id') {
			return i18n.t(`Retrieve {{dept}} SharePoint document by ID from search results`, { dept });
		}
		return i18n.t(`Search and retrieve {{dept}} SharePoint documents`, { dept });
	}

	// Fallback to formatted tool name (short and clean)
	const fallback = actualToolName.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
	return fallback;
};

// Helper function to get localized MCP tool name
export const getMCPToolName = (toolName: string, i18n: any): string => {
	// Extract the actual function name
	const actualToolName = extractToolName(toolName);

	// Static names for well-known non-SharePoint tools
	const toolNames: Record<string, string> = {
		get_current_time: 'MCP: Current Time',
		get_top_headlines: 'MCP: News Headlines'
	};

	if (actualToolName in toolNames) {
		return i18n.t(toolNames[actualToolName]);
	}

	// Dynamic pattern for department SharePoint tools: {dept}_{action}
	const spPattern =
		/^([a-z0-9]+)_(search_documents_fast|list_folder_contents|get_document_by_id|analyze_all_documents_for_content|get_sharepoint_document_content|get_all_documents_comprehensive|check_sharepoint_permissions)$/;
	const spMatch = actualToolName.match(spPattern);
	if (spMatch) {
		const dept = spMatch[1].toUpperCase();
		const action = spMatch[2];
		if (action === 'get_document_by_id') {
			return i18n.t(`MCP: {{dept}} SharePoint (By ID)`, { dept });
		}
		return i18n.t(`MCP: {{dept}} SharePoint`, { dept });
	}

	// Fallback to formatted tool name with MCP prefix
	const fallback =
		'MCP: ' + actualToolName.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
	return fallback;
};

// Helper function to get display name for any tool
export const getToolDisplayName = (tool: any, i18n: any): string => {
	if (tool?.meta?.manifest?.is_mcp_tool) {
		// Use originalName (the actual function name) for translation lookup
		const toolNameForTranslation = tool.meta?.manifest?.original_name || tool.name;
		return getMCPToolName(toolNameForTranslation, i18n);
	}
	return tool.name;
};

// Helper function to get tooltip content for tools
export const getToolTooltipContent = (tool: any, i18n: any): string => {
	// Check both isMcp property and meta.manifest.is_mcp_tool
	const isMcpTool = tool?.isMcp || tool?.meta?.manifest?.is_mcp_tool || false;

	if (isMcpTool) {
		// Use originalName (the actual function name) for translation lookup
		const toolNameForTranslation = tool.meta?.manifest?.original_name || tool.name;
		const localizedDescription = getMCPToolDescription(toolNameForTranslation, i18n);
		return localizedDescription;
	}

	// For non-MCP tools, check if description is JSON
	const description = tool.originalDescription || tool.meta?.description || '';
	if (description && description.trim().startsWith('{')) {
		try {
			const parsed = JSON.parse(description);
			// Return the description field from parsed JSON if it exists
			return parsed.description || '';
		} catch (e) {
			// If parsing fails, return as-is
			return description;
		}
	}
	return description;
};
